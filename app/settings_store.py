"""Persisted settings + the user-managed model list.

Runtime settings (output mode, dry-run) live in the `settings` table. Translation
models live in the `models` table — each is just an OpenAI-compatible
{name, base_url, api_key}, so any provider works (DeepSeek, OpenRouter, OpenAI,
Groq, a self-hosted vLLM/Ollama OpenAI endpoint, …). No provider-specific config.

Config env values seed ONE default model on first boot.
"""
from __future__ import annotations

import os

from app.config import settings as cfg
from app.models import Model, Setting

SETTINGS: dict[str, tuple[str, tuple | None]] = {
    "output_mode": ("folder", ("folder", "cbz")),
    "dry_run": ("true", ("true", "false")),
}


# ---- settings (key/value) ----

def get_setting(db, key: str) -> str:
    s = db.get(Setting, key)
    return s.value if s is not None else SETTINGS[key][0]


def get_all(db) -> dict[str, str]:
    return {k: get_setting(db, k) for k in SETTINGS}


def set_setting(db, key: str, value) -> bool:
    if key not in SETTINGS:
        return False
    allowed = SETTINGS[key][1]
    if allowed is not None and value not in allowed:
        return False
    s = db.get(Setting, key)
    if s is None:
        db.add(Setting(key=key, value=str(value)))
    else:
        s.value = str(value)
    return True


# ---- models (OpenAI-compatible list) ----

def _model_dict(m: Model) -> dict:
    return {"id": m.id, "name": m.name, "base_url": m.base_url, "api_key": m.api_key}


def list_models(db) -> list[dict]:
    return [_model_dict(m) for m in db.query(Model).order_by(Model.id).all()]


def get_model(db, model_id: int | None) -> Model | None:
    return db.get(Model, model_id) if model_id is not None else None


def default_model(db) -> Model | None:
    return db.query(Model).order_by(Model.id).first()


def add_model(db, name: str, base_url: str, api_key: str) -> dict:
    m = Model(
        name=(name or "").strip(),
        base_url=(base_url or "").strip(),
        api_key=(api_key or "").strip(),
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return _model_dict(m)


def delete_model(db, model_id: int) -> bool:
    m = db.get(Model, model_id)
    if m is None:
        return False
    db.delete(m)
    db.commit()
    return True


def _env_key() -> str:
    p = os.path.expanduser("~/.hermes/.env")
    if os.path.exists(p):
        for line in open(p):
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY=") or line.startswith("OPENROUTER_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def seed_default_model(db) -> None:
    """Create one default model from config/env on first boot (if none exist)."""
    if db.query(Model).count() > 0:
        return
    key = cfg.default_api_key or _env_key()
    db.add(Model(
        name=cfg.default_model or "deepseek-v4-flash",
        base_url=cfg.default_base_url or "https://api.deepseek.com/v1",
        api_key=key,
    ))
    db.commit()
