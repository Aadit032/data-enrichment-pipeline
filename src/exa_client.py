"""Exa web-search client (REST API via httpx)."""

from __future__ import annotations

import httpx

from .logging_util import get_logger
from .models import ExaSearchResult
from .queries import netloc, normalize_domain

logger = get_logger("exa")


class ExaClient:
    def __init__(self, api_key: str, timeout: float, max_text_chars: int = 4000) -> None:
        self.api_key = api_key
        self.timeout = timeout
        self.max_text_chars = max_text_chars
        self._client = httpx.Client(timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def search(self, query: str, num_results: int = 5) -> list[ExaSearchResult]:
        """Run a search and return results, each with optional text content."""
        payload = {
            "query": query,
            "numResults": num_results,
            "contents": {"text": {"maxCharacters": self.max_text_chars}},
        }
        response = self._client.post(
            "https://api.exa.ai/search",
            json=payload,
            headers={"x-api-key": self.api_key, "Content-Type": "application/json"},
        )
        response.raise_for_status()
        data = response.json()

        results: list[ExaSearchResult] = []
        for item in data.get("results", []):
            url = item.get("url")
            if not url:
                continue
            results.append(
                ExaSearchResult(
                    url=url,
                    domain=normalize_domain(netloc(url)),
                    title=item.get("title"),
                    text=(item.get("text") or "") or None,
                    score=item.get("score"),
                )
            )
        logger.debug("exa search %r -> %d results", query, len(results))
        return results
