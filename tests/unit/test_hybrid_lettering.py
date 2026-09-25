"""v0.30.0 — the HYBRID: the engine reads the page, this app letters it.

WHY THIS EXISTS (2026-09-24)
v0.29 gave the engine the WHOLE pipeline, lettering included, and the output was
rejected on sight: text sat small inside the detected box instead of filling the balloon, and
it escaped the frame. That is upstream's renderer behaving normally — it places text in the
box; it does not fit text to a balloon. Our own typesetter does, and that is what made
v0.27.x look finished. So the split is now: the engine detects + OCRs + translates (it finds
measurably more Korean — 5.0 → 2.8 leftover chars/page over 10 pages), and our render path
draws the result.

These tests pin the two seams that make that work, and the two ways it could silently break:
  1. upstream's JSON shape → our region dicts (the parse), including its
     {language_code: text} block text map, where the SOURCE string and the translation live
     in the same dict;
  2. our region dicts → TextBlocks the typesetter accepts, INCLUDING the guard that an
     untranslated block (translation == source) is dropped rather than drawn back over
     itself;
  3. that the payload we send upstream is unchanged in shape (it is parsed by pydantic).
"""
from __future__ import annotations

from app.pipeline import engine
from app.pipeline.render import _blocks_from_engine_regions


def _upstream_block(texts: dict, box=(10, 20, 110, 220), angle=0.0, prob=0.93):
    """One block of upstream's TranslationResponse (their to_json.py shape)."""
    minX, minY, maxX, maxY = box
    return {"text": texts, "minX": minX, "minY": minY, "maxX": maxX, "maxY": maxY,
            "angle": angle, "prob": prob, "is_bulleted_list": False}


def test_upstream_json_maps_source_and_translation_apart():
    data = {"translations": [_upstream_block({"JPN": "こんにちは", "ENG": "Hello"})],
            "debug_folder": None}
    regions = engine._regions_from_engine_json(data, "ENG")
    assert len(regions) == 1
    r = regions[0]
    assert (r["x0"], r["y0"], r["x1"], r["y1"]) == (10, 20, 110, 220)
    assert r["text"] == "こんにちは"      # the source, not the translation
    assert r["translation"] == "Hello"


def test_korean_and_chinese_source_codes_are_recognised():
    for code in ("KOR", "CHS", "CHT", "JPN"):
        regions = engine._regions_from_engine_json(
            {"translations": [_upstream_block({code: "원문", "ENG": "source text"})]}, "ENG")
        assert regions and regions[0]["text"] == "원문", code


def test_blocks_without_a_translation_are_dropped_at_the_boundary():
    """Source == target means there is nothing to draw — never re-letter the source."""
    data = {"translations": [
        _upstream_block({"KOR": "번역 없음"}),                      # no target entry at all
        _upstream_block({"KOR": "그대로", "ENG": "그대로"}),          # identical — untranslated
    ]}
    assert engine._regions_from_engine_json(data, "ENG") == []


def test_engine_regions_become_letterable_blocks():
    regions = engine._regions_from_engine_json(
        {"translations": [_upstream_block({"KOR": "안녕하세요", "ENG": "Hello there"})]}, "ENG")
    blocks = _blocks_from_engine_regions(regions)
    assert len(blocks) == 1
    b = blocks[0]
    assert b.bbox == (10, 20, 100, 200)
    assert b.text == "안녕하세요"
    assert b.translation == "Hello there"
    assert b.orientation == "vertical"        # 100x200 box: taller than wide
    assert b.confidence == 0.93


def test_orientation_follows_the_box_shape_like_our_own_detector():
    wide = _blocks_from_engine_regions([{"x0": 0, "y0": 0, "x1": 200, "y1": 40,
                                         "text": "가", "translation": "A"}])
    assert wide[0].orientation == "horizontal"


def test_an_out_of_range_angle_is_ignored_not_applied():
    """Upstream reports its own angle semantics; ours is defined for ±45° only."""
    blocks = _blocks_from_engine_regions([{"x0": 0, "y0": 0, "x1": 100, "y1": 200,
                                           "text": "가", "translation": "A", "angle": -90}])
    assert blocks[0].angle == 0.0
    blocks = _blocks_from_engine_regions([{"x0": 0, "y0": 0, "x1": 100, "y1": 200,
                                           "text": "가", "translation": "A", "angle": 12.5}])
    assert blocks[0].angle == 12.5


def test_degenerate_and_malformed_regions_never_raise():
    for bad in ([{"x0": 0, "y0": 0, "x1": 0, "y1": 0, "text": "a", "translation": "b"}],
                [{"text": "a", "translation": "b"}],           # no box at all
                [{"x0": "x", "y0": 0, "x1": 5, "y1": 5, "text": "a", "translation": "b"}],
                [None], [{}]):
        assert _blocks_from_engine_regions(bad) == []


def test_blocks_are_read_in_reading_order():
    """Top-to-bottom, then right-to-left within a row — the same order
    `_build_blocks_from_det` uses, because manga is read right to left."""
    blocks = _blocks_from_engine_regions([
        {"x0": 10, "y0": 300, "x1": 110, "y1": 400, "text": "b", "translation": "B"},
        {"x0": 200, "y0": 10, "x1": 300, "y1": 110, "text": "c", "translation": "C"},
        {"x0": 10, "y0": 10, "x1": 110, "y1": 110, "text": "a", "translation": "A"},
    ])
    assert [b.translation for b in blocks] == ["C", "A", "B"]


def test_region_list_round_trips_into_the_hybrid_config_shape():
    """The regions we hand render_translated_page carry exactly the keys it reads."""
    regions = engine._regions_from_engine_json(
        {"translations": [_upstream_block({"CHS": "你好", "ENG": "Hi"})]}, "ENG")
    assert set(regions[0]) == {"x0", "y0", "x1", "y1", "text", "translation", "angle", "prob"}


# ---- the source language, and why the engine has to tell us ----------------------
# Job 29 (2026-09-24) went out BYTE-IDENTICAL with 0 blocks: the source language was on
# auto, the memory-guarded probe declined to run (2.0 GB free, needs 2.5), our render path
# fell back to "ja", and the Japanese pipeline's colour-page skip threw the whole page
# away — while the engine had read its 2 Korean regions correctly. These pin the fix.

def test_the_real_world_lowercase_key_shape_is_understood():
    """MEASURED 2026-09-24: upstream keys a Korean page's blocks {"ENG": ..., "ko": ...}.
    The first version of this map knew only uppercase codes, so it reported no language and
    a colour Korean page came back untouched (job 30). Pin the real shape."""
    data = {"translations": [_upstream_block({"ENG": "Hankyung Department Store",
                                             "ko": "한경 백화점"}),
                            _upstream_block({"ENG": "South Korea's sales scale",
                                             "ko": "대한민국매출규뫼"})]}
    assert engine.lang_from_engine_json(data, "ENG") == "ko"
    regions = engine._regions_from_engine_json(data, "ENG")
    assert regions[0]["text"] == "한경 백화점"                    # source, not the translation
    assert regions[0]["translation"] == "Hankyung Department Store"
    assert len(_blocks_from_engine_regions(regions)) == 2


def test_source_language_comes_from_the_engines_own_text_keys():
    data = {"translations": [_upstream_block({"KOR": "한경 백화점", "ENG": "Hankyung Dept"}),
                             _upstream_block({"KOR": "매출 규모", "ENG": "sales scale"})]}
    assert engine.lang_from_engine_json(data, "ENG") == "ko"
    assert engine.lang_from_engine_json(
        {"translations": [_upstream_block({"JPN": "あ", "ENG": "a"})]}, "ENG") == "ja"
    assert engine.lang_from_engine_json(
        {"translations": [_upstream_block({"CHS": "你", "ENG": "you"})]}, "ENG") == "zh"


def test_the_majority_source_language_wins():
    """A page with one misdetected block must not flip the whole page's pipeline."""
    data = {"translations": [_upstream_block({"KOR": "가", "ENG": "A"}),
                             _upstream_block({"KOR": "나", "ENG": "B"}),
                             _upstream_block({"JPN": "う", "ENG": "C"})]}
    assert engine.lang_from_engine_json(data, "ENG") == "ko"


def test_no_language_reported_stays_empty_rather_than_guessing():
    """Empty means "the caller keeps whatever it had" — never a silent ja/ko invention."""
    assert engine.lang_from_engine_json({"translations": []}, "ENG") == ""
    assert engine.lang_from_engine_json({}, "ENG") == ""
    assert engine.lang_from_engine_json({"translations": [{"text": {"ENG": "hi"}}]}, "ENG") == ""
