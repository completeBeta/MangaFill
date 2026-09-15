"""A translation-provider outage must stop the job LOUDLY, not churn silently.

Regression cover for the reported behaviour: DeepSeek went down, the pipeline
kept going, the job finished "partial" with no error, and it just looked stalled.
Now repeated provider failures stop the job and put the reason on the job row.
"""
from __future__ import annotations

import os

import pytest

from app.config import settings
from app.db import SessionLocal, init_db
from app.models import Job, Page, Setting
from app.pipeline.translate import ProviderError
from app.services import job_engine
from app.settings_store import set_setting


@pytest.fixture(autouse=True)
def _isolate_settings():
    """Snapshot + restore the settings table around each test.

    The test DB is shared for the whole pytest session, so a test that pins
    `dry_run=false` or `source_lang=ja` would otherwise leak into later modules
    that assert the defaults.
    """
    init_db()
    db = SessionLocal()
    try:
        before = {s.key: s.value for s in db.query(Setting).all()}
    finally:
        db.close()
    yield
    db = SessionLocal()
    try:
        db.query(Setting).delete()
        for k, v in before.items():
            db.add(Setting(key=k, value=v))
        db.commit()
    finally:
        db.close()


def _make_job(db, n_pages: int, jobs_dir: str) -> int:
    job = Job(name="outage", output_mode="folder", source_format="folder",
              status="queued", pages_total=n_pages)
    db.add(job)
    db.commit()
    db.refresh(job)
    d = os.path.join(jobs_dir, str(job.id), "original")
    os.makedirs(d, exist_ok=True)
    for i in range(n_pages):
        p = os.path.join(d, f"p{i:03d}.jpg")
        with open(p, "w") as f:
            f.write("x")
        db.add(Page(job_id=job.id, index=i, status="pending", original_path=p))
    db.commit()
    return job.id


def _configure(db, **settings_kv):
    for k, v in settings_kv.items():
        assert set_setting(db, k, v) is True
    db.commit()


def test_provider_outage_stops_job_and_reports_reason(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "jobs_dir", str(tmp_path))
    init_db()
    db = SessionLocal()
    try:
        _configure(db, source_lang="ja", dry_run="false", provider_fail_limit="2")
        jid = _make_job(db, 5, str(tmp_path))
    finally:
        db.close()

    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise ProviderError(
            "api.deepseek.com returned no completion for model 'deepseek-flash' "
            "(empty/absent 'choices')"
        )

    monkeypatch.setattr(job_engine, "render_translated_page", boom)
    job_engine.process_job(jid)

    db = SessionLocal()
    try:
        job = db.get(Job, jid)
        assert calls["n"] == 2, "must stop after provider_fail_limit consecutive failures"
        assert job.status == "failed"
        assert "provider unavailable" in (job.error or "").lower()
        assert "api.deepseek.com" in (job.error or "")
        assert db.query(Page).filter(Page.job_id == jid, Page.status == "failed").count() == 2
        # pages beyond the limit are never started
        assert db.query(Page).filter(Page.job_id == jid, Page.status == "pending").count() == 3
    finally:
        db.close()


def test_single_provider_blip_does_not_stop_the_job(tmp_path, monkeypatch):
    """One transient failure (below the limit) must not kill a long job."""
    monkeypatch.setattr(settings, "jobs_dir", str(tmp_path))
    init_db()
    db = SessionLocal()
    try:
        _configure(db, source_lang="ja", dry_run="false", provider_fail_limit="3")
        jid = _make_job(db, 3, str(tmp_path))
    finally:
        db.close()

    from PIL import Image

    calls = {"n": 0}

    def flaky(path, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ProviderError("api.deepseek.com timed out after 300s", transient=True)
        return Image.new("RGB", (12, 12)), [], 0, 0, []

    monkeypatch.setattr(job_engine, "render_translated_page", flaky)
    job_engine.process_job(jid)

    db = SessionLocal()
    try:
        job = db.get(Job, jid)
        assert calls["n"] == 3
        assert job.status == "partial"  # 2 done, 1 failed — job kept going
        assert "page 1" in (job.error or "")  # the failure is still reported
        assert db.query(Page).filter(Page.job_id == jid, Page.status == "done").count() == 2
    finally:
        db.close()


def test_completed_job_with_no_failures_has_no_error(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "jobs_dir", str(tmp_path))
    init_db()
    db = SessionLocal()
    try:
        _configure(db, source_lang="ja", dry_run="false")
        jid = _make_job(db, 2, str(tmp_path))
    finally:
        db.close()

    from PIL import Image

    def ok(path, *a, **k):
        return Image.new("RGB", (12, 12)), [], 0, 0, []

    monkeypatch.setattr(job_engine, "render_translated_page", ok)
    job_engine.process_job(jid)

    db = SessionLocal()
    try:
        job = db.get(Job, jid)
        assert job.status == "done"
        assert job.error is None
    finally:
        db.close()
