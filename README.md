# Company Data-Enrichment Pipeline

A production-quality Python pipeline that enriches a company CSV — originally containing
**Company Name**, **City**, and **Address** — by appending three new columns:

- **Official company website**
- **What the business does**
- **Who its customers are**

The pipeline discovers candidate websites with [Exa](https://exa.ai), verifies each candidate
belongs to the **exact** legal entity using an LLM (via [OpenRouter](https://openrouter.ai))
entity-resolution step, and only then extracts business/customer information. Everything
expensive is cached locally, so reruns are cheap and safe to interrupt.

Built with Python + [`uv`](https://docs.astral.sh/uv/). No heavy frameworks — just
`httpx`, `beautifulsoup4`, `pydantic`, `openai` (OpenRouter client), and Playwright (optional
fallback).

---

## Solution overview

| Concern | Approach |
| --- | --- |
| Candidate discovery | 3 query variations per company (name + city, name + locality from address, name + "official website"), run on the Exa search API (5 results each), aggregated and deduplicated by **domain**. |
| Evidence retrieval | Prefer Exa-provided page text; otherwise fetch the HTML directly over HTTPS; if a page is JS-rendered or too thin, re-render it with Playwright (headless Chromium). Evidence = title, meta description, JSON-LD, address/pincode snippets, and cleaned page text. |
| Entity resolution | LLM is given the target company (name/city/address) plus each candidate domain's evidence and must answer `is_match`, `confidence (0–1)`, `explanation`, `evidence`. Matches below a confidence threshold are rejected — unresolved companies are left blank rather than guessed. |
| Extraction | For the highest-confidence valid match, the LLM extracts the official website, a business description, and a customer description as structured JSON. |
| Caching | Every API call (Exa searches, page fetches, entity resolution, extraction) is keyed by a content hash of its inputs and stored as JSON under `.cache/`. Reruns skip completed work → cheap, resumable, reproducible. |
| Output | The original input CSV is written back with the three columns appended; original columns/data are preserved byte-for-byte. |

## End-to-end pipeline architecture

```
input.csv ──► csvio.read_companies ──► [Company(name, city, address)]
                                          │
                                          ▼
                          ┌─────────────────────────────────────────────┐
                          │  Pipeline.enrich_company (per company,      │
                          │  run sequentially)                          │
                          │                                             │
                          │  1. build_queries ──► 3 variations          │
                          │  2. ExaClient.search × 3 (cached)           │
                          │  3. dedupe_candidates (by domain)           │
                          │  4. Fetcher.evidence_for_domain (cached)    │
                          │  5. LLM entity resolution (cached)          │
                          │  6. select best match ≥ threshold           │
                          │  7. LLM extraction (cached)                 │
                          └─────────────────────────────────────────────┘
                                          │
                                          ▼
                     csvio.write_enriched ──► output.csv (columns appended)
```

### Module map

| Module | Responsibility |
| --- | --- |
| `src/main.py` | CLI (`uv run python -m src.main input.csv`), config wiring, orchestration, summary. |
| `src/config.py` | `Settings` (pydantic-settings) — reads environment / `.env`. |
| `src/models.py` | Pydantic models: `Company`, `ExaSearchResult`, `PageEvidence`, `EntityResolution`, `ExtractionResult`, `CompanyResult`. |
| `src/queries.py` | Query generation (`build_queries`), domain normalization, candidate dedup. |
| `src/exa_client.py` | Exa REST API client. |
| `src/fetch.py` | HTTP fetching, Playwright fallback, HTML → `PageEvidence` extraction (title/meta/JSON-LD/address/pincode/text). |
| `src/llm.py` | OpenRouter client, robust JSON parsing, retries. |
| `src/pipeline.py` | Per-company orchestration + batch runner (`run_batch`). |
| `src/cache.py` | `JsonCache` — content-hashed JSON cache with `get_or_compute`. |
| `src/csvio.py` | CSV reading/writing; original columns preserved. |
| `src/logging_util.py` | Logging setup (stderr + file). |

## Candidate discovery and deduplication

For each company the pipeline builds **3 query variations**:

1. `"<base name>" <city>` — the legal name minus boilerplate suffixes (PRIVATE LIMITED, PVT LTD,
   LLP, …) plus the city.
2. `"<base name>" <locality>` — locality/landmark signalled by the address (the last non-boilerplate
   capitalized word, e.g. `BANDRA` from `FLAT 1, HILL ROAD, BANDRA (WEST), MUMBAI`).
3. `"<base name>" official website` — catches sites whose name differs from the search phrasing.

Each query returns up to 5 results from Exa. Results are aggregated and deduplicated **by domain**
(`www.` stripped, lowercase), keeping the highest-scoring result per domain. Only the top
`MAX_CANDIDATES` (default 5) domains are pursued. This keeps searches cheap while ensuring
namesake companies in other cities — which usually appear as *separate* domains — stay distinct
candidates for the entity-resolution step.

## Website retrieval and Playwright fallback

For each candidate domain, evidence is gathered from:

1. The Exa result URL — Exa's own content is used directly when it is long enough.
2. The domain homepage, if the result URL is a subpage.
3. Common informative pages (`/about`, `/contact`, …) when the above are too thin.

Per URL the retrieval ladder is:

```
Exa text (if ≥ MIN_GOOD_TEXT_CHARS)        ── preferred
   │
   ▼
HTTP fetch + parse (httpx, redirects, UA)  ── normal path
   │  (page too thin / empty text → suspicious of JS rendering)
   ▼
Playwright headless Chromium render        ── fallback for JS-rendered sites
```

`extract_evidence()` then builds a `PageEvidence` object from the HTML:

- page `<title>` and meta/OG description
- all JSON-LD blocks, flattened and summarized (name, url, description, address fields)
- up to 5 address snippets (120 chars before / 80 after any 6-digit pincode or a mention of the target city)
- the first 6-digit pincode found
- cleaned page text (whitespace collapsed, truncated to `MAX_PAGE_TEXT_CHARS`)

## Entity-resolution methodology and confidence scoring

Each candidate domain is sent to the LLM (OpenRouter) along with the **target company**:

```
TARGET COMPANY
- Legal name: NATIONAL SECURITIES DEPOSITORY LIMITED
- City: Mumbai
- Address: 301, Naman Chambers, BKC, Mumbai 400051

CANDIDATE WEBSITE EVIDENCE
[{url, title, description, json_ld, pincode, address_snippets, city_mentions, text}]
```

The LLM must return strict JSON:

```json
{"is_match": true, "confidence": 0.95, "explanation": "...", "evidence": "National Securities Depository Limited; BKC Mumbai 400051"}
```

The prompt is explicit that a match requires the **exact legal entity** and that namesake
companies, subsidiaries, franchisees, and aggregator pages must be rejected. Confidence is scored
as:

- **0.8–1.0** — exact name plus corroborating city/address/pincode, or an unmistakable exact-name match.
- **0.5–0.7** — plausible but only partial corroboration.
- **< 0.5** — namesake / competitor / portal / unrelated.

Selection logic (`_select_best_match`): among candidates with `is_match == true`, pick the one with
the **highest confidence**; if none reaches `ENTITY_MATCH_THRESHOLD` (default **0.7**), the company
is left **unresolved** (blank cells) rather than hallucinated. All entity-resolution verdicts are
cached, so re-tuning the threshold later costs nothing.

## Extraction of enrichment fields

For the selected (verified) domain the LLM extracts:

```json
{"website": "https://www.nsdl.co.in", "business": "...", "customers": "..."}
```

- `website` — full official URL from the verified evidence.
- `business` — 1–3 sentence description of what the company does.
- `customers` — who its customers are (segments/industries), only if the site claims it.

Fields the site doesn't support are `null` and become empty cells in the CSV.

## Caching strategy

`JsonCache` (in `src/cache.py`) stores JSON files under `CACHE_DIR` (default `.cache/`), one
namespace per stage:

| Namespace | Cache key (SHA-256 of) | Cached value |
| --- | --- | --- |
| `exa_search` | query + num_results | Exa results |
| `web` | URL | raw HTML + fetch method |
| `entity_resolution` | company (name/city/address) + domain + evidence snapshot | LLM verdict |
| `extraction` | company + verified domain evidence + explanation | extracted fields |

Because keys are **content hashes of inputs**, a cached result is never reused for different input
(a different company, a different page, or a different evidence snapshot produce a different key).
Reruns short-circuit to the cache, which makes the pipeline:

- **Cheap** — the second run of the same dataset performs ~zero network/LLM calls.
- **Resumable** — if the process is killed mid-run, rerunning it picks up exactly where it left off
  and only processes the remaining companies.
- **Deterministic** — the same inputs produce the same cached outputs.

## Accuracy validation

The pipeline was validated by a **sampling + manual-review** procedure:

1. **Run with a detailed dump.** Run
   `uv run python -m src.main input.csv --dump-json results.json`.
   `results.json` contains, per company: status, candidates (each with page evidence), the chosen
   resolution (confidence, explanation, evidence strings) and the extracted fields.
2. **Sample for review.** Generate a review sheet for a random 10% of companies:
   `uv run python scripts/sample_review.py results.json --sample 0.1 -o review.csv`
   The sheet lists the original company data, chosen website, business/customer descriptions,
   the LLM's confidence + explanation + supporting evidence, and the candidate domains, plus
   empty `reviewer_ok` / `notes` columns to fill in.
3. **Review each row manually.** For each row, open the website and confirm: (a) it is the exact
   legal entity (name + address/city on the site match), (b) the business description matches,
   (c) the customer description is reasonable. Mark `reviewer_ok = yes/no`.
4. **Measure and adjust.** Compute precision (fraction of `yes`) and the unresolved rate. If a
   mis-match passes the threshold, either raise `ENTITY_MATCH_THRESHOLD` or tighten the
   entity-resolution prompt (cached verdicts make this iteration free); if too many are unresolved,
   increase `MAX_CANDIDATES` or `EXA_NUM_RESULTS`.

In practice this identified the dominant failure mode: **small firms without any web presence**
(very common in the sample data — many registered companies are dormant). These are correctly left
unresolved. The remaining risk is namesake companies, which the entity-resolution prompt plus the
threshold are designed to reject.

## Setup and reproduction

### Prerequisites

- Python 3.10+ and [`uv`](https://docs.astral.sh/uv/)
- An [Exa API key](https://dashboard.exa.ai/api-keys)
- An [OpenRouter API key](https://openrouter.ai/keys)

### Install

```bash
uv sync                        # create the venv and install dependencies
uv run playwright install chromium   # only needed for the JS-rendering fallback
```

### Configure

```bash
cp .env.example .env
# edit .env and fill in:
#   EXA_API_KEY=...
#   OPENROUTER_API_KEY=...
```

## Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `EXA_API_KEY` | *(required)* | Exa web-search API key. |
| `OPENROUTER_API_KEY` | *(required)* | OpenRouter API key. |
| `OPENROUTER_MODEL` | `openai/gpt-4o-mini` | LLM model used for resolution & extraction. |
| `OPENROUTER_BASE_URL` | `https://openrouter.ai/api/v1` | OpenRouter-compatible endpoint. |
| `EXA_NUM_RESULTS` | `5` | Results per query. |
| `MAX_CANDIDATES` | `5` | Max candidate domains considered per company. |
| `ENTITY_MATCH_THRESHOLD` | `0.7` | Minimum confidence to accept a match. |
| `REQUEST_TIMEOUT` | `25` | HTTP timeout (seconds). |
| `LLM_TIMEOUT` | `90` | LLM request timeout (seconds). |
| `MAX_RETRIES` | `3` | Retry attempts for transient failures. |
| `CACHE_DIR` | `.cache` | Local JSON cache directory. |
| `MAX_PAGE_TEXT_CHARS` | `6000` | Max page-text chars kept as evidence. |
| `PROMPT_TEXT_CHARS` | `3500` | Max page-text chars shown to the LLM. |
| `MIN_GOOD_TEXT_CHARS` | `300` | Text length below which a page is "thin". |
| `USE_PLAYWRIGHT` | `true` | Enable JS-rendering fallback. |

## Usage

```bash
# Enrich in place (appends the 3 columns to input.csv)
uv run python -m src.main input.csv

# Write to a new file instead of modifying the input
uv run python -m src.main input.csv --output enriched.csv

# Test run on the first 5 companies
uv run python -m src.main input.csv --limit 5

# Detailed results dump for validation
uv run python -m src.main input.csv --dump-json results.json

# Dry run: verify CSV read/write with zero network calls
uv run python -m src.main input.csv --dry-run

# Tune the match threshold
uv run python -m src.main input.csv --threshold 0.75

# Generate a manual-review sample
uv run python scripts/sample_review.py results.json --sample 0.1 -o review.csv
```

The output is the input CSV with three columns appended (`Official company website`,
`What the business does`, `Who its customers are`). Unresolved companies get empty cells.

### Resume after interruption

Kill the process at any time. Rerunning the exact same command skips every company (or step) whose
results are already cached and only does the remaining work. There is no partial-write risk: the
output CSV is written once, after processing completes.

## Development

```bash
uv run pytest        # tests
uv run ruff check src tests scripts
```

## AI tools usage

This project was built with the assistance of AI tools:

- **ChatGPT** — used for research and to come up with the overall architecture plan for how to
  implement the pipeline (candidate discovery via Exa, evidence retrieval with Playwright fallback,
  caching strategy, LLM entity resolution, and extraction), which is reflected in `spec.md` and this
  README.
- **opencode** — used for writing and iterating on the code (all modules under `src/`) and the
  documentation (this README, `spec.md`, and module docstrings), including reviewing and fixing
  errors.

## Limitations and failure cases

- **Companies with no web presence** (dormant/small registered entities) cannot be enriched and are
  left unresolved — this is intentional (precision over recall).
- **Namesake companies** in different cities are rejected by entity resolution, but a very similar
  name *in the same city* can occasionally be accepted; the confidence threshold is the dial to
  trade precision vs. coverage.
- **JS-rendered sites** need Playwright + a Chromium install; without it such pages may yield thin
  evidence and be rejected.
- **Paywalled / bot-blocked sites** may fail to fetch (captchas, 403s); those candidates are skipped.
- **LLM model choice** affects extraction quality and JSON reliability; `MAX_RETRIES` + robust JSON
  parsing mitigate failures, and the default `openai/gpt-4o-mini` is a good balance of cost/quality.
- **Query language**: the sample data is Indian-English addresses; locality extraction is tuned for
  that format and is heuristic.
- **Rate limits**: `MAX_RETRIES` provides basic backpressure; very large datasets
  should run in batches (`--limit`) to stay within API quotas.