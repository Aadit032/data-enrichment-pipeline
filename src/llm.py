"""OpenRouter LLM client with retries and robust JSON extraction."""

from __future__ import annotations

import json
import re
import time
from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from .config import Settings
from .logging_util import get_logger

logger = get_logger("llm")

T = TypeVar("T", bound=BaseModel)

SYSTEM_ENTITY_RESOLUTION = """\
You are an exact-entity resolution engine for a company data-enrichment pipeline.
Given a target company and evidence from a candidate website, decide whether the \
website belongs to the EXACT same company — not merely a company with a similar name.

Rules:
- A match requires the candidate to correspond to the exact legal entity: e.g. the \
site displays the same company name, or there is an unambiguous, verifiable link \
(name + matching city/address/pincode) to the target.
- Be suspicious of namesake companies in other cities/countries, subsidiaries, \
franchisees, aggregator/portal pages, and unrelated firms that happen to share a word.
- High confidence (0.8-1.0): explicit exact name plus corroborating location/address \
evidence, or an exact-name match that is unmistakable.
- Medium (0.5-0.7): plausible but only partial corroboration, or strong name match \
without location confirmation.
- Low (<0.5): namesake, competitor, portal, or unrelated page.
- When in doubt, is_match must be false. Precision over recall — it is better to \
leave a company unresolved than to attach the wrong website.

Respond with ONLY a JSON object with these keys:
{"is_match": <bool>, "confidence": <float 0-1>, "explanation": <str>, "evidence": <list[str]>, "site_type": <str>}
where "evidence" is a short list of concrete strings from the candidate page \
(e.g. exact company name, address, pincode) that establish the match (["none"] if none), \
and "site_type" is one of:
- "official": ONLY if the page/domain provides evidence that it is operated by the company itself \
(e.g. it presents itself as the company's own site, carries its official branding/contact details, \
or the domain is clearly owned by the company).
- "third_party": anything else that merely describes or references the company — directories, \
aggregators, portals, news, review or data sites (e.g. tracxn.com, sensibook.com, addressadda.com, \
qorpiq.com, tofler.in, thecompanycheck.com), even if they display the exact company name, address \
and registration details. Such a page is a match for the entity but NOT the official website.
- "ambiguous": it is not clear from the evidence whether the site is operated by the company itself.\
"""

SYSTEM_EXTRACTION = """\
You are a business-intelligence extraction engine.
Given a target company and evidence from its VERIFIED official website, extract:

- website: the official website URL of the company (full URL from the evidence).
- business: 1-3 sentence plain-English description of what the company does \
(products/services), based only on the evidence.
- customers: 1-2 sentence description of who its customers are (industries, \
segments, client types). FIRST carefully scan the evidence text for any explicit \
customer information — phrases like "serves", "clients", "customers", "targets", \
"for", "used by", "we work with", "supports", or named industries/segments. If \
the evidence mentions customers, describe exactly what it says and do NOT guess. \
ONLY when the evidence contains no customer information whatsoever, make a very \
calculated guess inferred from the company's business/industry (who would \
typically buy its products/services) and phrase it clearly as an inference \
(e.g. "Likely served industries include ..."). Use null only if even the \
industry cannot be inferred.

If the evidence does not support the website or business fields, set them to null. \
Do not fabricate specific facts. For customers, a reasoned inference is expected.
Respond with ONLY a JSON object:
{"website": <str|null>, "business": <str|null>, "customers": <str|null>}\
"""


class LLMClient:
    def __init__(self, settings: Settings) -> None:
        if not settings.openrouter_api_key:
            raise ValueError("OPENROUTER_API_KEY is not set")
        self.settings = settings
        self.model = settings.openrouter_model
        self._client = OpenAI(
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key,
            timeout=settings.llm_timeout,
        )

    def complete_json(
        self,
        system: str,
        user: str,
        model_type: type[T],
        retries: int | None = None,
    ) -> T:
        attempts = retries or self.settings.max_retries
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            if self.settings.llm_call_delay > 0:
                time.sleep(self.settings.llm_call_delay)
            try:
                response = self._client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                )
                content = response.choices[0].message.content or ""
                data = _extract_json(content)
                return model_type.model_validate(data)
            except (ValidationError, ValueError, KeyError, TypeError) as exc:
                last_error = exc
                logger.warning(
                    "LLM JSON validation failed (attempt %d/%d, model=%s): %s",
                    attempt,
                    attempts,
                    self.model,
                    exc,
                )
                if attempt < attempts:
                    time.sleep(min(2**attempt, 8))
            except Exception as exc:  # noqa: BLE001 - network/API errors
                last_error = exc
                logger.warning(
                    "LLM API error (attempt %d/%d): %s",
                    attempt,
                    attempts,
                    exc,
                )
                if attempt < attempts:
                    time.sleep(min(2**attempt, 8))
        raise RuntimeError(f"LLM request failed after {attempts} attempts: {last_error}")


def _extract_json(text: str) -> dict:
    """Extract the first JSON object from LLM output (tolerates fences/prose)."""
    cleaned = text.strip()
    cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    start = cleaned.find("{")
    if start == -1:
        raise ValueError(f"no JSON object found in LLM output: {text[:200]!r}")

    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(cleaned)):
        char = cleaned[i]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start : i + 1])
    raise ValueError(f"unbalanced JSON in LLM output: {text[:200]!r}")
