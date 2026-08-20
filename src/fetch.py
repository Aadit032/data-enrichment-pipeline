"""Website retrieval and evidence extraction.

Retrieval strategy per URL:
1. Exa-provided content is used directly when it is sufficiently long.
2. Otherwise the page is fetched over plain HTTP.
3. If the page looks JS-rendered or the extracted text is too thin, a
   Playwright (headless Chromium) render is attempted as a fallback.

Evidence includes title, meta description, JSON-LD, address/pincode signals
and cleaned page text.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any

import httpx
from bs4 import BeautifulSoup

from .cache import JsonCache, payload_key
from .config import Settings
from .logging_util import get_logger
from .models import ExaSearchResult, PageEvidence
from .queries import build_url, netloc, normalize_domain

logger = get_logger("fetch")

_PINCODE_RE = re.compile(r"\b[1-9][0-9]{5}\b")
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

_EXTRA_PAGES = ["/about", "/contact", "/about-us", "/about/", "/contact-us", "/contact/", "/"]


class Fetcher:
    """Fetches and parses web pages.

    Raw HTML is cached under the ``web`` namespace so re-runs avoid re-fetching.
    """

    def __init__(self, settings: Settings, cache: JsonCache | None = None) -> None:
        self.settings = settings
        self.cache = cache
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                follow_redirects=True,
                timeout=self.settings.request_timeout,
                headers={"User-Agent": _USER_AGENT, "Accept-Language": "en"},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
        close_playwright()

    def fetch_html(self, url: str) -> tuple[str, str]:
        """Return ``(html, via)`` where via is 'http' or 'playwright', cached by URL."""

        def compute() -> dict[str, str]:
            try:
                response = self.client.get(url)
                response.raise_for_status()
                return {"html": response.text, "via": "http"}
            except Exception as exc:
                logger.debug("http fetch failed for %s: %s", url, exc)
                if not self.settings.use_playwright:
                    raise
                html = render_playwright(url, timeout=self.settings.request_timeout)
                return {"html": html, "via": "playwright"}

        if self.cache is not None:
            cached = self.cache.get_or_compute("web", {"url": url}, compute)
            return cached["html"], cached["via"]
        result = compute()
        return result["html"], result["via"]

    def store_html(self, url: str, html: str, via: str) -> None:
        """Overwrite the cached HTML for ``url`` (used after a Playwright upgrade)."""
        if self.cache is not None:
            self.cache.set("web", payload_key({"url": url}), {"html": html, "via": via})

    def evidence_for(self, url: str, city: str = "") -> PageEvidence:
        """Fetch ``url`` and extract evidence, upgrading to Playwright if thin."""
        html, via = self.fetch_html(url)
        evidence = extract_evidence(url, html, via, city, self.settings.max_page_text_chars)

        if via == "http" and self._is_thin(evidence) and self.settings.use_playwright:
            try:
                rendered_html = render_playwright(url, timeout=self.settings.request_timeout)
                rendered = extract_evidence(
                    url,
                    rendered_html,
                    "playwright",
                    city,
                    self.settings.max_page_text_chars,
                )
                if len(rendered.text) > len(evidence.text):
                    logger.debug(
                        "playwright upgrade for %s (%d -> %d chars)",
                        url,
                        len(evidence.text),
                        len(rendered.text),
                    )
                    evidence = rendered
                    self.store_html(url, rendered_html, "playwright")
            except Exception as exc:  # noqa: BLE001
                logger.warning("playwright fallback failed for %s: %s", url, exc)

        return evidence

    def _is_thin(self, evidence: PageEvidence) -> bool:
        return len(evidence.text.strip()) < self.settings.min_good_text_chars

    def evidence_for_domain(self, candidate: ExaSearchResult, city: str) -> list[PageEvidence]:
        """Gather evidence for a candidate domain: result page, homepage, then extras."""
        domain = normalize_domain(netloc(candidate.url))
        root = build_url(domain)
        urls = [candidate.url]
        if urlsplit_path(candidate.url) != "/":
            urls.append(root)

        pages: list[PageEvidence] = []
        for url in urls:
            try:
                pages.append(self.evidence_for(url, city))
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to fetch %s: %s", url, exc)
            if any(len(p.text.strip()) >= self.settings.min_good_text_chars for p in pages):
                break

        if not any(len(p.text.strip()) >= self.settings.min_good_text_chars for p in pages):
            for path in _EXTRA_PAGES:
                url = build_url(domain, path)
                if url in urls:
                    continue
                try:
                    page = self.evidence_for(url, city)
                    pages.append(page)
                    if len(page.text.strip()) >= self.settings.min_good_text_chars:
                        break
                except Exception as exc:  # noqa: BLE001
                    logger.debug("failed to fetch %s: %s", url, exc)

        return dedupe_pages(pages)


def urlsplit_path(url: str) -> str:
    path = urllib.parse.urlsplit(url).path or "/"
    return path.rstrip("/") or "/"


def dedupe_pages(pages: list[PageEvidence]) -> list[PageEvidence]:
    seen: set[str] = set()
    unique: list[PageEvidence] = []
    for page in pages:
        if page.url not in seen:
            seen.add(page.url)
            unique.append(page)
    return unique


def extract_evidence(
    url: str,
    html: str,
    via: str,
    city: str,
    max_text_chars: int,
) -> PageEvidence:
    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.get_text(strip=True) if soup.title else None
    if not title:
        title = _meta(soup, "og:title") or _meta(soup, "twitter:title")

    description = _meta(soup, "description") or _meta(soup, "og:description")

    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True)).strip()
    truncated = len(text) > max_text_chars
    text = text[:max_text_chars]

    return PageEvidence(
        url=url,
        domain=normalize_domain(netloc(url)),
        title=title,
        description=description,
        json_ld=extract_json_ld(soup),
        address_lines=address_snippets(text, city),
        pincode=_first_pincode(text),
        city_mentions=city_mentions(text, city),
        text=text,
        fetched_via=via,
        truncated=truncated,
    )


def _meta(soup: BeautifulSoup, key: str) -> str | None:
    tag = soup.find("meta", attrs={"name": key, "content": True}) or soup.find(
        "meta", attrs={"property": key, "content": True}
    )
    if tag and tag.get("content"):
        return tag["content"].strip()
    return None


def _is_json_ld(value: object) -> bool:
    return isinstance(value, str) and "application/ld+json" in value.lower()


def extract_json_ld(soup: BeautifulSoup) -> list[str]:
    out: list[str] = []
    for script in soup.find_all("script", type=_is_json_ld):
        try:
            data = json.loads(script.string or script.get_text())
        except (json.JSONDecodeError, TypeError):
            continue
        out.extend(summarize_ld(data))
    return out


def summarize_ld(data: object) -> list[str]:
    """Flatten @graph structures into a list of small JSON summaries."""
    items = []
    if isinstance(data, dict):
        graph = data.get("@graph")
        items = graph if isinstance(graph, list) else [data]
    elif isinstance(data, list):
        items = data
    elif isinstance(data, str):
        try:
            items = [json.loads(data)]
        except json.JSONDecodeError:
            return []

    summaries: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        summary: dict[str, object] = {}
        for key in ("name", "url", "description", "legalName"):
            if item.get(key):
                summary[key] = item[key]
        address = item.get("address") or item.get("location")
        if isinstance(address, dict):
            summary["address"] = {
                k: address[k]
                for k in (
                    "streetAddress",
                    "addressLocality",
                    "addressRegion",
                    "postalCode",
                    "addressCountry",
                )
                if address.get(k)
            }
        if summary:
            summaries.append(json.dumps(summary, ensure_ascii=False))
    return summaries


def address_snippets(text: str, city: str, limit: int = 5) -> list[str]:
    """Return short text windows containing pincodes or the target city."""
    snippets: list[str] = []

    def add(window: str) -> None:
        window = window.strip()
        if window and window not in snippets and len(snippets) < limit:
            snippets.append(window)

    for match in _PINCODE_RE.finditer(text):
        add(text[max(0, match.start() - 120) : match.end() + 80])

    if city:
        pattern = re.compile(rf"\b{re.escape(city)}\b", re.IGNORECASE)
        for match in pattern.finditer(text):
            add(text[max(0, match.start() - 100) : match.end() + 80])

    return snippets


def _first_pincode(text: str) -> str | None:
    match = _PINCODE_RE.search(text)
    return match.group(0) if match else None


def city_mentions(text: str, city: str) -> list[str]:
    if not city:
        return []
    pattern = re.compile(rf"\b{re.escape(city)}\b", re.IGNORECASE)
    return [match.group(0) for match in pattern.finditer(text)]


# ---------------------------------------------------------------------------
# Playwright rendering (lazy singleton)
# ---------------------------------------------------------------------------

_playwright: Any = None
_browser: Any = None


def render_playwright(url: str, timeout: float) -> str:
    """Render a page in headless Chromium and return its post-JS HTML."""
    global _playwright, _browser
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is not installed; run 'uv sync' and 'uv run playwright install chromium'"
        ) from exc

    if _playwright is None:
        _playwright = sync_playwright().start()
        _browser = _playwright.chromium.launch(headless=True)

    page = _browser.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
        page.wait_for_timeout(2500)
        return page.content()
    finally:
        page.close()


def close_playwright() -> None:
    global _playwright, _browser
    if _playwright is not None:
        try:
            _playwright.stop()
        finally:
            _playwright = None
            _browser = None
