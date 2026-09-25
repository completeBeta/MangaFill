"""In-world caption / name-tag recovery on pure-horizontal Japanese pages.

WHY THIS EXISTS
`translate_page` translates horizontal text only on a page that also carries vertical
dialogue (or a chapter heading), so a pure-horizontal page — a cover, a credits or
colophon page, a splash page whose only text is a 【…】 name-tag — is left alone. That
rule is load-bearing: v0.16.1/v0.16.2 measured the damage of running non-story pages
through inpaint+typeset (logos erased and never re-lettered, credits overlapped, the
LLM hallucinating titles). But it also drops IN-WORLD captions that a commercial
release does letter — job-3 p159's 【給仕 キーマ】 is "[SERVER: KEEMA]" in the Ichigo
edition while we produced nothing at all.

So this module NARROWS the rule instead of removing it. On a pure-horizontal page it
finds blocks that could be an in-world caption and asks the **already-configured**
OpenAI-compatible endpoint (the same model/api_key/base_url as translation — nothing
new to configure, and no provider-specific UI) to read the crop and classify it. Only
a CAPTION verdict that also survives every guard below becomes a lettered block;
credits, colophon/publisher text, titles and unreadable glyphs are left exactly as
they were.

WHY A VISION READ AND NOT MORE OCR
manga-ocr is the component that fails on this text. p159 came back as THREE boxes —
`【給仕`, `キ`, and a 27-character hallucination (`つまりますのですかもしれませんでしょうか`)
— and p109's 【木工職人 キャロ】 as `職人キャ` plus 40 characters of nonsense. Measured
2026-09-05 and again 2026-09-20: upscaling the crop does not help (manga-ocr
normalises text height internally), padding helps only marginally, and forcing
horizontal translation on those pages breaks titles outright (あずみ圭 → "AH, THE
MASTER OF THE MOUNTAIN"). A vision read of the crop is the only approach that works;
it was verified against the live prod endpoint (`{"jp": "【給仕 キーマ】", "en":
"Waiter Kima", "kind": "CAPTION"}`) before this module was written.

GUARDS — each one earns its place (see tests/unit/test_captions.py)
 * candidate : horizontal, short, CJK, not tall, no credit marker. The pages this
               rule exists to protect (p3 cover credits, p192 colophon) must never
               even reach the model.
 * cluster   : the OCR box detector splits ONE caption across several boxes, so
               neighbouring candidates are unioned and read as a single crop.
 * capacity  : a reading longer than the box can PHYSICALLY hold is a hallucination
               (27 CJK glyphs do not fit a 426x37 box). This is arithmetic measured
               from the ink, because the ja path carries no confidence at all to
               threshold (0 of 2627 job-3 blocks).
 * kind      : only CAPTION is accepted; TITLE / CREDITS / NONE are rejects.
 * clean     : the English must survive `translate._clean_translation` (no CJK echo,
               no placeholder marker such as "[untranslatable]").
 * geometry  : the block that gets erased and lettered is the GLYPH-SCALE ink inside
               the cluster, not the padded union — p159's caption sits above a
               diamond-pattern decorative rule and erasing that band would damage the
               art (the same principle as v0.27.14's stroke-level erase).
 * failure   : every error path returns the page untouched. A provider error stops
               the feature for the rest of the page and is logged, never raised.

NOTHING IN HERE MAY BREAK A RENDER — the same design rule as `fitlog`.
"""
from __future__ import annotations

import base64
import io
import json
import re

import numpy as np
from PIL import Image

from .translate import ProviderError, _chat, _clean_translation, _has_cjk
from .types import TextBlock

CAPTION_MAX_CHARS = 60      # geometry contributions; longer than this is prose, not a caption
CLUSTER_MAX_CHARS = 60      # a cluster's combined reading; prose pages never qualify
CAPTION_MAX_H = 130         # px — taller blocks are panels of prose
CAPTION_PAD = 12            # px of context around a cluster for the vision read
CAPTION_MAX_TOKENS = 800    # room for a reasoning model plus the JSON answer
MIN_INK_AREA = 20           # px — smaller components are texture/screentone
WIDTH_TOL = 1.25            # allowance on measured glyph capacity (kerning, half-widths)

# Publisher/author/cover text. Any of these anywhere in a reading disqualifies it:
# this is the cheap, deterministic half of "don't letter the credits page".
_CREDIT_MARKERS = (
    "発行", "出版社", "株式会社", "レーベル", "定価", "印刷", "著者", "原作",
    "漫画", "キャラクター原案", "翻訳", "編集", "デザイン", "協力",
    "©", "(c)", "copyright", "ISBN", "●", "◆", "★", "※",
)

_PROMPT = (
    "This crop is a piece of text taken from a Japanese manga page.\n"
    "1) Read the Japanese text exactly as printed, including any brackets.\n"
    "2) Classify it as exactly one of:\n"
    "   CAPTION = an IN-WORLD caption or name/role tag that belongs to the story\n"
    "             scene: a character's name or role shown beside them, a place\n"
    "             label, lettering that is part of the artwork's world.\n"
    "   TITLE   = a work, series or chapter title, or a logo.\n"
    "   CREDITS = author, publisher, cover credit, colophon or licensing text.\n"
    "   NONE    = there is no legible Japanese text in the crop.\n"
    "3) Translate it the way an English manga release would. If it is a name or\n"
    "   role tag, format it as a bracketed label, e.g. [SERVER: KEEMA].\n"
    "Reply with JSON only, no prose:\n"
    '{"kind": "CAPTION|TITLE|CREDITS|NONE", "jp": "...", "en": "..."}'
)


# --------------------------------------------------------------------------- helpers

def _logger():
    try:
        from app.services.logging import get_logger

        return get_logger("fit")
    except Exception:  # pragma: no cover - logging must never break a render
        return None


def _log(level: str, msg: str, *args) -> None:
    try:
        log = _logger()
        if log is not None:
            getattr(log, level, log.info)(msg, *args)
    except Exception:
        pass


def has_credit_marker(text: str) -> bool:
    """True when a reading carries publisher/author/cover wording."""
    t = (text or "")
    low = t.lower()
    return any((m.lower() in low) if m.isascii() else (m in t) for m in _CREDIT_MARKERS)


# ------------------------------------------------------------------- candidate rules

def caption_candidates(blocks: list[TextBlock]) -> list[TextBlock]:
    """Blocks on this page that could contribute to an in-world caption.

    NOTE the length limit is deliberately GENEROUS (60 chars). It is not the
    acceptance test — the vision read plus `capacity()` decides that. It exists only
    to keep a paragraph of prose from being sent to the model, because a long junk
    OCR box can be essential GEOMETRY: p159's caption 【給仕 キーマ】 spans three boxes,
    and the only box covering the `ーマ` half is the 27-character hallucination. A
    tight limit here silently truncated the crop at `キ` and the caption could never
    be read (caught by test_accepted_caption_replaces_the_fragments).

    Everything that fails a test below is simply not considered, so it keeps its
    original Japanese exactly as before this feature existed.
    """
    out: list[TextBlock] = []
    for b in blocks:
        if b.orientation != "horizontal":
            continue
        text = (b.text or "").strip()
        if not text or len(text) > CAPTION_MAX_CHARS:
            continue
        if not _has_cjk(text):
            continue                      # Latin signage is not a JP caption
        if has_credit_marker(text):
            continue                      # credits/cover wording — never touch
        if b.translation:
            continue                      # already has English
        try:
            _x, _y, _w, h = (int(v) for v in b.bbox)
        except Exception:
            continue
        if h <= 0 or h > CAPTION_MAX_H:
            continue
        out.append(b)
    return out


def union_bbox(boxes, pad: int = 0) -> tuple:
    """Axis-aligned union of `(x, y, w, h)` boxes (or objects with `.bbox`)."""
    bs = []
    for b in boxes:
        bs.append(tuple(int(v) for v in (b.bbox if hasattr(b, "bbox") else b)))
    x0 = min(b[0] for b in bs)
    y0 = min(b[1] for b in bs)
    x1 = max(b[0] + b[2] for b in bs)
    y1 = max(b[1] + b[3] for b in bs)
    return (x0 - pad, y0 - pad, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad)


def _near(a: tuple, b: tuple, grow: float) -> bool:
    """True when boxes `a` and `b` overlap or sit within `grow` line-heights."""
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    slack = grow * max(ah, bh, 1)
    return not (ax0 + aw + slack < bx0 or bx0 + bw + slack < ax0
                or ay0 + ah + slack < by0 or by0 + bh + slack < ay0)


def cluster_candidates(cands: list[TextBlock]) -> list[list[TextBlock]]:
    """Group fragments of ONE caption (union-grow over neighbouring boxes).

    The detector splits a single caption into several boxes — p159's 【給仕 キーマ】
    arrived as three (two line fragments plus one long hallucination box that
    overlaps the decorative rule below the caption). Reading the cluster's union is
    what makes the crop legible to the vision model.
    """
    groups: list[list[TextBlock]] = []
    used: set[int] = set()
    for i, b in enumerate(cands):
        if i in used:
            continue
        group = [b]
        used.add(i)
        changed = True
        while changed:
            changed = False
            u = union_bbox(group, 0)
            for j, c in enumerate(cands):
                if j in used:
                    continue
                if _near(u, tuple(int(v) for v in c.bbox), 1.2):
                    group.append(c)
                    used.add(j)
                    changed = True
        groups.append(group)
    return groups


# ------------------------------------------------------------------ ink measurement

def measure_text_box(gray: np.ndarray, union: tuple) -> tuple:
    """Return (`tight bbox of glyph-scale ink`, `glyph height`) inside `union`.

    Two jobs, one measurement:
     * the block that will be erased and lettered is the caption's OWN band, so a
       decorative rule sitting under it survives (p159's caption sits above a
       diamond-pattern band that must not be erased, and the English must be centred
       on the caption, not on the band);
     * the glyph height gives the physical capacity of the region, which is what
       rejects an OCR hallucination (27 glyphs cannot occupy a 426x37 box).

    Components much shorter than the tallest ink in the crop are treated as
    decoration. Falls back to `union` when nothing can be measured.
    """
    x, y, w, h = (int(v) for v in union)
    H, W = gray.shape[:2]
    x, y = max(0, x), max(0, y)
    w, h = max(1, min(w, W - x)), max(1, min(h, H - y))
    fallback = ((x, y, w, h), max(1, h))
    try:
        import cv2
    except Exception:  # pragma: no cover
        return fallback
    crop = gray[y:y + h, x:x + w]
    if crop.size == 0:
        return fallback
    ink = (crop < 140).astype(np.uint8)
    n, lab, st, _ = cv2.connectedComponentsWithStats(ink, 8)
    comps = []
    for i in range(1, n):
        cw, ch, ca = int(st[i, 2]), int(st[i, 3]), int(st[i, 4])
        if ca >= MIN_INK_AREA:
            comps.append((cw, ch, ca, i))
    if not comps:
        return fallback
    hmax = max(c[1] for c in comps)
    keep = [c[3] for c in comps if c[1] >= 0.5 * hmax]
    m = np.isin(lab, keep)
    ys, xs = np.nonzero(m)
    if ys.size == 0:
        return fallback
    pad = 4
    nx0 = max(x, x + int(xs.min()) - pad)
    ny0 = max(y, y + int(ys.min()) - pad)
    nx1 = min(x + w, x + int(xs.max()) + 1 + pad)
    ny1 = min(y + h, y + int(ys.max()) + 1 + pad)
    return (nx0, ny0, max(1, nx1 - nx0), max(1, ny1 - ny0)), max(1, hmax)


# Characters that do NOT consume a full glyph advance. A name-tag is mostly brackets,
# so counting them would make `capacity()` reject real captions. NOTE `ー` and `〜` are
# deliberately NOT here: they are width-consuming glyphs (【給仕 キーマ】 needs 5, not 4)
# — treating the katakana long-vowel mark as punctuation was a real bug caught by
# test_width_glyphs_ignores_brackets_and_spaces.
_WIDTHLESS = set(" \t\n\u3000【】「」『』（）()[]{}〈〉《》、。，．・…‥!?！？:：;；-–—_'\"”’")


def width_glyphs(text: str) -> int:
    """Count the characters that actually consume horizontal space."""
    return sum(1 for ch in (text or "") if ch not in _WIDTHLESS)


def capacity(width: int, height: int, glyph_h: int) -> int:
    """How many width-consuming glyphs can physically fit in a `width`x`height` box.

    One CJK glyph advances roughly one glyph-height horizontally and 1.15 vertically,
    with `WIDTH_TOL` slack for kerning and half-width characters. Measured on p159:
    the caption's own band (438x72 at glyph height 64) holds 8 — its reading needs 5 —
    while the 426x37 hallucination box holds 14, so a 20-glyph reading is rejected.
    """
    gh = max(1, int(glyph_h))
    lines = max(1, int(round(max(1, int(height)) / (1.15 * gh))))
    return max(3, int((max(1, int(width)) / float(gh)) * WIDTH_TOL * lines))


# ----------------------------------------------------------------------- vision read

def _parse_json(content: str) -> dict:
    """Tolerant JSON extraction — models wrap the object in fences or prose."""
    if not content:
        return {}
    s = content.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    try:
        d = json.loads(s)
        return d if isinstance(d, dict) else {}
    except Exception:
        pass
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def read_caption(image: Image.Image, union: tuple, *, model: str, api_key: str,
                 base_url: str, timeout: float | None = None) -> tuple:
    """One vision call: read + classify + translate the crop.

    Uses the app's configured translation endpoint — the same model, key and base
    URL as every other LLM call, so there is nothing new to configure. Returns
    `(parsed dict, prompt_tokens, completion_tokens)`; raises `ProviderError` from the
    shared transport so the caller can stop trying for this page.
    """
    x, y, w, h = (int(v) for v in union)
    W, H = image.size
    x, y = max(0, x), max(0, y)
    x1, y1 = min(W, x + max(1, w)), min(H, y + max(1, h))
    if x1 - x <= 1 or y1 - y <= 1:
        return {}, 0, 0
    crop = image.crop((x, y, x1, y1)).convert("RGB")
    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    payload = {
        "model": model,
        "max_tokens": CAPTION_MAX_TOKENS,
        "temperature": 0,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": _PROMPT},
                {"type": "image_url",
                 "image_url": {"url": "data:image/png;base64," + b64}},
            ],
        }],
    }
    content, pt, ct = _chat(payload, api_key, base_url, timeout or 300.0)
    return _parse_json(content), pt, ct


# --------------------------------------------------------------------------- driver

def recover_captions(image: Image.Image, blocks: list[TextBlock], *, model: str,
                     api_key: str, base_url: str, timeout: float | None = None) -> tuple:
    """Replace junk-OCR in-world captions with a vision read.

    Returns `(blocks, prompt_tokens, completion_tokens)`. The returned list is the
    SAME objects plus, for each accepted caption, one new `TextBlock` whose
    `translation` is already set — so the normal erase/typeset path letters it and no
    other stage needs to know this feature exists.

    Never raises: any failure returns the blocks untouched.
    """
    try:
        from . import fitlog
    except Exception:  # pragma: no cover
        fitlog = None
    try:
        cands = caption_candidates(blocks)
        if not cands:
            # A page with text but no caption CANDIDATES is a legitimate outcome (no
            # in-world caption on it) — but it must be distinguishable in the log from
            # "we tried and rejected", or a silent miss looks identical to a clean page.
            if blocks:
                _log("info", "CAPTION: no candidates on this page (%d block(s)) — "
                             "nothing was read", len(blocks))
            return blocks, 0, 0
        if not (model and api_key and base_url):
            return blocks, 0, 0
        gray = np.asarray(image.convert("L"))
        out = list(blocks)
        pt_tot = ct_tot = 0
        for group in cluster_candidates(cands):
            union = union_bbox(group, CAPTION_PAD)
            combined = sum(len((b.text or "").strip()) for b in group)
            if combined > CLUSTER_MAX_CHARS:
                _log("info", "CAPTION skipped (cluster too long: %d chars over %d boxes)",
                     combined, len(group))
                if fitlog is not None:
                    fitlog.record_caption(union, "", "", "?", applied=False,
                                          reason=f"cluster too long ({combined} chars)")
                continue
            try:
                data, pt, ct = read_caption(image, union, model=model, api_key=api_key,
                                            base_url=base_url, timeout=timeout)
            except ProviderError as e:
                _log("warning", "CAPTION read failed (%s) — captions left as-is on this page", e)
                break
            except Exception as e:  # pragma: no cover - never break the render
                _log("warning", "CAPTION read error %s: %s", type(e).__name__, e)
                break
            pt_tot += int(pt or 0)
            ct_tot += int(ct or 0)
            kind = str((data or {}).get("kind", "") or "").upper()
            jp = str((data or {}).get("jp", "") or "").strip()
            en = str((data or {}).get("en", "") or "").strip()

            def _skip(reason: str) -> None:
                # NEVER SILENT (v0.27.36): this used to record only into the fitlog, which
                # is off unless the container was opted in — so a rejected caption left no
                # trace at all and the page simply came back Japanese (job-3 p109).
                _log("info", "CAPTION skipped (%s): kind=%s jp=%r en=%r box=%s",
                     reason, kind or "?", jp, en, list(union))
                if fitlog is not None:
                    fitlog.record_caption(union, jp, en, kind or "?", applied=False,
                                          reason=reason)

            if kind != "CAPTION":
                _skip("kind")
                continue
            tight, glyph_h = measure_text_box(gray, union)
            cap = capacity(tight[2], tight[3], glyph_h)
            if not jp:
                _skip("empty reading")
                continue
            if not _has_cjk(jp):
                _skip("not japanese")
                continue
            if has_credit_marker(jp):
                _skip("credit marker")
                continue
            if width_glyphs(jp) > cap:
                _skip(f"implausible length {width_glyphs(jp)}>{cap}")
                continue
            en_clean = _clean_translation(en)
            if not en_clean:
                _skip("unusable translation")
                continue
            x0, y0, w0, h0 = tight
            blk = TextBlock(
                box=[[x0, y0], [x0 + w0, y0], [x0 + w0, y0 + h0], [x0, y0 + h0]],
                bbox=(x0, y0, w0, h0),
                text=jp,
                translation=en_clean,
                confidence=None,
                orientation="horizontal",
            )
            drop = {id(b) for b in group}
            out = [b for b in out if id(b) not in drop] + [blk]
            if fitlog is not None:
                fitlog.record_caption(tight, jp, en_clean, "CAPTION", applied=True,
                                      reason="")
            _log("info", "CAPTION bbox=%s jp=%r en=%r (glyph_h=%d capacity=%d)",
                 list(tight), jp, en_clean, glyph_h, cap)
        return out, pt_tot, ct_tot
    except Exception as e:  # pragma: no cover - belt and braces
        _log("warning", "CAPTION recovery aborted (%s: %s) — page left as-is",
             type(e).__name__, e)
        return blocks, 0, 0
