"""Settings loaded from environment variables and a local ``.env`` file."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration.

    Values are read from environment variables (or a ``.env`` file in the
    project root). Variable names match field names, case-insensitively, so
    ``exa_api_key`` is populated from ``EXA_API_KEY``.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # Credentials
    exa_api_key: str = ""
    openrouter_api_key: str = ""

    # LLM
    openrouter_model: str = "openai/gpt-4o-mini"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # Candidate discovery
    exa_num_results: int = 5
    max_candidates: int = 5

    # Concurrency / robustness
    max_workers: int = 4
    request_timeout: float = 25.0
    llm_timeout: float = 90.0
    max_retries: int = 3

    # Matching
    entity_match_threshold: float = 0.7

    # Caching / text sizes
    cache_dir: Path = Path(".cache")
    max_page_text_chars: int = 6000
    prompt_text_chars: int = 3500
    min_good_text_chars: int = 300

    # Playwright JS-rendering fallback
    use_playwright: bool = True
