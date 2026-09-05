"""Configuration — env + defaults (pydantic-settings).

Secrets come from `.env` (never committed). The model list is user-managed in
the web UI (SQLite `models` table); the env vars below only seed ONE default
model on first boot — any OpenAI-compatible endpoint works.
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
    source_lang: str = "auto"     # auto | ja | ko | zh — source language for OCR
    target_lang: str = "en"
    device: str = "auto"          # auto | cpu | cuda — local vision-model device

    # Default translation model (seeded on first boot; add/remove more from the
    # web UI Settings tab). Model-agnostic — any OpenAI-compatible endpoint.
    default_model: str = "deepseek-v4-flash"
    default_base_url: str = "https://api.deepseek.com/v1"
    default_api_key: str = ""

    # Storage
    state_db: str = "/data/mangafill.db"
    jobs_dir: str = "/data/jobs"
    raw_dir: str = "/input"
    output_dir: str = "/data/output"

    # Output
    output_mode: str = "folder"   # folder | cbz | mirror


settings = Settings()
