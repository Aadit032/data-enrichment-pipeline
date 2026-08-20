"""CSV reading and writing.

The original columns are preserved verbatim; enrichment columns are appended.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .models import Company

ENRICHMENT_COLUMNS = [
    "Official company website",
    "What the business does",
    "Who its customers are",
]


def _find_column(fieldnames: list[str], hint: str) -> str:
    for name in fieldnames:
        if hint in (name or "").lower():
            return name
    raise ValueError(f"could not find a column containing '{hint}' in header: {fieldnames}")


def read_companies(path: str | Path) -> tuple[list[Company], list[str]]:
    """Read input CSV and return companies plus original header order."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        header = next(reader, None)
        if not header:
            raise ValueError(f"{path} has no header row")
        rows = [row for row in reader if row]

    # Some spreadsheets export a phantom leading column whose header cell is
    # blank (e.g. an empty "Sr No" column). If every data row starts with an
    # empty field but the header does not, realign by prepending an empty
    # header cell so the remaining columns line up with their names.
    if header and header[0] != "" and all((row[0] or "").strip() == "" for row in rows):
        header = [""] + header

    name_col = _find_column(header, "company")
    city_col = _find_column(header, "city")
    address_col = _find_column(header, "address")

    companies: list[Company] = []
    for row_index, row in enumerate(rows):
        record = dict(zip(header, row))
        name = (record.get(name_col) or "").strip()
        if not name:
            continue
        companies.append(
            Company(
                row_index=row_index,
                name=name,
                city=(record.get(city_col) or "").strip(),
                address=(record.get(address_col) or "").strip(),
                original=record,
            )
        )
    return companies, header


def write_enriched(
    output_path: str | Path,
    fieldnames: list[str],
    rows: list[dict[str, str]],
    enrichments: list[tuple[str, str, str]],
) -> None:
    """Write rows with enrichment columns appended (original data untouched)."""
    new_columns = [c for c in ENRICHMENT_COLUMNS if c not in fieldnames]
    out_fieldnames = [*fieldnames, *new_columns]

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fieldnames, restval="", extrasaction="ignore")
        writer.writeheader()
        for row, (website, business, customers) in zip(rows, enrichments):
            out = dict(row)
            out["Official company website"] = website
            out["What the business does"] = business
            out["Who its customers are"] = customers
            writer.writerow(out)
