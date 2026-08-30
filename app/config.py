"""Configuration — env + defaults (pydantic-settings).

Secrets come from `.env` (never committed). The full YAML schema is documented in
`config.example.yaml`; YAML loading is wired in a later milestone once runtime
settings (output mode, dry-run, model) are exposed via the web UI.
"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MANGA_FILL_", env_file=".env", extra="ignore"
    )

    # App
    host: str = "0.0.0.0"
    port: int = 8788
    log_level: str = "INFO"

    # Pipeline
    dry_run: bool = True          # default ON — never change the default
    source_lang: str = "jp"
    target_lang: str = "en"
    device: str = "cpu"

    # Translation (cloud-only, no local GPU)
    openrouter_api_key: str = ""
    openrouter_model: str = "google/gemini-2.0-flash"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    deepseek_api_key: str = ""

    # Storage
    state_db: str = "/data/mangafill.db"
    jobs_dir: str = "/data/jobs"
    raw_dir: str = "/input"
    output_dir: str = "/data/output"

    # Output
    output_mode: str = "folder"   # folder | cbz  (leave-as-is default)


settings = Settings()
