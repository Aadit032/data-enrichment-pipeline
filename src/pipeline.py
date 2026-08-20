"""End-to-end enrichment pipeline for a single company and the batch runner.

Flow per company:
1. Generate 3 query variations (name + city/address signals).
2. Run each query on Exa (cached); aggregate and deduplicate domains.
3. For each candidate domain, gather page evidence (Exa content / HTTP / Playwright).
4. LLM entity resolution per domain (cached) against the target company.
5. Pick the highest-confidence valid match above the threshold.
6. LLM extraction of website / business / customers (cached).
"""

from __future__ import annotations

import json

from .cache import JsonCache
from .config import Settings
from .exa_client import ExaClient
from .fetch import Fetcher
from .llm import SYSTEM_ENTITY_RESOLUTION, SYSTEM_EXTRACTION, LLMClient
from .logging_util import get_logger
from .models import (
    Company,
    CompanyResult,
    DomainEvidence,
    EntityResolution,
    ExaSearchResult,
    ExtractionResult,
    PageEvidence,
)
from .queries import build_queries, dedupe_candidates

logger = get_logger("pipeline")


class Pipeline:
    def __init__(self, settings: Settings, cache: JsonCache) -> None:
        self.settings = settings
        self.cache = cache
        self.exa = ExaClient(
            api_key=settings.exa_api_key,
            timeout=settings.request_timeout,
        )
        self.fetcher = Fetcher(settings, cache)
        self.llm = LLMClient(settings)

    def close(self) -> None:
        self.exa.close()
        self.fetcher.close()

    # ------------------------------------------------------------------
    # Step 1-2: candidate discovery via Exa
    # ------------------------------------------------------------------
    def discover_candidates(self, company: Company) -> list[ExaSearchResult]:
        queries = build_queries(company)
        results: list[ExaSearchResult] = []
        for query in queries:
            payload = {"query": query, "num_results": self.settings.exa_num_results}
            try:
                found = self.cache.get_or_compute(
                    "exa_search",
                    payload,
                    lambda q=query: self.exa.search(q, self.settings.exa_num_results),
                )
                results.extend(
                    ExaSearchResult.model_validate(item) if isinstance(item, dict) else item
                    for item in found
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("exa search failed for %r: %s", query, exc)

        candidates = dedupe_candidates(results)
        return candidates[: self.settings.max_candidates]

    # ------------------------------------------------------------------
    # Step 3: website retrieval + evidence
    # ------------------------------------------------------------------
    def gather_domain_evidence(self, candidate: ExaSearchResult, company: Company) -> DomainEvidence:
        domain = candidate.domain or ""
        pages = self.fetcher.evidence_for_domain(candidate, company.city)
        return DomainEvidence(domain=domain, pages=pages)

    # ------------------------------------------------------------------
    # Step 4: entity resolution
    # ------------------------------------------------------------------
    def resolve_domain(
        self,
        company: Company,
        domain_evidence: DomainEvidence,
    ) -> EntityResolution:
        evidence_payload = {
            "company": {
                "name": company.name,
                "city": company.city,
                "address": company.address,
            },
            "domain": domain_evidence.domain,
            "evidence": [self._prompt_evidence(p) for p in domain_evidence.pages],
        }
        return self.cache.get_or_compute(
            "entity_resolution",
            evidence_payload,
            lambda: self.llm.complete_json(
                SYSTEM_ENTITY_RESOLUTION,
                _entity_resolution_prompt(evidence_payload, self.settings.prompt_text_chars),
                EntityResolution,
            ),
            model=EntityResolution,
        )

    # ------------------------------------------------------------------
    # Step 6: final extraction
    # ------------------------------------------------------------------
    def extract(
        self,
        company: Company,
        domain_evidence: DomainEvidence,
        resolution: EntityResolution,
    ) -> ExtractionResult:
        payload = {
            "company": {
                "name": company.name,
                "city": company.city,
                "address": company.address,
            },
            "domain": domain_evidence.domain,
            "evidence": [self._prompt_evidence(p) for p in domain_evidence.pages],
            "resolution_explanation": resolution.explanation,
        }
        return self.cache.get_or_compute(
            "extraction",
            payload,
            lambda: self.llm.complete_json(
                SYSTEM_EXTRACTION,
                _extraction_prompt(payload, self.settings.prompt_text_chars),
                ExtractionResult,
            ),
            model=ExtractionResult,
        )

    @staticmethod
    def _prompt_evidence(page: PageEvidence) -> dict:
        return {
            "url": page.url,
            "title": page.title,
            "description": page.description,
            "json_ld": page.json_ld,
            "pincode": page.pincode,
            "address_snippets": page.address_lines,
            "city_mentions": page.city_mentions,
            "fetched_via": page.fetched_via,
            "text": page.text,
        }

    # ------------------------------------------------------------------
    # Whole-company flow
    # ------------------------------------------------------------------
    def enrich_company(self, company: Company) -> CompanyResult:
        result = CompanyResult(company=company)
        try:
            candidates = self.discover_candidates(company)
            if not candidates:
                logger.info("no candidates found for %s", company.name)
                return result

            domains: list[DomainEvidence] = []
            for candidate in candidates:
                domain_evidence = self.gather_domain_evidence(candidate, company)
                if domain_evidence.pages:
                    domains.append(domain_evidence)
            result.candidates = domains
            if not domains:
                logger.info("no evidence fetched for %s", company.name)
                return result

            resolutions: list[tuple[DomainEvidence, EntityResolution]] = []
            for domain_evidence in domains:
                try:
                    resolution = self.resolve_domain(company, domain_evidence)
                    resolutions.append((domain_evidence, resolution))
                    logger.debug(
                        "%s vs %s -> match=%s confidence=%.2f",
                        company.name,
                        domain_evidence.domain,
                        resolution.is_match,
                        resolution.confidence,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("entity resolution failed for %s: %s", domain_evidence.domain, exc)

            best = _select_best_match(resolutions, self.settings.entity_match_threshold)
            if best is None:
                logger.info(
                    "no confident match for %s (%d candidates, threshold=%.2f)",
                    company.name,
                    len(resolutions),
                    self.settings.entity_match_threshold,
                )
                return result

            domain_evidence, resolution = best
            result.resolution = resolution
            try:
                result.extraction = self.extract(company, domain_evidence, resolution)
                result.status = "matched"
            except Exception as exc:  # noqa: BLE001
                result.error = str(exc)
                result.status = "error"
                logger.error("extraction failed for %s: %s", company.name, exc)

        except Exception as exc:
            result.error = str(exc)
            result.status = "error"
            logger.exception("company %s failed", company.name)
        return result


def _select_best_match(
    resolutions: list[tuple[DomainEvidence, EntityResolution]],
    threshold: float,
) -> tuple[DomainEvidence, EntityResolution] | None:
    """Pick the highest-confidence valid match above ``threshold``."""
    best: tuple[DomainEvidence, EntityResolution] | None = None
    for domain_evidence, resolution in resolutions:
        if resolution.is_match and resolution.confidence >= threshold and (
            best is None or resolution.confidence > best[1].confidence
        ):
            best = (domain_evidence, resolution)
    return best


def run_batch(
    settings: Settings,
    cache: JsonCache,
    companies: list[Company],
    limit: int | None = None,
) -> list[CompanyResult]:
    """Enrich companies sequentially, preserving input order."""
    pipeline = Pipeline(settings, cache)
    companies = companies if limit is None else companies[:limit]
    results: list[CompanyResult] = []
    try:
        for index, company in enumerate(companies, start=1):
            results.append(pipeline.enrich_company(company))
            _log_progress(index, len(companies))
    finally:
        pipeline.close()

    results.sort(key=lambda r: r.company.row_index)
    return results


def _log_progress(done: int, total: int) -> None:
    logger.info("progress %d/%d", done, total)


def _entity_resolution_prompt(payload: dict, prompt_text_chars: int) -> str:
    evidence = _truncated_evidence(payload["evidence"], prompt_text_chars)
    return f"""TARGET COMPANY
- Legal name: {payload['company']['name']}
- City: {payload['company']['city'] or 'n/a'}
- Address: {payload['company']['address'] or 'n/a'}

CANDIDATE WEBSITE EVIDENCE
{json.dumps(evidence, indent=2, ensure_ascii=False)}

Return ONLY JSON: {{"is_match": <bool>, "confidence": <float 0-1>, "explanation": <str>, "evidence": <str>}}"""


def _extraction_prompt(payload: dict, prompt_text_chars: int) -> str:
    evidence = _truncated_evidence(payload["evidence"], prompt_text_chars)
    return f"""TARGET COMPANY
- Legal name: {payload['company']['name']}
- City: {payload['company']['city'] or 'n/a'}
- Address: {payload['company']['address'] or 'n/a'}

VERIFIED OFFICIAL WEBSITE: {payload['domain']}
(Entity-resolution note: {payload['resolution_explanation']})

WEBSITE EVIDENCE
{json.dumps(evidence, indent=2, ensure_ascii=False)}

Return ONLY JSON: {{"website": <str|null>, "business": <str|null>, "customers": <str|null>}}"""


def _truncated_evidence(evidence: list[dict], prompt_text_chars: int) -> list[dict]:
    out = []
    for item in evidence:
        item = dict(item)
        text = item.pop("text", "")
        if text:
            item["text"] = text[:prompt_text_chars]
        out.append(item)
    return out
