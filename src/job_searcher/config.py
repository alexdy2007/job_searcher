"""Single source of truth for configuration.

Everything reads `settings` from here; no module calls `os.getenv` directly.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://job_searcher:job_searcher@localhost:5433/job_searcher"

    anthropic_api_key: str = ""
    extraction_model: str = "claude-opus-5"

    user_agent: str = "job-searcher/0.1 (+https://github.com/yourname/job-searcher)"

    max_concurrency: int = 8
    per_domain_concurrency: int = 2

    #: Hard stop for one scrape run, in USD. 0 disables the ceiling.
    run_cost_budget_usd: float = 5.0

    #: Raw page cache. Kept inside the project so the path works on Windows
    #: without depending on a POSIX temp dir.
    cache_dir: Path = Field(default=PROJECT_ROOT / ".cache")

    @property
    def page_cache_dir(self) -> Path:
        return self.cache_dir / "pages"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
