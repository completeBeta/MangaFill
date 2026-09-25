"""Font catalog + resolution for typesetting.

Manga lettering needs a manga/comic face. The catalog holds two kinds of font:

- **bundled** — SIL OFL faces committed under `fonts/` (redistributable, always
  present in the image). These are the guaranteed fallback set.
- **remote** — the Blambot "Anime Ace" face, pulled into `/app/fonts` at build
  time (its license forbids redistribution, so it can't be committed). It is the
  default look but may be absent if the build-time pull failed.

Resolution order (first hit wins): ``$MANGA_FILL_FONT`` path override → the
user-selected font (if present) → the default font (if present) → the first
available bundled font → ``None`` (caller falls back to DejaVu Sans Bold).
"""
from __future__ import annotations

import os

FONT_CATALOG: list[dict] = [
    {
        "id": "anime-ace",
        "name": "Anime Ace",
        "style": "Hand-lettered manga dialogue — the classic scanlation face",
        "filename": "AnimeAce-Regular.ttf",
        "license": "Blambot freeware — non-profit/indie use, no redistribution",
        "source": "remote",
        "url": "https://st.1001fonts.net/download/font/anime-ace.regular.ttf",
        "default": True,
    },
    {
        "id": "comic-neue",
        "name": "Comic Neue",
        "style": "Clean comic, mixed case — best for manhwa/webtoon",
        "filename": "ComicNeue-Regular.ttf",
        "license": "SIL OFL 1.1",
        "source": "bundled",
        "default": False,
    },
    {
        # INSERT NEW FACES *AFTER* THIS PAIR, never above it: `resolve_font` step 4 falls back to
        # "the first available bundled font" by CATALOG ORDER, so inserting a face above comic-neue
        # silently changes the face used whenever Anime Ace is absent (a build-time pull failure).
        # Comic Shanns (added v0.29) did exactly that and was caught by
        # `test_fonts.py::test_fallback_when_default_absent`. It is a selectable face and upstream's
        # own English default — not our fallback, which stays Comic Neue by measurement (see the
        # LEGIBILITY_FALLBACK_IDS note below: Comic Neue lettered 21/30 previously unreadable blocks
        # where Anime Ace reached none).
        "id": "comic-shanns",
        "name": "Comic Shanns",
        "style": "Comic mono, clean — the engine's own default English lettering face",
        "filename": "ComicShanns-Regular.ttf",
        "license": "MIT (© 2020 Shannon Miwa) — redistributable, shipped in-repo",
        "source": "bundled",
        "default": False,
    },
    {
        "id": "bangers",
        "name": "Bangers",
        "style": "Comic-book all-caps — manga titles & emphasis",
        "filename": "Bangers-Regular.ttf",
        "license": "SIL OFL 1.1",
        "source": "bundled",
        "default": False,
    },
    {
        "id": "patrick-hand",
        "name": "Patrick Hand",
        "style": "Handwriting — casual webtoon dialogue",
        "filename": "PatrickHand-Regular.ttf",
        "license": "SIL OFL 1.1",
        "source": "bundled",
        "default": False,
    },
    {
        "id": "gloria-hallelujah",
        "name": "Gloria Hallelujah",
        "style": "Loose handwriting — webtoon alt",
        "filename": "GloriaHallelujah.ttf",
        "license": "SIL OFL 1.1",
        "source": "bundled",
        "default": False,
    },
]

FONT_DIRS = ("fonts", "/app/fonts")
FONT_FALLBACK = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _find_file(filename: str) -> str | None:
    for base in FONT_DIRS:
        p = os.path.join(base, filename)
        if os.path.isfile(p):
            return p
    return None


def _catalog_by_id(font_id: str) -> dict | None:
    for f in FONT_CATALOG:
        if f["id"] == font_id:
            return f
    return None


def default_font_id() -> str | None:
    for f in FONT_CATALOG:
        if f.get("default"):
            return f["id"]
    return FONT_CATALOG[0]["id"] if FONT_CATALOG else None


def list_fonts() -> list[dict]:
    """Catalog with per-font availability (for the UI + resolution)."""
    out: list[dict] = []
    for f in FONT_CATALOG:
        d = dict(f)
        d["available"] = _find_file(f["filename"]) is not None
        out.append(d)
    return out


def font_path(font_id: str) -> str | None:
    """Resolved file path for a catalog font id, or None if absent/unknown."""
    f = _catalog_by_id(font_id)
    return _find_file(f["filename"]) if f else None


def resolve_font(selected_id: str | None = None) -> tuple[str | None, str | None, str | None]:
    """Pick the font to typeset with → (path, font_id, name).

    1. ``$MANGA_FILL_FONT`` — explicit path override (any file on disk).
    2. ``selected_id`` — the user's chosen font, if its file is present.
    3. the default font, if present.
    4. the first available bundled font (guaranteed fallback).
    5. ``None`` — caller falls back to DejaVu Sans Bold.
    """
    env = os.environ.get("MANGA_FILL_FONT")
    if env and os.path.isfile(env):
        return env, None, os.path.basename(env)

    ids: list[str] = []
    if selected_id:
        ids.append(selected_id)
    d = default_font_id()
    if d and d not in ids:
        ids.append(d)
    for f in FONT_CATALOG:
        if f["id"] not in ids:
            ids.append(f["id"])

    for fid in ids:
        f = _catalog_by_id(fid)
        if f is None:
            continue
        path = _find_file(f["filename"])
        if path:
            return path, fid, f["name"]
    return None, None, None


def resolve_font_path(selected_id: str | None = None) -> str | None:
    """Resolved font path with the DejaVu ultimate fallback baked in."""
    path, _fid, _name = resolve_font(selected_id)
    if path:
        return path
    for p in FONT_FALLBACK:
        if os.path.exists(p):
            return p
    return None


# --- legibility fallback (v0.27.41) ----------------------------------------------------
# A block whose English CANNOT be lettered legibly in its own box with the configured
# face (see `typeset._LEGIBLE_FLOOR`) used to stay microscopic — 8px on a page whose
# dialogue is lettered at 22px. Anime Ace, the default face, is the WIDEST face in the
# catalog, which is what costs the size on tight boxes. Measured on 30 under-legible
# blocks (job-3 pp. 4-164, 2026-09-22): Anime Ace reached 14px on 0 of them, Comic Neue
# on 21 (mean +5px), Patrick Hand on 20 (+6.8px), Bangers on 23 (+6.3px but it is an
# all-caps display face, wrong for mixed-case dialogue).
#
# ORDER IS FIDELITY FIRST, NOT WIDTH FIRST: a clean mixed-case comic face, then
# handwriting. The configured face always wins wherever it IS legible — this only ever
# replaces a block the reader could not otherwise read.
LEGIBILITY_FALLBACK_IDS = ("comic-neue", "patrick-hand")


def resolve_legibility_fallback(primary_path: str | None,
                               primary_id: str | None = None) -> str | None:
    """Path of a narrower face for blocks the configured face cannot letter legibly.

    Returns None when there is no usable alternative (no bundled face present, or the
    only candidate IS the primary), so callers keep their existing behaviour.
    ``$MANGA_FILL_FALLBACK_FONT`` overrides the choice with any path on disk.
    """
    def _same(a: str | None, b: str | None) -> bool:
        try:
            return bool(a) and bool(b) and os.path.realpath(a) == os.path.realpath(b)
        except Exception:
            return False

    env = os.environ.get("MANGA_FILL_FALLBACK_FONT")
    if env and os.path.isfile(env) and not _same(env, primary_path):
        return env
    for fid in LEGIBILITY_FALLBACK_IDS:
        if primary_id and fid == primary_id:
            continue
        f = _catalog_by_id(fid)
        if f is None:
            continue
        p = _find_file(f["filename"])
        if p and not _same(p, primary_path):
            return p
    return None
