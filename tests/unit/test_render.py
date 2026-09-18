"""Unit tests for render helpers (pure functions — no model load)."""
import numpy as np
from PIL import Image

from app.pipeline.render import (
    _box_containment,
    _dedup_blocks,
    _dedup_boxes,
    _drop_non_japanese,
    _drop_titles,
    _has_chapter_heading,
    _iou,
    _is_blank,
    _is_color,
    _orientation,
    _split_bullet_lines,
)


def test_orientation_classifies_by_shape():
    assert _orientation(100, 30) == "horizontal"   # wide stat line
    assert _orientation(50, 60) == "horizontal"    # near-square
    assert _orientation(40, 200) == "vertical"     # tall name column
    assert _orientation(10, 40) == "furigana"      # narrow ruby column


def test_drop_low_confidence_never_drops_unknown_or_legit():
    from app.pipeline.render import _drop_low_confidence

    # manga-ocr (ja) emits no confidence -> never dropped by the confidence gate.
    assert _drop_low_confidence(None) is False
    # Legit PaddleOCR reads (zh/ko) sit well above the threshold.
    assert _drop_low_confidence(0.999) is False
    assert _drop_low_confidence(0.895) is False
    assert _drop_low_confidence(0.51) is False
    # Exactly at the threshold is kept (only strictly-below is dropped).
    assert _drop_low_confidence(0.5) is False


def test_drop_low_confidence_drops_garbage_sfx():
    from app.pipeline.render import _drop_low_confidence

    # Misread SFX / decorative glyphs / garbled signs read below 0.5.
    assert _drop_low_confidence(0.37) is True   # 阿大奥色狂
    assert _drop_low_confidence(0.11) is True   # 福
    assert _drop_low_confidence(0.473) is True  # 房育院院司民房院房理司


def test_inset_box_shrinks_edges():
    from app.pipeline.render import _inset_box

    bbox = (100, 100, 200, 200)
    ins = _inset_box(bbox)
    # 15% width / 12% height inset -> narrower + shorter, still centered.
    assert ins[0] > bbox[0] and ins[1] > bbox[1]
    assert ins[2] < bbox[2] and ins[3] < bbox[3]
    assert ins[2] == 200 - 2 * int(200 * 0.15)
    assert ins[3] == 200 - 2 * int(200 * 0.12)


def test_inset_box_does_not_collapse_tiny_box():
    from app.pipeline.render import _inset_box

    tiny = (0, 0, 8, 8)  # 15%/12% inset drops a dimension below the 8px floor
    assert _inset_box(tiny) == tiny


def test_expand_box_grows_around_center():
    from app.pipeline.render import _expand_box

    bbox = (100, 100, 100, 100)
    ex = _expand_box(bbox)
    assert ex[0] < bbox[0] and ex[1] < bbox[1]
    assert ex[2] > bbox[2] and ex[3] > bbox[3]
    assert ex[2] == 100 + 2 * int(100 * 0.25)


def test_expand_box_clamps_to_origin():
    from app.pipeline.render import _expand_box

    bbox = (0, 0, 100, 100)
    ex = _expand_box(bbox)
    assert ex[0] >= 0 and ex[1] >= 0


def test_caption_region_widens_and_tallens_wide_caption():
    from app.pipeline.render import _caption_region

    # A 935x56 footnote: the old _expand_box gave a 1401x78 region that crushed
    # the font vertically. The caption region must be wide AND tall enough for
    # ~3 wrapped lines, and stay on-page.
    x, y, w, h = _caption_region((173, 1814, 935, 56), 1600, 2262)
    assert w >= 935            # at least the caption's own width
    assert h >= int(2262 * 0.06)  # page-proportional height floor (~3 lines)
    assert x >= 0 and x + w <= 1600  # on-page


def test_caption_region_centers_on_vertical_caption():
    from app.pipeline.render import _caption_region

    # A tall vertical caption (问世间情为何物, 72x461) must get a WIDE strip (English
    # is horizontal) roughly centered on the original text's vertical position —
    # but bounded to the source column's OWN footprint (2.5x its width + 24px).
    # Before that bound it was lettered across a 691px strip and ran into the
    # neighbouring panel's balloon (job-4 page 10: two texts on top of each other).
    x, y, w, h = _caption_region((754, 376, 72, 461), 1600, 2262)
    assert w >= int(72 * 2.5)   # wide enough for horizontal English
    assert w <= int(h * 1.5)    # ...but never wider than the source's own length
    # strip's vertical centre is near the original text's centre
    assert abs((y + h // 2) - (376 + 461 // 2)) < h // 2 + 1


def test_caption_region_short_vertical_label_not_page_wide():
    from app.pipeline.render import _caption_region

    # A SHORT vertical label (a 2-char 大吉 on the fortune stone, 97x173) must NOT
    # blow up to a 60%-page-wide strip (which would typeset "Great fortune" at an
    # enormous font). Width tracks the text length instead.
    x, y, w, h = _caption_region((225, 621, 97, 173), 1600, 2262)
    assert w < int(1600 * 0.6)
    assert w >= 97


def test_caption_region_clamps_to_page_edge():
    from app.pipeline.render import _caption_region

    # A caption near the right edge must not spill off-page.
    x, y, w, h = _caption_region((1500, 100, 200, 40), 1600, 2262)
    assert x + w <= 1600


def test_box_containment_nested():
    # 20x20 box fully inside a 100x100 box: containment is 1.0.
    assert _box_containment((0, 0, 100, 100), (10, 10, 20, 20)) == 1.0
    assert _box_containment((0, 0, 10, 10), (50, 50, 10, 10)) == 0.0


def test_dedup_drops_nested_and_overlapping():
    # A whole column plus its sub-fragments collapses to the largest box.
    boxes = [
        (10, 10, 20, 200),   # full column
        (10, 30, 20, 120),   # nested fragment
        (12, 12, 16, 80),    # nested fragment
        (10, 10, 20, 50),    # nested fragment
    ]
    kept = _dedup_boxes(boxes, seen=[])
    assert kept == [(10, 10, 20, 200)]


def test_dedup_keeps_distinct_boxes():
    boxes = [(0, 0, 50, 50), (100, 0, 50, 50), (0, 100, 50, 50)]
    kept = _dedup_boxes(boxes, seen=[])
    assert sorted(kept) == sorted(boxes)


def test_dedup_keep_smallest_drops_container_keeps_lines():
    # Horizontal stat text: the detector returns a whole stat box (container) plus
    # its individual lines. keep="smallest" keeps the lines, drops the container.
    boxes = [
        (0, 0, 400, 300),    # whole stat box
        (10, 10, 200, 40),   # line 1
        (10, 60, 200, 40),   # line 2
        (10, 110, 150, 40),  # line 3
    ]
    kept = _dedup_boxes(boxes, seen=[], keep="smallest")
    assert (0, 0, 400, 300) not in kept
    assert (10, 10, 200, 40) in kept
    assert (10, 60, 200, 40) in kept
    assert (10, 110, 150, 40) in kept


def test_device_resolve():
    from app.pipeline import device as d

    assert d.resolve("cpu") == "cpu"
    # auto/cuda resolve to "cuda" only when a CUDA torch is present; otherwise
    # they degrade to "cpu" — never an invalid value.
    assert d.resolve("auto") in ("cpu", "cuda")
    assert d.resolve("cuda") in ("cpu", "cuda")
    assert d.resolve("") in ("cpu", "cuda")


def test_is_blank_and_color():
    white = np.full((10, 10, 3), 255, dtype=np.uint8)
    assert _is_blank(white) is True

    art = np.zeros((10, 10, 3), dtype=np.uint8)  # black page
    assert _is_blank(art) is False

    gray_img = Image.fromarray(np.full((10, 10, 3), 128, dtype=np.uint8))
    assert _is_color(gray_img) is False

    red = np.zeros((10, 10, 3), dtype=np.uint8)
    red[:, :, 0] = 255  # pure red — full chroma
    assert _is_color(Image.fromarray(red)) is True


def test_drop_non_japanese_filters_english():
    from app.pipeline.types import TextBlock

    jp = TextBlock(bbox=(0, 0, 10, 30), text="こんにちは", orientation="vertical")
    mixed = TextBlock(bbox=(0, 0, 10, 30), text="ジョン John", orientation="vertical")
    en = TextBlock(bbox=(0, 0, 40, 12), text="CONTENTS", orientation="horizontal")
    empty = TextBlock(bbox=(0, 0, 10, 10), text="", orientation="horizontal")

    kept = _drop_non_japanese([jp, mixed, en, empty])
    assert jp in kept       # Japanese -> translate
    assert mixed in kept    # has kana -> translate
    assert en not in kept   # already-English -> leave untouched
    assert empty not in kept


def test_drop_corner_watermarks_skips_publisher_mark():
    from app.pipeline.render import _drop_corner_watermarks
    from app.pipeline.types import TextBlock

    # 腾讯动漫 in the bottom-right corner is a publisher watermark — skip it. A
    # footnote higher up the page is real text — keep it.
    wm = TextBlock(bbox=(1390, 2185, 203, 61), text="腾讯动漫", orientation="horizontal")
    footnote = TextBlock(bbox=(173, 1814, 935, 56), text="*注", orientation="horizontal")
    kept = _drop_corner_watermarks([wm, footnote], 1600, 2262)
    assert wm not in kept
    assert footnote in kept


def test_dedup_blocks_drops_nested_duplicates():
    from app.pipeline.types import TextBlock

    # The same region detected whole + a nested sub-region -> keep the largest.
    whole = TextBlock(bbox=(100, 100, 300, 200), text="A B C D", orientation="vertical")
    sub = TextBlock(bbox=(110, 110, 280, 180), text="A B C", orientation="vertical")
    distinct = TextBlock(bbox=(500, 100, 100, 50), text="Z", orientation="horizontal")

    kept = _dedup_blocks([whole, sub, distinct])
    assert whole in kept        # largest kept
    assert sub not in kept      # nested duplicate dropped
    assert distinct in kept     # non-overlapping kept


def test_drop_titles_skips_large_text():
    from app.pipeline.types import TextBlock

    title = TextBlock(bbox=(0, 0, 800, 360), text="月が導く異世界道中", orientation="vertical")
    header = TextBlock(bbox=(0, 500, 500, 300), text="学園生徒の能力チェック", orientation="vertical")
    bio = TextBlock(bbox=(0, 900, 300, 170), text="あいうえお", orientation="vertical")
    # A tall-narrow bio PARAGRAPH: many glyphs for its area (high density) -> kept.
    tall_bio = TextBlock(bbox=(0, 1100, 300, 400), text="長い説明文が入る" * 8, orientation="vertical")
    kept = _drop_titles([title, header, bio, tall_bio], page_h=1600)
    assert title not in kept     # wide + tall -> title skipped
    assert header not in kept    # wide + tall -> header skipped
    assert bio in kept           # short -> kept
    assert tall_bio in kept      # tall but narrow + glyph-dense -> bio kept


def test_drop_titles_skips_vertical_brush_title():
    """A large vertical brush title (few glyphs for its area) is title art —
    manga-ocr misreads it (job-3 p187 月夜に提灯 -> 提出), so leave it as-is."""
    from app.pipeline.types import TextBlock

    # 379x437 = 165,623 px2, 5 glyphs -> 0.3 glyphs/10k px2 (the real p187 title).
    brush_title = TextBlock(bbox=(604, 142, 379, 437), text="月夜に提灯", orientation="vertical")
    # A big vertical dialogue column: 179x486 = 86,994 px2, 13 glyphs -> 1.49 -> kept.
    dialogue = TextBlock(bbox=(98, 187, 179, 486), text="全身の血の気が引く音がした", orientation="vertical")
    kept = _drop_titles([brush_title, dialogue], page_h=1600)
    assert brush_title not in kept   # large + sparse -> title art skipped
    assert dialogue in kept          # big but glyph-dense -> dialogue kept


def test_partition_shared_bubble_splits_stacked_balloons():
    """Two vertically-stacked balloons whose regions overlap get split at the
    midpoint of the shared band, so their centred text can't collide (job-3 p45)."""
    from app.pipeline.render import _partition_shared_bubble
    from app.pipeline.types import TextBlock

    upper = TextBlock(bbox=(872, 102, 122, 179), text="次の講義", orientation="vertical")
    lower = TextBlock(bbox=(773, 153, 105, 220), text="二度目の限界", orientation="vertical")
    # overlapping regions (upper's bottom reaches into lower's top)
    targets = [
        (upper, (847, 83, 173, 229)),   # y 83..312
        (lower, (748, 130, 159, 269)),  # y 130..399
    ]
    _partition_shared_bubble(targets)
    (b1, r1), (b2, r2) = targets
    # the upper region must end where the lower begins (no shared band)
    assert r1[1] + r1[3] <= r2[1] or r2[1] + r2[3] <= r1[1]
    # both keep their full width
    assert r1[2] == 173 and r2[2] == 159
    # both stay within the original union
    assert r1[1] >= 83 and r2[1] + r2[3] <= 399


def test_partition_shared_bubble_leaves_nestling_balloons_alone():
    """Balloons that merely nestle (a few px of rounded corner) are NOT split —
    their centred text never reaches the overlap."""
    from app.pipeline.render import _partition_shared_bubble
    from app.pipeline.types import TextBlock

    a = TextBlock(bbox=(100, 100, 100, 200), text="あ", orientation="vertical")
    b = TextBlock(bbox=(300, 250, 100, 200), text="い", orientation="vertical")
    ra = (90, 90, 150, 220)   # y 90..310
    rb = (290, 290, 150, 220)  # y 290..510  -> 20px vertical overlap, ~9% of 220
    targets = [(a, ra), (b, rb)]
    _partition_shared_bubble(targets)
    assert targets[0][1] == ra and targets[1][1] == rb  # untouched


def test_partition_shared_bubble_ignores_non_overlapping():
    from app.pipeline.render import _partition_shared_bubble
    from app.pipeline.types import TextBlock

    a = TextBlock(bbox=(100, 100, 100, 200), text="あ", orientation="vertical")
    b = TextBlock(bbox=(100, 500, 100, 200), text="い", orientation="vertical")
    targets = [(a, (90, 90, 150, 220)), (b, (90, 490, 150, 220))]
    _partition_shared_bubble(targets)
    assert targets[0][1] == (90, 90, 150, 220)
    assert targets[1][1] == (90, 490, 150, 220)


def test_split_bullet_lines_splits_stat_columns():
    from app.pipeline.types import TextBlock

    b = TextBlock(bbox=(100, 100, 200, 120), text="●筋力Ｂ＋●持久力Ｂ●防御技術Ｂ", orientation="vertical")
    out = _split_bullet_lines([b])
    assert [o.text for o in out] == ["筋力Ｂ＋", "持久力Ｂ", "防御技術Ｂ"]
    assert all(o.orientation == "horizontal" for o in out)


def test_split_bullet_lines_keeps_non_bullet():
    from app.pipeline.types import TextBlock

    b = TextBlock(bbox=(0, 0, 100, 50), text="こんにちは", orientation="vertical")
    assert _split_bullet_lines([b]) == [b]


def test_has_chapter_heading_detects_toc_and_chapter_pages():
    from app.pipeline.types import TextBlock

    # TOC / chapter-title pages carry a 第N話/章 marker.
    toc = [TextBlock(bbox=(0, 0, 100, 30), text="第百五話", orientation="horizontal"),
           TextBlock(bbox=(0, 40, 100, 30), text="包囲網", orientation="horizontal")]
    assert _has_chapter_heading(toc) is True

    # Arabic numerals and other counters also count.
    assert _has_chapter_heading([TextBlock(bbox=(0, 0, 100, 30), text="第105話", orientation="horizontal")]) is True
    assert _has_chapter_heading([TextBlock(bbox=(0, 0, 100, 30), text="第1章", orientation="horizontal")]) is True

    # Cover/credit/title pages carry no chapter number -> not a TOC.
    credits = [TextBlock(bbox=(0, 0, 100, 30), text="原作 あずみ圭", orientation="horizontal"),
               TextBlock(bbox=(0, 40, 100, 30), text="漫画 木野コトラ", orientation="horizontal")]
    assert _has_chapter_heading(credits) is False


def test_merge_stacked_lines_groups_columns():
    from app.pipeline.render import _merge_stacked_lines

    boxes = [
        ((100, 100, 200, 40), "line1", 0.9, 0.0),
        ((100, 145, 200, 40), "line2", 0.9, 0.0),
        ((100, 190, 200, 40), "line3", 0.9, 0.0),
        ((400, 100, 200, 40), "other", 0.9, 0.0),   # separate column (no x-overlap)
    ]
    merged = _merge_stacked_lines(boxes)
    assert len(merged) == 2
    assert merged[0][1] == "line1line2line3"   # stacked lines joined top-to-bottom
    assert merged[1][1] == "other"             # other column stays separate


def test_merge_stacked_lines_keeps_single():
    from app.pipeline.render import _merge_stacked_lines

    boxes = [((100, 100, 200, 40), "solo", 0.9, 0.0)]
    assert _merge_stacked_lines(boxes) == boxes


def test_merge_stacked_lines_merges_overlapping_fragments():
    from app.pipeline.render import _merge_stacked_lines

    # Adjacent OCR line fragments can overlap by ~20-30% of their height (box
    # padding) — a multi-line narration must still merge into one block, not
    # three separate "floating" translations.
    boxes = [
        ((622, 463, 165, 69), "资料上虽", 1.0, 0.0),
        ((616, 512, 163, 66), "然是怎么", 1.0, 0.0),
        ((611, 561, 163, 62), "写的……", 0.98, 0.0),
    ]
    merged = _merge_stacked_lines(boxes)
    assert len(merged) == 1
    assert merged[0][1] == "资料上虽然是怎么写的……"


def test_merge_stacked_lines_keeps_separate_bubbles():
    from app.pipeline.render import _merge_stacked_lines

    # A tall bubble (5 tightly-stacked fragments) followed by a SEPARATE bubble
    # ~100px below must NOT merge. The old `0.6 * max(h, bh)` gap threshold
    # scaled with the ACCUMULATED block height, so the grown block (220px) let a
    # 100px inter-bubble gap pass (0.6*220=132) and greedily absorbed the next
    # bubble — three manhua bubbles became one giant floating text block.
    boxes = [
        ((100, 100, 200, 40), "a1", 1.0, 0.0),
        ((100, 145, 200, 40), "a2", 1.0, 0.0),
        ((100, 190, 200, 40), "a3", 1.0, 0.0),
        ((100, 235, 200, 40), "a4", 1.0, 0.0),
        ((100, 280, 200, 40), "a5", 1.0, 0.0),   # block A ends at y=320
        ((100, 420, 200, 40), "b1", 1.0, 0.0),   # gap 100px -> separate bubble
    ]
    merged = _merge_stacked_lines(boxes)
    assert len(merged) == 2
    assert merged[0][1] == "a1a2a3a4a5"
    assert merged[1][1] == "b1"


def test_merge_horizontal_words_joins_same_line():
    from app.pipeline.render import _merge_horizontal_words

    # PaddleOCR splits a spaced Korean line into one box per word; same-line
    # words share a vertical band + small horizontal gap and must join
    # left-to-right, while a stacked line below stays separate.
    boxes = [
        ((524, 1255, 101, 52), "않아?", 1.0, 0.0),   # right word (sorted first by y)
        ((450, 1256, 80, 50), "좋지", 1.0, 0.0),      # left word, same line
        ((425, 1308, 231, 52), "도시사람들은", 0.97, 0.0),  # next line (stacked)
    ]
    merged = _merge_horizontal_words(boxes)
    assert len(merged) == 2
    assert merged[0][1] == "좋지 않아?"            # left-to-right, space-joined
    assert merged[1][1] == "도시사람들은"          # different line stays separate


def test_merge_horizontal_words_keeps_separate_bubbles():
    from app.pipeline.render import _merge_horizontal_words

    # Two side-by-side bubbles at the same y but far apart in x must NOT merge.
    boxes = [
        ((100, 500, 120, 50), "왼쪽", 1.0, 0.0),
        ((500, 500, 120, 50), "오른쪽", 1.0, 0.0),  # ~280px gap -> separate bubble
    ]
    merged = _merge_horizontal_words(boxes)
    assert len(merged) == 2
