"""Persisted settings + the user-managed model list.

Runtime settings (output mode, dry-run) live in the `settings` table. Translation
models live in the `models` table — each is just an OpenAI-compatible
{name, base_url, api_key} with optional peak/off-peak pricing, so any provider
works (DeepSeek, OpenRouter, OpenAI, Groq, a self-hosted vLLM/Ollama OpenAI
endpoint, …). No provider-specific config.

Config env values seed ONE default model on first boot.
"""
from __future__ import annotations

from app.config import settings as cfg
from app.models import Model, Setting
from app.pipeline.fonts import FONT_CATALOG, default_font_id

_DEVICE_CHOICES = ("auto", "cpu", "cuda")

SETTINGS: dict[str, tuple[str, tuple | None]] = {
    "output_mode": ("folder", ("folder", "cbz", "mirror")),
    "dry_run": ("true", ("true", "false")),
    "font": (default_font_id() or "anime-ace", tuple(f["id"] for f in FONT_CATALOG)),
    "device": (cfg.device if cfg.device in _DEVICE_CHOICES else "auto", _DEVICE_CHOICES),
    "gpu_worker_url": ("", None),  # e.g. http://gpu-host:9001 (remote vision worker)
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
    db.flush()  # surface pending rows so a 2nd set in the same session updates, not duplicate-inserts
    s = db.get(Setting, key)
    if s is None:
        db.add(Setting(key=key, value=str(value)))
    else:
        s.value = str(value)
    return True


# ---- models (OpenAI-compatible list + peak/off-peak pricing) ----

def _model_dict(m: Model) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "base_url": m.base_url,
        "api_key": m.api_key,
        "price_in": m.price_in or 0.0,
        "price_out": m.price_out or 0.0,
        "offpeak_in": m.offpeak_in,
        "offpeak_out": m.offpeak_out,
        "offpeak_start": m.offpeak_start,
        "offpeak_end": m.offpeak_end,
    }


def list_models(db) -> list[dict]:
    return [_model_dict(m) for m in db.query(Model).order_by(Model.id).all()]


def get_model(db, model_id: int | None) -> Model | None:
    return db.get(Model, model_id) if model_id is not None else None


def default_model(db) -> Model | None:
    return db.query(Model).order_by(Model.id).first()


def add_model(db, name: str, base_url: str, api_key: str = "",
              price_in: float = 0.0, price_out: float = 0.0,
              offpeak_in: float | None = None, offpeak_out: float | None = None,
              offpeak_start: str | None = None, offpeak_end: str | None = None) -> dict:
    m = Model(
        name=(name or "").strip(),
        base_url=(base_url or "").strip(),
        api_key=(api_key or "").strip(),
        price_in=price_in or 0.0,
        price_out=price_out or 0.0,
        offpeak_in=offpeak_in,
        offpeak_out=offpeak_out,
        offpeak_start=offpeak_start,
        offpeak_end=offpeak_end,
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    return _model_dict(m)


def update_model(db, model_id: int, **fields) -> dict | None:
    m = db.get(Model, model_id)
    if m is None:
        return None
    for k, v in fields.items():
        if hasattr(m, k):
            setattr(m, k, v)
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


def seed_default_model(db) -> None:
    """Create one default model from config/env on first boot (if none exist)."""
    if db.query(Model).count() > 0:
        return
    db.add(Model(
        name=cfg.default_model or "deepseek-v4-flash",
        base_url=cfg.default_base_url or "https://api.deepseek.com/v1",
        api_key=cfg.default_api_key,
    ))
    db.commit()
