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
from app.pipeline.engine import TARGET_LANGUAGES
from app.pipeline.fonts import FONT_CATALOG, default_font_id

_DEVICE_CHOICES = ("auto", "cpu", "cuda")
_SOURCE_LANG_CHOICES = ("auto", "ja", "ko", "zh")

SETTINGS: dict[str, tuple[str, tuple | None]] = {
    "output_mode": ("folder", ("folder", "cbz", "mirror")),
    "dry_run": ("true", ("true", "false")),
    "font": (default_font_id() or "anime-ace", tuple(f["id"] for f in FONT_CATALOG)),
    "device": (cfg.device if cfg.device in _DEVICE_CHOICES else "auto", _DEVICE_CHOICES),
    "gpu_worker_url": ("", None),  # e.g. http://gpu-host:9001 (remote vision worker)
    "source_lang": ("auto", _SOURCE_LANG_CHOICES),  # auto | ja | ko | zh
    # Translation-provider resilience. Lax by default: a degraded provider can
    # take minutes to answer, so give a slow-but-alive call room. Only repeated
    # failures (a real outage) stop the job, loudly, with a visible error.
    "llm_timeout": ("300", None),        # seconds to wait for one LLM request
    "llm_max_retries": ("2", None),      # retries per request on a transient error
    "provider_fail_limit": ("3", None),  # consecutive failed pages before stopping the job
    # Narrow relaxation of the horizontal-text gate. A pure-horizontal page (cover,
    # credits, colophon, or a splash page whose only text is a 【…】 name-tag) is
    # normally left in Japanese; with this ON, the configured model READS the crop and
    # translates only IN-WORLD captions/name-tags — job-3 p159's 【給仕 キーマ】 is
    # "[SERVER: KEEMA]" in the commercial release while we produced nothing. Credits,
    # titles and logos are still left alone (see app/pipeline/captions.py). One vision
    # call per candidate caption, and only on those pages.
    "caption_vlm": ("true", ("true", "false")),

    # ---- ENGINE (option A: upstream runs the pipeline, this app is the shell) -----
    # Empty URL ⇒ the app uses its OWN in-process pipeline (pre-v0.28 behaviour), so a
    # fresh install keeps working with no engine present. Point it at the engine
    # container (e.g. http://your-engine-host:8000) and flip the switch to hand pages over.
    "engine_url": (cfg.engine_url, None),
    "engine_enabled": ("true" if cfg.engine_enabled else "false", ("true", "false")),

    # ---- Advanced tab: upstream's knobs, defaults = the values this app always used --
    # Nothing below changes behaviour until it is edited; the UI groups them under
    # Advanced so the main flow stays as simple as it was.
    "engine_detector": ("default", ("default", "ctd", "paddle")),
    "engine_detection_size": ("1536", ("1024", "1536", "2048", "2560")),
    "engine_box_threshold": ("0.7", None),
    "engine_unclip_ratio": ("2.3", None),
    "engine_render_direction": ("auto", ("auto", "horizontal", "vertical")),
    "engine_inpainter": ("default", ("default", "lama_large", "lama_mpe", "sd", "none", "original")),
    "engine_inpainting_size": ("1024", ("516", "1024", "2048", "2560")),
    "engine_mask_dilation_offset": ("30", None),
    # Measured per-source-language presets (see app/pipeline/engine.py PRESETS for the
    # six-arm sweep behind them). "per-language" applies the measured values for the
    # languages that have one — Korean only, today — and they WIN over the Advanced
    # values above; "off" leaves the Advanced tab in full manual control. The chosen
    # preset is named in the job's lifecycle log line.
    "engine_preset_mode": ("per-language", ("per-language", "off")),
    # WHO DRAWS THE TRANSLATED TEXT (v0.30.0, option 1). "ours" = hybrid: the
    # engine detects/OCRs/translates and THIS app letters the result with its own
    # typesetter — the finished look (fitted to the balloon outline, filling it) that
    # upstream's renderer does not produce. "upstream" = let the engine render the page
    # itself, i.e. the v0.29 behaviour, kept as a one-click comparison.
    "engine_lettering": ("ours", ("ours", "upstream")),
    # SECOND PASS (v0.30.1). The engine only reports what it TRANSLATED, so text its
    # detector missed — the tail of a line, vertical kana — is never erased or lettered and
    # stays visible under our English. With this on, our own detector finds those regions and
    # our LLM translates them (a few blocks per page, only on engine jobs). Our OCR is
    # memory-hungry, so the pass is skipped automatically when free memory is low.
    #
    # DEFAULT ON (v0.30.5). The three failures below were NOT in this pass — they were in
    # render.py's `_merge_blocks_per_bubble`, which rebuilt a merged block from its source text
    # alone and so DISCARDED the engine's translation. Any second-pass region for the same plate
    # as an engine region collapsed the two into one untranslatable block: nothing erased,
    # nothing lettered, "2 blocks · 1 translated". Fixed in v0.30.5 by carrying the translations
    # through the merge (with the joined source text, in reading order) and logging the merge at
    # INFO so it is visible in the webui. Kept here as the measurement history:
    #   v0.30.1 (append the recovered box): 3 regions in → 2 blocks out, 1 translated; the
    #      Korean page's lower plate lost its English where v0.30.0 had lettered it.
    #   v0.30.2 (merge into the engine's region, combined text): the top plate came back
    #      DUPLICATED — "HAN-KYUNG DEPARTMENT STORE, HAN-KYUNG DEPARTMENT STORE".
    #   v0.30.3 (covered-vs-extends classification, our text alone): top plate clean, lower
    #      plate still unlettered — same block loss, which is what pointed at render.py.
    # Turn it off to reproduce the v0.30.0 behaviour (engine regions only, no recovery).
    "engine_second_pass": ("on", ("on", "off")),
    # Output language. English is the default (upstream's own default is ENG too) and
    # the whole list is upstream's, so a job can target any of them.
    "target_lang": ("ENG", tuple(code for code, _ in TARGET_LANGUAGES)),
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

def mask_api_key(key: str | None) -> str:
    """Show enough of a key to recognise it, never enough to use it (v0.27.16).

    `GET /api/models` used to return the raw key, which put it in the DOM of any
    browser that opened Settings (and in any screenshot of it). The API now
    returns this mask plus an `api_key_set` flag; the editor treats an empty or
    masked value as "keep the stored key".
    """
    k = (key or "").strip()
    if not k:
        return ""
    if len(k) <= 8:
        return "•" * len(k)
    return f"{k[:6]}…{k[-4:]}"


def is_masked_key(value: str | None) -> bool:
    """True if `value` is a mask (or empty) rather than a real key to store."""
    v = (value or "").strip()
    return (not v) or ("…" in v) or ("•" in v)


def _model_dict(m: Model) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "base_url": m.base_url,
        "api_key": mask_api_key(m.api_key),
        "api_key_set": bool((m.api_key or "").strip()),
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
    # The editor is shown the MASKED key, so an empty or masked value means
    # "leave the stored key alone" — echoing the mask back must never overwrite
    # a working key with "sk-a2a…3f9d" (v0.27.16).
    new_key = fields.pop("api_key", None)
    for k, v in fields.items():
        if hasattr(m, k):
            setattr(m, k, v)
    if new_key is not None and not is_masked_key(new_key):
        m.api_key = new_key.strip()
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
