"""Persisted settings store — a thin layer over the `settings` table.

Runtime-editable settings (model, API keys, base URL, dry-run, output mode) live
in SQLite so the web UI can change them without a rebuild. Config env values
(`app.config.settings`) are the *defaults*; a DB row overrides its default.

`SETTINGS` maps key -> (default, allowed_values). `allowed_values` is None for
free-text fields, a tuple for constrained fields.
"""
from __future__ import annotations

from app.config import settings as cfg
from app.models import Setting

SETTINGS: dict[str, tuple[str, tuple | None]] = {
    "output_mode": ("folder", ("folder", "cbz")),
    "model": (cfg.deepseek_model, None),
    "base_url": (cfg.deepseek_base_url, None),
    "deepseek_api_key": (cfg.deepseek_api_key, None),
    "openrouter_api_key": (cfg.openrouter_api_key, None),
    "dry_run": ("true", ("true", "false")),
}


def get_setting(db, key: str) -> str:
    """Persisted value for `key`, falling back to its config default."""
    s = db.get(Setting, key)
    return s.value if s is not None else SETTINGS[key][0]


def get_all(db) -> dict[str, str]:
    return {k: get_setting(db, k) for k in SETTINGS}


def set_setting(db, key: str, value) -> bool:
    """Persist `key` = value. Returns True on success (False = invalid/ignored)."""
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
