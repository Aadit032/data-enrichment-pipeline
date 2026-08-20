# Company Data-Enrichment Pipeline

A Python pipeline that takes a CSV of companies (**name**, **city**, **address**) and enriches
each row with three new fields:

- **Official company website**
- **What the business does**
- **Who its customers are**

It discovers candidate sites with [Exa](https://exa.ai), verifies each one is the **exact**
legal entity using an LLM, and only then extracts business/customer info. Everything expensive
is cached locally, so reruns are cheap and resumable.

Built with Python + [`uv`](https://docs.astral.sh/uv/) — no heavy frameworks, just `httpx`,
`beautifulsoup4`, `pydantic`, `openai`, and optional Playwright.

---

## How it works

| Step | What happens |
| --- | --- |
| **1. Candidate discovery** | 3 Exa queries per company (name+city, name+locality, name+`official website`), 5 results each, aggregated and deduplicated **by domain**. |
| **2. Evidence retrieval** | Fetch each candidate page (Exa text → direct HTTP → Playwright fallback for JS-heavy sites) and reduce it to title, description, JSON-LD, address/pincode snippets and cleaned text. |
| **3. Entity resolution (LLM)** | LLM decides if a candidate is the **exact legal entity** (rejecting namesakes, subsidiaries, aggregators) and returns `is_match`, `confidence`, `site_type` (`official` / `third_party` / `ambiguous`). Only matches above `ENTITY_MATCH_THRESHOLD` (0.7) are accepted as the official site. |
| **4. Extraction (LLM)** | Extract a business description and a customer description as structured JSON from the matched site — or, when nothing matched, from the single best available page (even a third-party profile). The website URL is only ever kept when the site was confirmed official. |

Every expensive call (Exa searches, page fetches, LLM verdicts) is cached under `.cache/`
keyed by a hash of its inputs — reruns skip completed work.

## Output files

The run produces **two CSVs**:

| File | Contents |
| --- | --- |
| `enriched.csv` | Original rows + the 3 new columns. `Official company website` is **empty unless** a candidate was confirmed as the company's own official site. |
| `references.csv` | Original rows + `Status` (`FOUND` / `NOT_FOUND` / `AMBIGUOUS`) + `Found URLs` — the third-party pages that describe the company (tracxn, tofler, sensibook, …). |

> **Why two files?** Most registered companies have no discoverable official website — the web
> only surfaces directory/aggregator pages. Rather than write a guess into the official-website
> column, that column is kept **null**, and the directory URLs are preserved in `references.csv`
> for manual review.

### Why some "Who its customers are" cells are empty

The customer field is the hardest column to fill and stays empty in three situations:

1. **No web presence found.** If search returns no pages at all for the company (dormant,
   shell or brand-new firms), there is no evidence to extract from.
2. **Only registration metadata available.** The only sources surfaced are directory/aggregator
   pages (thecompanycheck.com, mycorporateinfo.com, tofler.in, tracxn.com, …) that list the
   legal name, address, directors and incorporation date — but never what the company does or
   who it serves. The LLM is instructed never to fabricate facts, so the cell is left empty
   rather than guessed. A fallback extraction still runs against the best available page
   (official or third-party), but if the content genuinely contains no business/customer
   information, both `business` and `customers` come back null.
3. **Transient API failures.** Rate limits (HTTP 429) can abort an LLM call; re-running the
   same command resumes from the cache and fills these cells.

Customers are only inferred when the company's business is itself known — the LLM never guesses
a customer profile for an unknown business — which is why `customers` is almost always empty
whenever `business` is empty too.

## LLM entity-resolution method

Each candidate domain is sent to the LLM along with the target company (name/city/address) and
its page evidence. The system prompt requires a match to the **exact legal entity** and rules on
namesakes/portals/franchisees. Confidence bands:

- **0.8–1.0** — exact name + corroborating city/address/pincode.
- **0.5–0.7** — plausible but partial corroboration.
- **< 0.5** — namesake / competitor / portal / unrelated.

`site_type` marks whether the site is *operated by* the company (`official`) or merely *describes*
it (`third_party` / `ambiguous`); that drives the `FOUND` / `NOT_FOUND` / `AMBIGUOUS` status.

### Model choice

This dataset was processed with two models:

- **First half** — `openai/gpt-4o-mini` via **OpenRouter**.
- **Second half (current default)** — `openai/gpt-oss-20b` via the **Groq API**
  (`https://api.groq.com/openai/v1`), switched after the OpenRouter credits ran out.

The pipeline is API-agnostic: any OpenAI-compatible endpoint works via
`OPENROUTER_BASE_URL` + `OPENROUTER_MODEL`.

## Setup and reproduction (from scratch)

### 0. Prerequisites

- **Python 3.10+** — <https://www.python.org/downloads/>
- **[`uv`](https://docs.astral.sh/uv/)** — fast Python package manager:

  ```bash
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ```

- **Git** — clone the repo:

  ```bash
  git clone <repo-url> && cd monter
  ```

### 1. Get free API keys

**Exa** — free tier gives **$20 on signup + $10 every month**, no card required.
Get a key at <https://dashboard.exa.ai/api-keys>.

**LLM** — pick one of:

- **OpenRouter** — free to join and includes free models (`openai/gpt-4o-mini`,
  `openrouter/free`). New accounts get a small free allowance; free models are capped at
  ~50 requests/day until you add ≥ $10 in credits. Keys at <https://openrouter.ai/keys>.
- **Groq (recommended)** — fast free tier with generous daily limits, no card needed.
  Keys at <https://console.groq.com/keys>. Set
  `OPENROUTER_BASE_URL=https://api.groq.com/openai/v1` and a model such as
  `openai/gpt-oss-20b`.

### 2. Install dependencies

```bash
uv sync                        # create the venv and install dependencies
uv run playwright install chromium   # only for the JS-rendering fallback
```

### 3. Configure

```bash
cp .env.example .env
# set EXA_API_KEY and OPENROUTER_API_KEY
```

### 4. Run

```bash
uv run python -m src.main input.csv --output enriched.csv --ref-output references.csv
```

## Environment variables

| Variable | Default | Description |
| --- | --- | --- |
| `EXA_API_KEY` | *(required)* | Exa web-search API key. |
| `OPENROUTER_API_KEY` | *(required)* | LLM API key (OpenRouter or Groq). |
| `OPENROUTER_MODEL` | `openai/gpt-oss-20b` | LLM model for resolution & extraction. |
| `OPENROUTER_BASE_URL` | `https://api.groq.com/openai/v1` | OpenAI-compatible endpoint. |
| `EXA_NUM_RESULTS` | `5` | Results per query. |
| `MAX_CANDIDATES` | `5` | Max candidate domains per company. |
| `ENTITY_MATCH_THRESHOLD` | `0.7` | Min confidence to accept a match. |
| `REQUEST_TIMEOUT` | `25` | HTTP timeout (seconds). |
| `LLM_TIMEOUT` | `90` | LLM request timeout (seconds). |
| `MAX_RETRIES` | `3` | Retries for transient failures. |
| `LLM_CALL_DELAY` | `5` | Pause between LLM calls (rate limiting). |
| `CACHE_DIR` | `.cache` | Local JSON cache. |
| `MAX_PAGE_TEXT_CHARS` | `6000` | Max page text kept as evidence. |
| `PROMPT_TEXT_CHARS` | `3500` | Max page text shown to the LLM. |
| `MIN_GOOD_TEXT_CHARS` | `300` | Text below this is "thin" (triggers fallback). |
| `USE_PLAYWRIGHT` | `true` | Enable the JS-rendering fallback. |

## Usage

```bash
# Enrich in place (appends the 3 columns to input.csv)
uv run python -m src.main input.csv

# Write to a new file + a reference CSV
uv run python -m src.main input.csv --output enriched.csv --ref-output references.csv

# Test run on the first 5 companies
uv run python -m src.main input.csv --limit 5

# Detailed results dump for validation
uv run python -m src.main input.csv --dump-json results.json

# Dry run: verify CSV read/write with zero network calls
uv run python -m src.main input.csv --dry-run

# Tune the match threshold
uv run python -m src.main input.csv --threshold 0.75

# Generate a manual-review sample (10%)
uv run python scripts/sample_review.py results.json --sample 0.1 -o review.csv
```

### Resume after interruption

Kill the process any time. Rerunning the same command skips everything already cached and only
does the remaining work. Output CSVs are written once, after processing completes.

## Validation

1. Run with `--dump-json results.json`.
2. `uv run python scripts/sample_review.py results.json --sample 0.1 -o review.csv`.
3. Manually check each sampled row: is the site the exact entity? does the business/customer
   text match? mark `reviewer_ok`.
4. If a wrong match passes, raise `ENTITY_MATCH_THRESHOLD` or tighten the prompt (cached verdicts
   make this free). If too many are unresolved, raise `MAX_CANDIDATES` / `EXA_NUM_RESULTS`.

The dominant failure mode in practice: **small/dormant firms with no web presence**, or firms
whose only trace is registry/aggregator metadata — such rows keep empty business/customer cells
(see "Why some cells are empty" above) but never a guessed website.

## AI tools usage

This project was built with AI assistance:

- **ChatGPT** — used to talk through and design the overall architecture plan (Exa candidate
  discovery, evidence retrieval with Playwright fallback, caching, LLM entity resolution and
  extraction), captured in `spec.md`.
- **opencode** — used to write and iterate on the code (`src/`, `scripts/`) and the
  documentation (this README, `spec.md`), including fixing errors.
- **LLM decision methods** — entity recognition uses the exact-entity prompt + confidence bands
  above (`site_type` official/third_party/ambiguous); extraction uses a prompt that describes
  the business from evidence and infers customers only when the site lacks explicit customer
  info.
- **Model change** — the first half of the dataset was processed with `openai/gpt-4o-mini` on
  OpenRouter; after the OpenRouter credits ran out, processing switched to `openai/gpt-oss-20b`
  on the free Groq API, which is the current default.

## Development

```bash
uv run pytest              # tests
uv run ruff check src tests scripts
```

## Limitations

- Companies with **no web presence**, or only registry/aggregator metadata, keep empty
  business/customer cells (never guessed); a best-effort extraction runs from the strongest
  available page but yields nothing if the content has no business/customer info.
- **Namesake companies** in the same city are the main remaining risk; `ENTITY_MATCH_THRESHOLD`
  is the dial.
- **JS-heavy / paywalled / bot-blocked** sites may yield thin evidence; Playwright + Chromium
  helps but captchas/403s are skipped.
- **LLM choice** affects extraction quality; `gpt-oss-20b` on Groq is the current default.
- Query/locality parsing is **tuned for Indian-English addresses** and is heuristic.
- **Rate limits**: `MAX_RETRIES` + `LLM_CALL_DELAY` provide backpressure; large datasets should
  run in batches (`--limit`).