"""Pydantic data models shared across the pipeline."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Company(BaseModel):
    """A single input row: the legal entity we want to enrich."""

    row_index: int = 0
    name: str
    city: str = ""
    address: str = ""
    original: dict[str, str] = Field(default_factory=dict)


class ExaSearchResult(BaseModel):
    """One search result returned by the Exa API."""

    url: str
    domain: str = ""
    title: str | None = None
    text: str | None = None
    score: float | None = None


class PageEvidence(BaseModel):
    """Extracted evidence from a single fetched page."""

    url: str
    domain: str = ""
    title: str | None = None
    description: str | None = None
    json_ld: list[str] = Field(default_factory=list)
    address_lines: list[str] = Field(default_factory=list)
    pincode: str | None = None
    city_mentions: list[str] = Field(default_factory=list)
    text: str = ""
    fetched_via: str = "http"  # "exa" | "http" | "playwright"
    truncated: bool = False


class DomainEvidence(BaseModel):
    """Combined evidence for one candidate domain (may cover several pages)."""

    domain: str
    pages: list[PageEvidence] = Field(default_factory=list)


class EntityResolution(BaseModel):
    """LLM verdict on whether a candidate website is the exact company."""

    is_match: bool
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str = ""
    evidence: str = ""


class ExtractionResult(BaseModel):
    """LLM-extracted enrichment fields for a verified company."""

    website: str | None = None
    business: str | None = None
    customers: str | None = None


class CompanyResult(BaseModel):
    """Full result for one company, used for logs, dumps and validation."""

    company: Company
    status: str = "unresolved"  # "matched" | "unresolved" | "error" | "dry-run"
    candidates: list[DomainEvidence] = Field(default_factory=list)
    resolution: EntityResolution | None = None
    extraction: ExtractionResult | None = None
    error: str | None = None
