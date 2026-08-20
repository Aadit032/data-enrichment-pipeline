"""CSV round-trip tests."""

from __future__ import annotations

import csv

from src.csvio import ENRICHMENT_COLUMNS, read_companies, write_enriched

SAMPLE = (
    ",Company Name (legal entity),City,Address\n"
    ',SALDANHA REAL ESTATE PRIVATE LIMITED,Mumbai,"FLAT 1, HILL ROAD, BANDRA (WEST), MUMBAI, 400050-India"\n'
    ',ART ESTATES PRIVATE LIMITED,Pune,"AGARKAR BHAVAN, LBS ROAD, PUNE, 411030-India"\n'
)


def test_read_companies(tmp_path) -> None:
    path = tmp_path / "input.csv"
    path.write_text(SAMPLE, encoding="utf-8")
    companies, fieldnames = read_companies(path)
    assert len(companies) == 2
    assert fieldnames == ["", "Company Name (legal entity)", "City", "Address"]
    assert companies[0].name == "SALDANHA REAL ESTATE PRIVATE LIMITED"
    assert companies[0].city == "Mumbai"
    assert "BANDRA" in companies[0].address
    assert companies[1].row_index == 1


def test_read_companies_realigns_phantom_leading_column(tmp_path) -> None:
    # Header cell for the empty leading column is missing, but data still has it.
    path = tmp_path / "input.csv"
    path.write_text(
        "Company Name (legal entity),City,Address\n"
        ",SALDANHA REAL ESTATE PRIVATE LIMITED,Mumbai,\"FLAT 1, HILL ROAD, BANDRA (WEST), MUMBAI, 400050-India\"\n",
        encoding="utf-8",
    )
    companies, fieldnames = read_companies(path)
    assert len(companies) == 1
    assert fieldnames == ["", "Company Name (legal entity)", "City", "Address"]
    assert companies[0].name == "SALDANHA REAL ESTATE PRIVATE LIMITED"
    assert companies[0].city == "Mumbai"
    assert "BANDRA" in companies[0].address


def test_write_enriched_preserves_original(tmp_path) -> None:
    path = tmp_path / "input.csv"
    out_path = tmp_path / "out.csv"
    path.write_text(SAMPLE, encoding="utf-8")
    companies, fieldnames = read_companies(path)

    rows = [c.original for c in companies]
    enrichments = [("https://a.com", "does A", "customers A"), ("https://b.com", "does B", "customers B")]
    write_enriched(out_path, fieldnames, rows, enrichments)

    with open(out_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        out_fieldnames = list(reader.fieldnames or [])
        output = list(reader)

    assert out_fieldnames == fieldnames + ENRICHMENT_COLUMNS
    assert output[0]["Company Name (legal entity)"] == "SALDANHA REAL ESTATE PRIVATE LIMITED"
    assert output[0]["Address"] == companies[0].address
    assert output[0]["Official company website"] == "https://a.com"
    assert output[1]["What the business does"] == "does B"
