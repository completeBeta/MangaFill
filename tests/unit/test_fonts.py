"""Unit tests for the font catalog + fallback resolution."""
from app.pipeline import fonts as F


def test_catalog_lists_default_and_bundled():
    cats = F.list_fonts()
    ids = [f["id"] for f in cats]
    assert "anime-ace" in ids
    assert F.default_font_id() == "anime-ace"
    # bundled OFL fonts are committed, so they're always present + available.
    bundled = [f for f in cats if f["source"] == "bundled"]
    assert bundled, "expected bundled fallback fonts"
    for f in bundled:
        assert f["available"] is True
        assert F.font_path(f["id"]) is not None


def test_selected_bundled_font_wins():
    p, fid, _ = F.resolve_font("bangers")
    assert fid == "bangers"
    assert p.endswith("Bangers-Regular.ttf")


def test_unknown_selected_id_falls_back():
    p, fid, _ = F.resolve_font("does-not-exist")
    # Never None — a bundled font is always available as fallback.
    assert p is not None and fid is not None


def test_fallback_when_default_absent(monkeypatch):
    """Anime Ace going dark (build-time pull failed) must fall back to a bundled face."""
    real_find = F._find_file

    def fake_find(filename):
        if filename == "AnimeAce-Regular.ttf":
            return None  # simulate the remote font being unavailable
        return real_find(filename)

    monkeypatch.setattr(F, "_find_file", fake_find)
    p, fid, _ = F.resolve_font(None)
    assert fid == "comic-neue"  # first bundled fallback
    assert p.endswith("ComicNeue-Regular.ttf")
