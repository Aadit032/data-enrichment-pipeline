"""Fetcher cache tests (no network)."""

from __future__ import annotations

from src.cache import JsonCache
from src.config import Settings
from src.fetch import Fetcher


class _Client:
    """Fake httpx-like client that counts requests."""

    def __init__(self, html: str) -> None:
        self.html = html
        self.calls = 0

    def get(self, url: str):
        self.calls += 1
        return _Response(self.html)


class _Response:
    def __init__(self, text: str) -> None:
        self.text = text
        self.status_code = 200

    def raise_for_status(self) -> None:
        return None


def test_fetch_html_is_cached(tmp_path) -> None:
    settings = Settings(cache_dir=tmp_path / "cache", use_playwright=False)
    cache = JsonCache(settings.cache_dir)

    fake = _Client("<html><title>Test</title><p>hello world</p></html>")
    fetcher = Fetcher(settings, cache)
    fetcher._client = fake  # type: ignore[assignment]

    html1, via1 = fetcher.fetch_html("https://example.com/")
    html2, via2 = fetcher.fetch_html("https://example.com/")

    assert via1 == "http"
    assert html1 == html2 == fake.html
    assert fake.calls == 1, "second fetch should hit the cache, not the network"
    assert via2 == "http"


def test_fetch_cache_separates_urls(tmp_path) -> None:
    settings = Settings(cache_dir=tmp_path / "cache2", use_playwright=False)
    cache = JsonCache(settings.cache_dir)
    fake = _Client("<p>a</p>")
    fetcher = Fetcher(settings, cache)
    fetcher._client = fake  # type: ignore[assignment]

    fetcher.fetch_html("https://example.com/a")
    fetcher.fetch_html("https://example.com/b")
    assert fake.calls == 2