"""v0.29.1 — per-source-language engine presets, and the OFF switch that undoes them.

WHY THE VALUES ARE THESE VALUES (measured 2026-09-24, not guessed)
A six-arm sweep, 10 pages per language per arm, scored by leftover source characters per
page (lower is better):

    arm (detector/detection_size/box_threshold/inpainting)   ja    ko    zh
    A  default 1536 0.7 inpaint 1024  (= what we shipped)    7.9   5.0   4.2
    C  default 2560 0.6 inpaint 1024                         7.6   2.8   4.2
    F  default 2560 0.6 inpaint 2048                         6.9   2.8   3.9
    D  ctd     ---  ---  inpaint 1024                        7.4   2.7  14.2  <- trap on Chinese

Korean is the only language with a large, reproducible win (−44%), and C, D and F all
agree on it; F's identical ko score for 26% more time is why the preset takes C's detector
values and leaves inpainting alone. ja/zh stay on the defaults by project decision
(2026-09-24): ja's gain is small, and ~60% of zh's "leftover text" is a site watermark
(腾讯动漫) that no setting can translate, so zh differences are noise.

These tests pin the *contract*: what the preset sets, that it wins over the Advanced
values while presets are on, that `off` restores today's behaviour exactly, and that
nothing extra leaks into the payload upstream parses.
"""
from __future__ import annotations

from app.pipeline import engine

# Exactly what the Settings UI sends: every value on its shipped default.
ADVANCED = {
    "detector": "default",
    "detection_size": "1536",
    "box_threshold": "0.7",
    "unclip_ratio": "2.3",
    "render_direction": "auto",
    "inpainter": "default",
    "inpainting_size": "1024",
    "mask_dilation_offset": "30",
    "target_lang": "ENG",
}


def _cfg(lang: str | None, mode: str = "per-language", advanced: dict | None = None) -> dict:
    return engine.build_config(advanced if advanced is not None else ADVANCED,
                               source_lang=lang, preset_mode=mode)


def test_korean_gets_the_measured_detector_values():
    cfg = _cfg("ko")
    assert cfg["detector"]["detection_size"] == 2560
    assert cfg["detector"]["box_threshold"] == 0.6
    # The preset deliberately leaves inpainting alone: F scored the same on ko for 26% more
    # time per page, so 2048 would buy nothing here.
    assert cfg["inpainter"]["inpainting_size"] == 1024
    assert cfg["detector"]["detector"] == "default"       # never CTD: it is a trap on zh
    assert engine.preset_name("ko") != ""


def test_japanese_and_chinese_keep_the_long_standing_defaults():
    for lang in ("ja", "zh"):
        cfg = _cfg(lang)
        assert cfg["detector"]["detection_size"] == 1536
        assert cfg["detector"]["box_threshold"] == 0.7
        assert cfg["inpainter"]["inpainting_size"] == 1024
        assert engine.preset_name(lang) == ""


def test_preset_mode_off_restores_todays_behaviour_for_every_language():
    for lang in ("ja", "ko", "zh", "", "auto"):
        cfg = _cfg(lang, mode="off")
        assert cfg == engine.build_config(ADVANCED), f"{lang!r} was not left alone with presets off"


def test_manual_advanced_values_still_apply_when_presets_are_off():
    adv = dict(ADVANCED, detection_size="2560", box_threshold="0.5", inpainting_size="2048")
    cfg = _cfg("ko", mode="off", advanced=adv)
    assert cfg["detector"]["detection_size"] == 2560
    assert cfg["detector"]["box_threshold"] == 0.5
    assert cfg["inpainter"]["inpainting_size"] == 2048


def test_preset_wins_over_the_advanced_values_while_it_is_on():
    """Documented behaviour, not an accident: the UI says presets override the values below."""
    adv = dict(ADVANCED, detection_size="1024", box_threshold="0.9")
    cfg = _cfg("ko", advanced=adv)
    assert cfg["detector"]["detection_size"] == 2560
    assert cfg["detector"]["box_threshold"] == 0.6


def test_an_undetected_language_never_invents_a_preset():
    """Engine mode with auto-detect and a failed probe sends "" — that must be the defaults."""
    for lang in ("", None, "auto", "xx"):
        assert engine.preset_for(lang) == {}
        assert _cfg(lang) == engine.build_config(ADVANCED)


def test_payload_shape_is_unchanged_for_upstream():
    """Upstream parses this with a pydantic model: no extra keys, no source_lang."""
    base = engine.build_config(ADVANCED)
    for lang in ("ko", "ja"):
        cfg = _cfg(lang)
        assert sorted(cfg) == sorted(engine.DEFAULT_CONFIG) == sorted(base)
        assert "source_lang" not in cfg
        assert sorted(cfg["detector"]) == sorted(base["detector"])


def test_defaults_are_not_mutated_by_building_a_preset_config():
    """DEFAULT_CONFIG is module state; a preset must never write into it."""
    before = engine.build_config(ADVANCED)
    engine.build_config(ADVANCED, source_lang="ko", preset_mode="per-language")
    assert engine.build_config(ADVANCED) == before
    assert engine.DEFAULT_CONFIG["detector"]["detection_size"] == 1536
