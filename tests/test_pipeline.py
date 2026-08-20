"""Pipeline orchestration tests using stubbed Exa/LLM/fetch clients."""

from __future__ import annotations

from pathlib import Path

from src.cache import JsonCache
from src.config import Settings
from src.models import (
    Company,
    DomainEvidence,
    EntityResolution,
    ExaSearchResult,
    ExtractionResult,
    PageEvidence,
)
from src.pipeline import Pipeline, _collect_reference_urls, _select_best_match, _site_status


def make_company() -> Company:
    return Company(
        row_index=0,
        name="ACME PRIVATE LIMITED",
        city="Mumbai",
        address="1 EXAMPLE ROAD, MUMBAI, 400050",
    )


def make_evidence(domain: str) -> DomainEvidence:
    page = PageEvidence(
        url=f"https://{domain}/",
        domain=domain,
        title=f"{domain} - Home",
        text="ACME PRIVATE LIMITED providing services in Mumbai, Maharashtra. Address: 1 Example Road, Mumbai 400050.",
        pincode="400050",
    )
    return DomainEvidence(domain=domain, pages=[page])


class StubbedPipeline(Pipeline):
    def __init__(self, settings: Settings, cache: JsonCache) -> None:
        self.settings = settings
        self.cache = cache

    def discover_candidates(self, company: Company) -> list[ExaSearchResult]:
        return [
            ExaSearchResult(url="https://acme-real.com/", domain="acme-real.com", text="", score=0.9),
            ExaSearchResult(url="https://acme-partner.com/", domain="acme-partner.com", text="", score=0.7),
            ExaSearchResult(url="https://acme-fake.com/", domain="acme-fake.com", text="", score=0.8),
        ]

    def gather_domain_evidence(self, candidate: ExaSearchResult, company: Company) -> DomainEvidence:
        return make_evidence(candidate.domain)

    def resolve_domain(self, company: Company, domain_evidence: DomainEvidence) -> EntityResolution:
        if domain_evidence.domain == "acme-real.com":
            return EntityResolution(is_match=True, confidence=0.95, explanation="exact name + address", evidence=["ACME PRIVATE LIMITED", "Mumbai 400050"], site_type="official")
        if domain_evidence.domain == "acme-partner.com":
            return EntityResolution(is_match=True, confidence=0.62, explanation="similar name only", evidence=["none"], site_type="third_party")
        return EntityResolution(is_match=False, confidence=0.1, explanation="unrelated", evidence=["none"], site_type="third_party")

    def extract(self, company: Company, domain_evidence: DomainEvidence, resolution: EntityResolution, best_effort: bool = False) -> ExtractionResult:
        return ExtractionResult(website=f"https://{domain_evidence.domain}/", business="does things", customers="everyone")


def test_site_status_maps_llm_site_type() -> None:
    assert _site_status("official") == "FOUND"
    assert _site_status("third_party") == "NOT_FOUND"
    assert _site_status("ambiguous") == "AMBIGUOUS"
    assert _site_status("") == "NOT_FOUND"


def test_collect_reference_urls_deduplicates_across_domains() -> None:
    a = make_evidence("acme.com")
    b = DomainEvidence(domain="aggregator.in", pages=[PageEvidence(url="https://aggregator.in/co/acme", domain="aggregator.in")])
    urls = _collect_reference_urls([a, b])
    assert urls == ["https://acme.com/", "https://aggregator.in/co/acme"]


def test_select_best_match_prefers_high_confidence() -> None:
    evidence_a = make_evidence("acme-real.com")
    evidence_b = make_evidence("acme-partner.com")
    resolutions = [
        (evidence_b, EntityResolution(is_match=True, confidence=0.62, explanation="")),
        (evidence_a, EntityResolution(is_match=True, confidence=0.95, explanation="")),
    ]
    best = _select_best_match(resolutions, threshold=0.7)
    assert best is not None
    assert best[0].domain == "acme-real.com"


def test_select_best_match_returns_none_below_threshold() -> None:
    resolutions = [
        (make_evidence("x.com"), EntityResolution(is_match=True, confidence=0.5, explanation="")),
    ]
    assert _select_best_match(resolutions, threshold=0.7) is None


def test_run_batch_matches_correct_company(tmp_path: Path) -> None:
    settings = Settings(cache_dir=tmp_path / "cache", use_playwright=False)
    settings.exa_api_key = "x"
    settings.openrouter_api_key = "y"
    cache = JsonCache(settings.cache_dir)

    pipeline = StubbedPipeline(settings, cache)
    result = pipeline.enrich_company(make_company())

    assert result.status == "matched"
    assert result.resolution is not None
    assert result.resolution.confidence == 0.95
    assert result.resolution.site_type == "official"
    assert result.site_status == "FOUND"
    assert result.extraction is not None
    assert result.extraction.website == "https://acme-real.com/"
    assert result.reference_urls  # candidate pages were collected


def test_no_match_still_gets_best_effort_reference_extraction(tmp_path: Path) -> None:
    class NoMatchPipeline(StubbedPipeline):
        def __init__(self, settings: Settings, cache: JsonCache) -> None:
            super().__init__(settings, cache)
            self.extract_kwargs: list[tuple[str, bool]] = []

        def resolve_domain(self, company: Company, domain_evidence: DomainEvidence) -> EntityResolution:
            return EntityResolution(is_match=False, confidence=0.2, explanation="no", evidence=["none"])

        def extract(self, company: Company, domain_evidence: DomainEvidence, resolution: EntityResolution, best_effort: bool = False) -> ExtractionResult:
            self.extract_kwargs.append((domain_evidence.domain, best_effort))
            return ExtractionResult(website=f"https://{domain_evidence.domain}/", business="does things", customers="everyone")

    settings = Settings(cache_dir=tmp_path / "cache2", use_playwright=False)
    settings.exa_api_key = "x"
    settings.openrouter_api_key = "y"
    pipeline = NoMatchPipeline(settings, JsonCache(settings.cache_dir))

    result = pipeline.enrich_company(make_company())
    assert result.status == "reference_only"
    assert result.site_status == "NOT_FOUND"
    assert result.matched_url is None
    assert result.extraction is not None
    assert result.extraction.customers == "everyone"
    assert pipeline.extract_kwargs and pipeline.extract_kwargs[0][1] is True


def test_unresolved_below_threshold_gets_reference_only_extraction(tmp_path: Path) -> None:
    class LowConfidencePipeline(StubbedPipeline):
        def resolve_domain(self, company: Company, domain_evidence: DomainEvidence) -> EntityResolution:
            if domain_evidence.domain == "acme-partner.com":
                return EntityResolution(is_match=True, confidence=0.62, explanation="similar name", evidence=["none"], site_type="third_party")
            return EntityResolution(is_match=False, confidence=0.1, explanation="unrelated", evidence=["none"])

    settings = Settings(cache_dir=tmp_path / "cache3", use_playwright=False)
    settings.exa_api_key = "x"
    settings.openrouter_api_key = "y"
    pipeline = LowConfidencePipeline(settings, JsonCache(settings.cache_dir))

    result = pipeline.enrich_company(make_company())
    assert result.status == "reference_only"
    assert result.site_status == "NOT_FOUND"
    assert result.extraction is not None
    assert result.extraction.business == "does things"