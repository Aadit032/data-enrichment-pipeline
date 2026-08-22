"""Search-query generation and candidate deduplication.

Queries are derived from the legal entity name plus city/address signals, with
no company-specific hardcoding.
"""

from __future__ import annotations

import re
import urllib.parse

from .models import Company, ExaSearchResult

_SUFFIX_PATTERNS = [
    (r"\bPRIVATE\s+LIMITED\b", ""),
    (r"\bPVT\s+LTD\.?\b", ""),
    (r"\bPVT\.?\b", ""),
    (r"\bL\.?\.?P\.?\b", ""),
    (r"\bLLP\b", ""),
    (r"\bLIMITED\b", ""),
    (r"\bLTD\.?\b", ""),
]

# Structural/boilerplate address words that never identify the business itself.
_STOP_WORDS = {
    "office",
    "no",
    "flat",
    "floor",
    "building",
    "bldg",
    "road",
    "rd",
    "street",
    "opp",
    "opposite",
    "near",
    "next",
    "behind",
    "chs",
    "ltd",
    "pvt",
    "society",
    "nagar",
    "wing",
    "plot",
    "unit",
    "shop",
    "gala",
    "tower",
    "complex",
    "sector",
    "estate",
    "marg",
    "avenue",
    "line",
    "village",
    "house",
    "chawl",
    "industrial",
    "area",
    "lane",
    "housing",
    "residential",
    "colony",
    "west",
    "east",
    "north",
    "south",
    "hill",
    "height",
    "heights",
    "park",
    "chowk",
    "chauk",
    "path",
    "gate",
    "square",
    "circle",
    "bridge",
    "peth",
    "gaon",
    "basti",
    "midc",
    "school",
    "cinema",
    "hotel",
    "market",
    "bazaar",
    "temple",
    "church",
    "hall",
    "mall",
    "bhavan",
    "sadan",
    "krupa",
    "guest",
    "college",
    "station",
    "post",
    "council",
    "club",
    "india",
    "maharashtra",
    "mumbai",
    "pune",
    "thane",
    "nagpur",
    "nashik",
    "jalna",
    "sangli",
    "aurangabad",
    "palghar",
    "kavesar",
}


def base_name(name: str) -> str:
    """Strip legal-suffix boilerplate, e.g. 'ABC PRIVATE LIMITED' -> 'ABC'."""
    cleaned = name.upper().strip()
    for pattern, replacement in _SUFFIX_PATTERNS:
        cleaned = re.sub(pattern, replacement, cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or name.strip()


def locality_from_address(address: str, city: str) -> str:
    """Pick the most identifying word from the address (falls back to city).

    Localities/landmarks tend to appear near the end of Indian addresses, so we
    choose the *last* capitalized word that is not boilerplate.
    """
    words = re.findall(r"[A-Za-z][A-Za-z-]*", address or "")
    candidates = [
        token
        for token in words
        if len(token) >= 4
        and token.lower() not in _STOP_WORDS
        and token.lower() != city.lower()
        and token[0].isupper()
    ]
    if candidates:
        return candidates[-1]
    return city.strip()


def build_queries(company: Company) -> list[str]:
    """Return 3 query variations combining name + location/address signals."""
    base = base_name(company.name)
    city = company.city.strip()
    locality = locality_from_address(company.address, city)

    queries = [
        f"{base} {city}".strip(),
        f"{base} {locality}".strip() if locality else f"{base} company",
        f"{base} official website",
    ]
    # Deduplicate while keeping order.
    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            unique.append(q)
    return unique


def netloc(url: str) -> str:
    return (urllib.parse.urlsplit(url).netloc or "").lower()


def build_url(domain: str, path: str = "/") -> str:
    """Build an https URL from a bare domain and path."""
    return f"https://{domain}{path if path.startswith('/') else '/' + path}"


def normalize_domain(domain: str) -> str:
    d = (domain or "").lower().strip()
    d = d.removeprefix("www.")
    return d


def dedupe_candidates(results: list[ExaSearchResult]) -> list[ExaSearchResult]:
    """Deduplicate results by domain, keeping the strongest result per domain.

    Within a domain the result with the highest score wins; a result with text
    is preferred over a text-less result regardless of score order.
    """
    best: dict[str, ExaSearchResult] = {}
    for result in results:
        domain = normalize_domain(netloc(result.url))
        if not domain:
            continue
        current = best.get(domain)
        if current is None:
            best[domain] = result
            continue
        score = result.score or 0.0
        cur_score = current.score or 0.0
        if score > cur_score or (not current.text and result.text):
            best[domain] = result

    ranked = sorted(best.values(), key=lambda r: r.score or 0.0, reverse=True)
    return ranked
