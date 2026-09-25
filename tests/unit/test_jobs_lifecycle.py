"""Job lifecycle controls — retention purge, clear-all, and the gpu_worker_url setting."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.db import SessionLocal, init_db
from app.models import Job
from app.services.job_engine import _job_dir, clear_all_jobs, purge_old_jobs


def _make_job(db, name: str, created_days_ago: int) -> Job:
    created = (datetime.now(timezone.utc) - timedelta(days=created_days_ago)).isoformat()
    job = Job(name=name, status="done", created_at=created)
    db.add(job)
    db.commit()
    db.refresh(job)
    d = _job_dir(job.id)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, "page.png"), "w") as f:
        f.write("x")
    return job


def test_purge_old_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "jobs_dir", str(tmp_path))
    init_db()
    db = SessionLocal()
    try:
        old = _make_job(db, "purge-old", created_days_ago=10)
        fresh = _make_job(db, "purge-fresh", created_days_ago=1)
        purge_old_jobs(db, days=7)
        assert db.get(Job, old.id) is None            # purged (row gone)
        assert db.get(Job, fresh.id) is not None      # kept
        assert not os.path.exists(_job_dir(old.id))   # files gone
        assert os.path.exists(_job_dir(fresh.id))
    finally:
        db.close()


def test_clear_all_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "jobs_dir", str(tmp_path))
    init_db()
    db = SessionLocal()
    try:
        j1 = _make_job(db, "clear-a", created_days_ago=0)
        j2 = _make_job(db, "clear-b", created_days_ago=0)
        assert db.get(Job, j1.id) is not None
        clear_all_jobs(db)
        assert db.query(Job).count() == 0
        assert not os.path.exists(_job_dir(j1.id))
        assert not os.path.exists(_job_dir(j2.id))
    finally:
        db.close()


def test_gpu_worker_url_setting():
    init_db()
    db = SessionLocal()
    try:
        from app.settings_store import get_setting, set_setting

        assert set_setting(db, "gpu_worker_url", "http://gpu-host:9001") is True
        db.commit()
        assert get_setting(db, "gpu_worker_url") == "http://gpu-host:9001"
    finally:
        db.close()
