"""Unit tests for pure pipeline logic."""

from __future__ import annotations

from src.models import Company, ExaSearchResult
from src.queries import (
    base_name,
    build_queries,
    dedupe_candidates,
    locality_from_address,
    normalize_domain,
)


def test_base_name_strips_legal_suffixes() -> None:
    assert base_name("SALDANHA REAL ESTATE PRIVATE LIMITED") == "SALDANHA REAL ESTATE"
    assert base_name("ABC PVT LTD") == "ABC"
    assert base_name("XYZ LLP") == "XYZ"
    assert base_name("NATIONAL SECURITIES DEPOSITORY LIMITED") == "NATIONAL SECURITIES DEPOSITORY"


def test_locality_from_address() -> None:
    company = Company(
        name="X",
        city="Mumbai",
        address="FLAT NO.1, PLOT NO.29, HILL ROAD, OPP. BATA SHOE SHOP, BANDRA (WEST), MUMBAI, 400050-India",
    )
    assert locality_from_address(company.address, company.city) == "BANDRA"


def test_build_queries_include_city_and_website() -> None:
    company = Company(name="ART ESTATES PRIVATE LIMITED", city="Pune", address="AGARKAR BHAVAN LBS ROAD PUNE 411030")
    queries = build_queries(company)
    assert len(queries) == 3
    assert any("ART ESTATES" in q and "Pune" in q for q in queries)
    assert any("official website" in q for q in queries)


def test_dedupe_candidates_keeps_best_per_domain() -> None:
    results = [
        ExaSearchResult(url="https://example.com/page1", domain="example.com", text="short", score=0.1),
        ExaSearchResult(url="https://example.com/page2", domain="example.com", text="much longer text", score=0.9),
        ExaSearchResult(url="https://other.org/", domain="other.org", text="x", score=0.5),
    ]
    deduped = dedupe_candidates(results)
    assert len(deduped) == 2
    assert deduped[0].url == "https://example.com/page2"


def test_normalize_domain() -> None:
    assert normalize_domain("Www.Example.COM") == "example.com"
