"""Download freshness + cost tally (v0.27.16).

Two reported/observed bugs:
  * the download archive was built once and reused forever, so pages re-rendered
    after the first download never reached the file the user got;
  * there was no running cost total, and the period the number covers matters
    (today / this week / this month / this year).
Also covers the API key that used to be returned in full by /api/models.
"""
from __future__ import annotations

import json
import os
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.api.jobs import _download_archive, _fingerprint, _out_pages
from app.api.stats import cost_tally, period_start, resolve_tz, job_timestamp
from app.db import SessionLocal, init_db
from app.models import Job
from app.services.job_engine import _job_dir, _out_dir
from app.settings_store import is_masked_key, mask_api_key, update_model
from app.models import Model


def _write_pages(job_id: int, pages: dict[str, bytes]) -> None:
    out = Path(_out_dir(job_id))
    out.mkdir(parents=True, exist_ok=True)
    for name, data in pages.items():
        (out / name).write_bytes(data)


def _zip_names(arc: str) -> list[str]:
    with zipfile.ZipFile(arc) as zf:
        return sorted(zf.namelist())


def _zip_bytes(arc: str, name: str) -> bytes:
    with zipfile.ZipFile(arc) as zf:
        return zf.read(name)


def test_archive_is_rebuilt_when_a_page_changes():
    """The bug: re-rendered pages never reached the download."""
    init_db()
    job_id = 90001
    _write_pages(job_id, {"page_001.jpg": b"OLD-RENDER", "page_002.jpg": b"page2"})
    arc = _download_archive(job_id, "cbz")
    assert _zip_bytes(arc, "page_001.jpg") == b"OLD-RENDER"

    # a re-render rewrites the page
    time.sleep(0.01)
    (Path(_out_dir(job_id)) / "page_001.jpg").write_bytes(b"NEW-RENDER-v0.27.16")
    arc2 = _download_archive(job_id, "cbz")
    assert _zip_bytes(arc2, "page_001.jpg") == b"NEW-RENDER-v0.27.16"


def test_archive_is_not_rebuilt_when_nothing_changed():
    init_db()
    job_id = 90002
    _write_pages(job_id, {"page_001.jpg": b"same"})
    arc = _download_archive(job_id, "cbz")
    first = os.stat(arc).st_mtime_ns
    time.sleep(0.01)
    assert _download_archive(job_id, "cbz") == arc
    assert os.stat(arc).st_mtime_ns == first, "archive rebuilt for no reason"


def test_archive_picks_up_added_and_deleted_pages():
    init_db()
    job_id = 90003
    _write_pages(job_id, {"page_001.jpg": b"a"})
    assert _zip_names(_download_archive(job_id, "cbz")) == ["page_001.jpg"]
    _write_pages(job_id, {"page_002.jpg": b"b"})
    assert _zip_names(_download_archive(job_id, "cbz")) == ["page_001.jpg", "page_002.jpg"]
    os.remove(Path(_out_dir(job_id)) / "page_001.jpg")
    assert _zip_names(_download_archive(job_id, "cbz")) == ["page_002.jpg"]


def test_archive_keeps_a_manifest_beside_it():
    init_db()
    job_id = 90004
    _write_pages(job_id, {"page_001.jpg": b"a", "page_002.jpg": b"b"})
    arc = _download_archive(job_id, "cbz")
    man = json.load(open(arc + ".manifest.json"))
    assert set(man) == {"page_001.jpg", "page_002.jpg"}
    assert man == _fingerprint(_out_dir(job_id), _out_pages(_out_dir(job_id)))


# ---------------------------------------------------------------- cost tally

def _tally_job(db, name: str, finished: str, cost: float, tokens: int):
    j = Job(name=name, status="done", pages_done=2, pages_total=2, blocks_found=3,
            blocks_ok=3, tokens_used=tokens, cost_usd=cost, created_at=finished,
            finished_at=finished, updated_at=finished)
    db.add(j)
    db.commit()
    return j


def test_period_windows_are_calendar_windows_in_local_time():
    tz = resolve_tz("Australia/Sydney")
    now = datetime(2026, 9, 17, 4, 30, tzinfo=timezone.utc)  # 14:30 AEST, a Thursday
    day = period_start("day", now, tz)
    week = period_start("week", now, tz)
    month = period_start("month", now, tz)
    year = period_start("year", now, tz)
    assert day == datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc)      # 00:00 AEST
    assert week == datetime(2026, 9, 13, 14, 0, tzinfo=timezone.utc)     # Monday 00:00
    assert month == datetime(2026, 8, 31, 14, 0, tzinfo=timezone.utc)    # 1 Sep 00:00
    assert year == datetime(2025, 12, 31, 13, 0, tzinfo=timezone.utc)    # 1 Jan 00:00 AEDT
    assert year < month < week < day, "windows must nest: year opens first, day last"
    assert resolve_tz("Nowhere/Nothing") is timezone.utc                 # bad name -> UTC


def test_tally_window_and_all_time_totals():
    init_db()
    db = SessionLocal()
    try:
        # Own the jobs table for this test: other modules' rows would make the
        # window counts ambiguous (they are created with created_at = now).
        db.query(Job).delete()
        db.commit()
        tz = resolve_tz("Australia/Sydney")
        now = datetime.now(timezone.utc)
        day_start = period_start("day", now, tz)
        month_start = period_start("month", now, tz)
        _tally_job(db, "tally-today", (day_start + timedelta(minutes=1)).isoformat(), 1.25, 1000)
        _tally_job(db, "tally-month", (month_start + timedelta(minutes=1)).isoformat(), 0.50, 500)
        _tally_job(db, "tally-old", (now - timedelta(days=800)).isoformat(), 9.00, 9000)

        expected_in_window = 2 if month_start >= day_start and month_start != day_start else 1
        out = cost_tally("day", "Australia/Sydney", db)   # same window, via the endpoint
        assert out["period"] == "day"
        assert set(out["periods"]) == {"day", "week", "month", "year"}
        assert out["window"]["jobs"] == expected_in_window
        assert out["all_time"]["jobs"] == 3
        assert abs(out["all_time"]["cost_usd"] - 10.75) < 1e-6
        assert out["all_time"]["tokens"] == 10500
        assert cost_tally("nonsense", None, db)["period"] == "day"  # unknown -> default
    finally:
        db.close()


# ------------------------------------------------------------- key masking

def test_api_keys_are_masked_and_blank_keeps_the_stored_key():
    assert mask_api_key("sk-abcdef123456789") == "sk-abc…6789"
    assert mask_api_key("") == ""
    assert mask_api_key("short") == "•" * 5
    assert is_masked_key("sk-abc…6789") and is_masked_key("") and is_masked_key("•••")
    assert not is_masked_key("sk-realnewkeyvalue")

    init_db()
    db = SessionLocal()
    try:
        # Do not touch other modules' rows: test_models.py asserts on the model
        # list, so this test creates its own row and removes it again.
        db.add(Model(name="key-mask-test", base_url="http://x/v1",
                     api_key="sk-original-key-value"))
        db.commit()
        m = db.query(Model).filter(Model.name == "key-mask-test").first()
        # the editor shows the mask and posts it back unchanged
        update_model(db, m.id, name="key-mask-test", base_url="http://x/v1",
                     api_key=mask_api_key("sk-original-key-value"))
        assert db.get(Model, m.id).api_key == "sk-original-key-value"
        # a blank field also keeps it
        update_model(db, m.id, name="key-mask-test", base_url="http://x/v1", api_key="")
        assert db.get(Model, m.id).api_key == "sk-original-key-value"
        # a real new key replaces it
        update_model(db, m.id, name="key-mask-test", base_url="http://x/v1",
                     api_key="sk-brand-new-key")
        assert db.get(Model, m.id).api_key == "sk-brand-new-key"
        row = db.get(Model, m.id)
        db.delete(row)
        db.commit()
    finally:
        db.close()
