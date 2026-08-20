Build a Python company-data enrichment pipeline using uv.

Input: an existing CSV containing Company Name, City, and Address. Modify the existing CSV by appending:
- Official company website
- What the business does
- Who its customers are

Requirements:

1. Use Exa for web search. For each company, generate 2–3 query variations using the company name + location/address signals. Run each query with 5 results. Aggregate all results and deduplicate candidate URLs/domains.

2. Cache everything expensive: Exa search results, webpage content, LLM entity-resolution results, and final extraction results. Use a simple local cache (JSON files) so reruns avoid unnecessary API calls.

3. For each candidate domain, retrieve content:
   - Prefer Exa-provided content when sufficient.
   - Otherwise fetch HTML directly.
   - If the site is JS-rendered/insufficient, fall back to Playwright.
   Extract useful evidence including company name, address, city, pincode, title, metadata, JSON-LD, and page text. Look at relevant pages such as homepage, about, contact, products/services, customers/case studies where available.

4. Perform entity resolution with an LLM via OpenRouter. Give it the original company name/city/address plus candidate website evidence. It should return structured JSON:
   - is_match
   - confidence (0–1)
   - explanation
   - evidence
   The LLM should determine whether the candidate website belongs to the exact company, not merely a similarly named company. Cache this result.

5. Select the highest-confidence valid match. Use sensible thresholds and leave companies unresolved rather than hallucinating a match.

6. For the selected company, use an LLM to extract:
   - official company website
   - what the business does
   - who its customers are
   Return structured JSON and cache the result.

7. Write the enriched fields back into the existing input CSV without removing or modifying the original columns/data.

8. Make the project production-quality but simple:
   - Python + uv
   - clean modular structure
   - .env for EXA_API_KEY and OPENROUTER_API_KEY
   - retries/timeouts
   - logging
   - simple sequential execution
   - type hints
   - structured Pydantic models
   - no hardcoded company-specific logic
   - CLI such as `uv run python -m src.main input.csv`
   - avoid unnecessary frameworks/dependencies

9. Create a comprehensive README.md explaining:
   - Solution overview
   - End-to-end architecture/pipeline
   - Candidate discovery and deduplication
   - Website retrieval and Playwright fallback
   - Entity-resolution methodology and confidence scoring
   - Caching strategy
   - How accuracy was validated, including a practical sampling/manual-validation methodology
   - Setup and reproduction instructions
   - Environment variables
   - Usage
   - Output format
   - Limitations/failure cases

Also include a sample `.env.example` and ensure the program can resume safely if interrupted.
