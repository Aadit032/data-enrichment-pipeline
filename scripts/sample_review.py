#!/usr/bin/env python3
"""Generate a manual-review CSV from a pipeline ``--dump-json`` file.

Randomly samples a fraction of companies and writes a CSV with everything a
reviewer needs to check each match: original data, chosen website, business and
customer descriptions, the entity-resolution explanation and evidence, plus all
candidate domains.

Usage:
    uv run python -m src.main input.csv --dump-json results.json
    uv run python scripts/sample_review.py results.json --sample 0.1 -o review.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import random


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dump_json", help="results.json produced by --dump-json")
    parser.add_argument("--sample", type=float, default=0.1, help="Fraction of companies to sample (default 0.1)")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("-o", "--output", default="review.csv", help="Output CSV path")
    args = parser.parse_args()

    with open(args.dump_json, encoding="utf-8") as f:
        results = json.load(f)

    random.seed(args.seed)
    sample = random.sample(results, max(1, round(len(results) * args.sample)))

    headers = [
        "row_index",
        "company_name",
        "city",
        "address",
        "status",
        "website",
        "business",
        "customers",
        "confidence",
        "is_match",
        "resolution_explanation",
        "resolution_evidence",
        "candidate_domains",
        "reviewer_ok",
        "notes",
    ]

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for result in sample:
            company = result["company"]
            extraction = result.get("extraction") or {}
            resolution = result.get("resolution") or {}
            candidates = "; ".join(
                c["domain"] for c in result.get("candidates", [])
            )
            writer.writerow(
                [
                    company.get("row_index", ""),
                    company.get("name", ""),
                    company.get("city", ""),
                    company.get("address", ""),
                    result.get("status", ""),
                    extraction.get("website", ""),
                    extraction.get("business", ""),
                    extraction.get("customers", ""),
                    resolution.get("confidence", ""),
                    resolution.get("is_match", ""),
                    resolution.get("explanation", ""),
                    resolution.get("evidence", ""),
                    candidates,
                    "",
                    "",
                ]
            )

    print(f"Wrote {len(sample)} sampled rows to {args.output}")


if __name__ == "__main__":
    main()
