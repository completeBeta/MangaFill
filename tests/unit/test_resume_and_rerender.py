"""Resume a partial job + re-render a single page.

A job that finished "partial" (some pages failed — e.g. the 2026-09-15 provider
outage) could not be resumed at all: `POST /start` only accepted
paused/cancelled/failed, so the "then Resume" advice in the outage error message
was wrong. These tests pin the fix, plus the single-page re-render used to apply
a pipeline fix to pages produced by older code.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.jobs import rerender_page, rerender_pages, start_job
from app.config import settings
from app.db import SessionLocal, init_db
from app.models import Job, Page, TextBlock


def _job(db, **kw) -> int:
    job = Job(name="resume-test", output_mode="folder", source_format="folder", **kw)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job.id


def _page(db, job_id, index, status="done", error=None):
    p = Page(job_id=job_id, index=index, status=status, error=error,
             original_path=f"/tmp/p{index}.jpg")
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "jobs_dir", str(tmp_path))
    init_db()


def test_start_job_resumes_partial_and_retries_only_unfinished_pages():
    db = SessionLocal()
    try:
        jid = _job(db, status="partial", pages_total=3, pages_done=2,
                   error="1 of 3 page(s) failed — first error (page 2): boom")
        _page(db, jid, 0, "done")
        _page(db, jid, 1, "failed", error="boom")
        _page(db, jid, 2, "done")
    finally:
        db.close()

    db = SessionLocal()
    try:
        out = start_job(jid, db)
        assert out["status"] == "queued"
        assert out["error"] is None
        pages = {p.index: p for p in db.query(Page).filter(Page.job_id == jid).all()}
        assert pages[0].status == "done"       # finished page untouched
        assert pages[2].status == "done"
        assert pages[1].status == "pending"    # failed page will be retried
        assert pages[1].error is None
    finally:
        db.close()


def test_start_job_clears_a_page_left_running_by_a_crash():
    db = SessionLocal()
    try:
        jid = _job(db, status="failed", pages_total=2, pages_done=1)
        _page(db, jid, 0, "done")
        _page(db, jid, 1, "running")
    finally:
        db.close()

    db = SessionLocal()
    try:
        start_job(jid, db)
        pages = {p.index: p for p in db.query(Page).filter(Page.job_id == jid).all()}
        assert pages[1].status == "pending"
    finally:
        db.close()


def test_start_job_ignores_a_running_job():
    db = SessionLocal()
    try:
        jid = _job(db, status="running", pages_total=1)
        _page(db, jid, 0, "running")
        assert start_job(jid, db)["status"] == "running"   # not re-queued
    finally:
        db.close()


def test_rerender_page_resets_one_page_and_keeps_counters_honest():
    db = SessionLocal()
    try:
        jid = _job(db, status="done", pages_total=2, pages_done=2,
                   blocks_found=5, blocks_ok=4)
        p0 = _page(db, jid, 0, "done")
        _page(db, jid, 1, "done")
        db.add_all([
            TextBlock(page_id=p0.id, box="[0, 0, 10, 10]", jp_text="a", en_text="A"),
            TextBlock(page_id=p0.id, box="[0, 0, 10, 10]", jp_text="b", en_text="B"),
            TextBlock(page_id=p0.id, box="[0, 0, 10, 10]", jp_text="c", en_text=""),
        ])
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        out = rerender_page(jid, 0, db)
        assert out["ok"] is True
        job = db.get(Job, jid)
        assert job.status == "queued"
        # page 0's old contribution is subtracted (the engine re-adds it on success)
        assert job.pages_done == 1
        assert job.blocks_found == 2
        assert job.blocks_ok == 2
        pages = {p.index: p for p in db.query(Page).filter(Page.job_id == jid).all()}
        assert pages[0].status == "pending"
        assert pages[1].status == "done"   # the other page is left alone
    finally:
        db.close()


def test_rerender_page_refuses_while_the_job_is_running():
    db = SessionLocal()
    try:
        jid = _job(db, status="running", pages_total=1)
        _page(db, jid, 0, "running")
    finally:
        db.close()

    db = SessionLocal()
    try:
        with pytest.raises(HTTPException) as ei:
            rerender_page(jid, 0, db)
        assert ei.value.status_code == 409
    finally:
        db.close()


def test_rerender_page_404s_on_a_bad_page_or_job():
    db = SessionLocal()
    try:
        jid = _job(db, status="done", pages_total=1)
        _page(db, jid, 0, "done")
    finally:
        db.close()

    db = SessionLocal()
    try:
        with pytest.raises(HTTPException):
            rerender_page(jid, 99, db)
        with pytest.raises(HTTPException):
            rerender_page(999999, 0, db)
    finally:
        db.close()


def test_rerender_pages_resets_every_page_and_the_counters():
    db = SessionLocal()
    try:
        jid = _job(db, status="partial", pages_total=3, pages_done=2,
                   blocks_found=9, blocks_ok=7)
        _page(db, jid, 0, "done")
        _page(db, jid, 1, "failed", error="boom")
        _page(db, jid, 2, "done")
    finally:
        db.close()

    db = SessionLocal()
    try:
        out = rerender_pages(jid, None, db)
        assert out["pages_queued"] == 3 and out["indices"] == [0, 1, 2]
        job = db.get(Job, jid)
        assert job.status == "queued"
        # pages_done drops by the two pages that had counted; the block counters
        # only drop by what those pages actually contributed (there are no
        # TextBlock rows in this fixture), so they stay put.
        assert job.pages_done == 0
        assert (job.blocks_found, job.blocks_ok) == (9, 7)
        for p in db.query(Page).filter(Page.job_id == jid).all():
            assert p.status == "pending"
            assert p.error is None
    finally:
        db.close()


def test_rerender_pages_can_target_a_subset():
    """A burst of per-page calls races the worker (later ones 409), so the
    endpoint must be able to reset a whole SET in one request."""
    db = SessionLocal()
    try:
        jid = _job(db, status="done", pages_total=4, pages_done=4,
                   blocks_found=8, blocks_ok=8)
        for i in range(4):
            p = _page(db, jid, i, "done")
            db.add(TextBlock(page_id=p.id, box="[0, 0, 10, 10]", jp_text="a", en_text="A"))
        db.commit()
    finally:
        db.close()

    db = SessionLocal()
    try:
        out = rerender_pages(jid, {"indices": [1, 3]}, db)
        assert out["pages_queued"] == 2
        assert out["indices"] == [1, 3]
        job = db.get(Job, jid)
        assert job.status == "queued"
        assert job.pages_done == 2         # only the two redone pages subtracted
        assert job.blocks_found == 6
        assert job.blocks_ok == 6
        pages = {p.index: p.status for p in db.query(Page).filter(Page.job_id == jid).all()}
        assert pages == {0: "done", 1: "pending", 2: "done", 3: "pending"}
    finally:
        db.close()


def test_rerender_pages_404s_on_an_unknown_index():
    db = SessionLocal()
    try:
        jid = _job(db, status="done", pages_total=1)
        _page(db, jid, 0, "done")
    finally:
        db.close()

    db = SessionLocal()
    try:
        with pytest.raises(HTTPException) as ei:
            rerender_pages(jid, {"indices": [0, 55]}, db)
        assert ei.value.status_code == 404
    finally:
        db.close()


def test_rerender_pages_refuses_while_the_job_is_running():
    db = SessionLocal()
    try:
        jid = _job(db, status="running", pages_total=1)
        _page(db, jid, 0, "running")
    finally:
        db.close()

    db = SessionLocal()
    try:
        with pytest.raises(HTTPException) as ei:
            rerender_pages(jid, None, db)
        assert ei.value.status_code == 409
    finally:
        db.close()
