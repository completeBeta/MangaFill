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
    # Verbose by default (house standard); MANGA_FILL_LOG_LEVEL raises or lowers it.
    log_level: str = "DEBUG"

    # Pipeline
    dry_run: bool = True          # default ON — never change the default
    source_lang: str = "auto"     # auto | ja | ko | zh — source language for OCR
    target_lang: str = "en"
    device: str = "auto"          # auto | cpu | cuda — local vision-model device

    # ---- Engine (upstream manga-image-translator as the pipeline) --------------
    # Empty URL ⇒ the app uses its OWN in-process pipeline (v0.27.x behaviour).
    # Set ⇒ every page is processed by that engine over HTTP instead, and the app is
    # the shell: jobs, queue, viewer, logs, CBZ/PDF, fonts.
    engine_url: str = ""
    engine_timeout_s: int = 900
    engine_enabled: bool = False

    # Logging surfaces (see app/services/logging.py). The engine container writes its
    # own stdout/stderr into engine_logs_dir through a shared volume.
    logs_dir: str = "/data/logs"
    engine_logs_dir: str = "/data/engine-logs"

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

    # Local timezone (IANA name) used for calendar windows — the cost tally's
    # Day/Week/Month/Year and anything that reports "today". Override with
    # MANGA_FILL_TIMEZONE, or with TZ (checked next); unknown names fall back to
    # the container's own zone and finally to UTC.
    timezone: str = "Australia/Sydney"


settings = Settings()
