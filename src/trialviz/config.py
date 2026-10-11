"""Runtime configuration, loaded from environment variables / .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    openai_api_key: str | None = None
    openai_base_url: str | None = None
    planner_model: str = "gpt-5.4"
    planner_timeout_s: float = 30.0

    ctgov_base_url: str = "https://clinicaltrials.gov/api/v2"
    ctgov_max_records: int = Field(3000, ge=100, le=20_000)
    ctgov_page_size: int = Field(1000, ge=1, le=1000)  # API hard max is 1000
    ctgov_max_concurrency: int = Field(8, ge=1, le=32)
    ctgov_timeout_s: float = 20.0
    ctgov_max_retries: int = 3

    max_citations_per_datum: int = Field(5, ge=0, le=50)

    # Saved {request, response} files the web UI can show without calling the LLM.
    examples_dir: Path = Path("examples")


@lru_cache
def get_settings() -> Settings:
    return Settings()
