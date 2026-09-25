"""Engine endpoints — status, and the option lists the Settings UI renders.

    GET /api/engine/status   url, switch, live probe, and the resolved request config
    GET /api/engine/options  detectors, resolutions, directions, inpainters, languages
    GET /api/engine/probe    live liveness probe only (the "Test connection" button)

The probe never raises and never blocks for long: the Settings page must render and stay
usable when the engine is down, and the badge has to say WHY.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.pipeline import engine
from app.settings_store import get_setting

router = APIRouter(prefix="/api/engine", tags=["engine"])


def _resolve(db: Session) -> None:
    """Point the client at whatever the store says (same call the job engine makes)."""
    engine.configure(url=(get_setting(db, "engine_url") or "").strip(),
                     enabled_flag=(get_setting(db, "engine_enabled") == "true"))


@router.get("/options")
def options():
    return {
        "detectors": [{"value": v, "label": l} for v, l in engine.DETECTORS],
        "detection_sizes": engine.DETECTION_SIZES,
        "inpainters": [{"value": v, "label": l} for v, l in engine.INPAINTERS],
        "inpainting_sizes": engine.INPAINTING_SIZES,
        "render_directions": [{"value": v, "label": l} for v, l in engine.RENDER_DIRECTIONS],
        "target_languages": [{"value": v, "label": l} for v, l in engine.TARGET_LANGUAGES],
        # The measured per-source-language presets, so the Advanced tab can state what
        # they set and that they win over the manual values (app/pipeline/engine.py).
        "preset_modes": [
            {"value": "per-language", "label": "Per source language (measured)"},
            {"value": "off", "label": "Off — use the values below for every language"},
        ],
        # Who draws the translated text (v0.30.0). "ours" is the hybrid: the engine
        # reads/translates and this app letters. See app/pipeline/render.py.
        "lettering_modes": [
            {"value": "ours", "label": "Our typesetter (fits the balloon, fills it)"},
            {"value": "upstream", "label": "Engine's own renderer"},
        ],
        # Second pass (v0.30.1): our detector tops up the regions the engine missed.
        "second_pass_modes": [
            {"value": "on", "label": "On — also translate text the engine missed"},
            {"value": "off", "label": "Off — use the engine's regions only"},
        ],
        "presets": [
            {"lang": lang, "name": engine.PRESET_NAMES.get(lang, lang), "values": values}
            for lang, values in engine.PRESETS.items()
        ],
        "defaults": {
            "detector": engine.DEFAULT_CONFIG["detector"]["detector"],
            "detection_size": engine.DEFAULT_CONFIG["detector"]["detection_size"],
            "box_threshold": engine.DEFAULT_CONFIG["detector"]["box_threshold"],
            "unclip_ratio": engine.DEFAULT_CONFIG["detector"]["unclip_ratio"],
            "render_direction": engine.DEFAULT_CONFIG["render"]["direction"],
            "inpainter": engine.DEFAULT_CONFIG["inpainter"]["inpainter"],
            "inpainting_size": engine.DEFAULT_CONFIG["inpainter"]["inpainting_size"],
            "mask_dilation_offset": engine.DEFAULT_CONFIG["mask_dilation_offset"],
            "target_lang": engine.DEFAULT_CONFIG["translator"]["target_lang"],
        },
    }


@router.get("/probe")
def probe(db: Session = Depends(get_db)):
    _resolve(db)
    return engine.health()


@router.get("/status")
def status(db: Session = Depends(get_db)):
    _resolve(db)
    url = engine.base_url()
    on = get_setting(db, "engine_enabled") == "true"
    h = engine.health() if url else {"ok": False, "queue": None, "detail": "no engine url configured"}
    return {
        "url": url,
        "enabled": on,
        "active": engine.enabled(),
        "reachable": bool(h.get("ok")),
        "queue": h.get("queue"),
        "detail": h.get("detail"),
        "config": engine.build_config({
            "detector": get_setting(db, "engine_detector"),
            "detection_size": get_setting(db, "engine_detection_size"),
            "box_threshold": get_setting(db, "engine_box_threshold"),
            "unclip_ratio": get_setting(db, "engine_unclip_ratio"),
            "render_direction": get_setting(db, "engine_render_direction"),
            "inpainter": get_setting(db, "engine_inpainter"),
            "inpainting_size": get_setting(db, "engine_inpainting_size"),
            "mask_dilation_offset": get_setting(db, "engine_mask_dilation_offset"),
            "target_lang": get_setting(db, "target_lang"),
        }),
    }
