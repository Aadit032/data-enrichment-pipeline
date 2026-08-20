"""CLI entry point: ``uv run python -m src.main input.csv``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .cache import JsonCache
from .config import Settings
from .csvio import read_companies, write_enriched
from .logging_util import get_logger, setup_logging
from .models import CompanyResult
from .pipeline import run_batch

logger = get_logger("main")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.main",
        description=(
            "Enrich a company CSV by appending official website, business "
            "description and customer profile using Exa search + LLM entity resolution."
        ),
    )
    parser.add_argument("input", help="Path to the input CSV (Company Name / City / Address).")
    parser.add_argument("--output", help="Output CSV path (default: overwrites the input file in place).")
    parser.add_argument("--limit", type=int, default=None, help="Only process the first N companies (testing).")
    parser.add_argument("--cache-dir", type=Path, default=None, help="Override the cache directory.")
    parser.add_argument("--threshold", type=float, default=None, help="Entity-match confidence threshold (0-1).")
    parser.add_argument("--model", default=None, help="Override the OpenRouter model.")
    parser.add_argument("--no-playwright", action="store_true", help="Disable Playwright JS-rendering fallback.")
    parser.add_argument("--dry-run", action="store_true", help="Read/write the CSV without any network calls.")
    parser.add_argument("--dump-json", type=Path, default=None, help="Write a detailed per-company results JSON.")
    parser.add_argument("--log-file", default="enrichment.log", help="Log file path (default: enrichment.log).")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(level=logging.DEBUG if args.verbose else logging.INFO, log_file=args.log_file)

    settings = Settings()
    if args.cache_dir is not None:
        settings.cache_dir = args.cache_dir
    if args.threshold is not None:
        settings.entity_match_threshold = args.threshold
    if args.model:
        settings.openrouter_model = args.model
    if args.no_playwright:
        settings.use_playwright = False

    input_path = Path(args.input)
    if not input_path.exists():
        logger.error("input file not found: %s", input_path)
        return 1
    output_path = Path(args.output) if args.output else input_path

    if not args.dry_run:
        if not settings.exa_api_key:
            logger.error("EXA_API_KEY is not set (see .env.example)")
            return 1
        if not settings.openrouter_api_key:
            logger.error("OPENROUTER_API_KEY is not set (see .env.example)")
            return 1

    companies, fieldnames = read_companies(input_path)
    logger.info("read %d companies from %s", len(companies), input_path)
    if args.limit is not None:
        companies = companies[: args.limit]
        logger.info("limiting run to %d companies", len(companies))

    if args.dry_run:
        results = [CompanyResult(company=c, status="dry-run") for c in companies]
    else:
        cache = JsonCache(settings.cache_dir)
        results = run_batch(settings, cache, companies)
        logger.info(
            "done: %d matched, %d unresolved, %d errors",
            sum(r.status == "matched" for r in results),
            sum(r.status == "unresolved" for r in results),
            sum(r.status == "error" for r in results),
        )

    rows = [company.original for company in companies]
    enrichments = [
        (
            (r.extraction.website or "") if r.extraction else "",
            (r.extraction.business or "") if r.extraction else "",
            (r.extraction.customers or "") if r.extraction else "",
        )
        for r in results
    ]

    write_enriched(output_path, fieldnames, rows, enrichments)
    logger.info("wrote enriched CSV to %s", output_path)

    if args.dump_json:
        args.dump_json.write_text(
            json.dumps(
                [r.model_dump(mode="json") for r in results],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        logger.info("wrote detailed results to %s", args.dump_json)

    print(f"Enriched {len(results)} companies -> {output_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
