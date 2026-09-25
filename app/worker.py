"""Background worker — a single daemon thread polling the jobs table for queued work.

One worker only (models are single-tenant; the pipeline is CPU-heavy and runs one
page at a time). All state lives in SQLite, so the worker can be restarted freely.
"""
from __future__ import annotations

import threading
import time

from app.db import SessionLocal
from app.models import Job
from app.services.job_engine import process_job, purge_old_jobs
from app.services.logging import get_logger

log = get_logger("worker")

PURGE_INTERVAL_S = 3600  # run the 7-day retention purge hourly
RETENTION_DAYS = 7


class Worker:
    def __init__(self, poll_interval: float = 1.0):
        self._interval = poll_interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mangafill-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _claim_next(self) -> int | None:
        db = SessionLocal()
        try:
            job = db.query(Job).filter(Job.status == "queued").order_by(Job.id).first()
            if job is None:
                return None
            job.status = "running"
            db.commit()
            return job.id
        finally:
            db.close()

    def _purge_old(self) -> None:
        db = SessionLocal()
        try:
            n = purge_old_jobs(db, days=RETENTION_DAYS)
            if n:
                log.info("retention: purged %d job(s) older than %d days", n, RETENTION_DAYS)
        except Exception as e:  # pragma: no cover - defensive
            log.warning("retention purge failed: %s", e)
        finally:
            db.close()

    def _run(self) -> None:
        last_purge = 0.0
        while not self._stop.is_set():
            now = time.time()
            if now - last_purge > PURGE_INTERVAL_S:
                self._purge_old()
                last_purge = now
            job_id = self._claim_next()
            if job_id is None:
                self._stop.wait(self._interval)
                continue
            log.info("starting job %s", job_id)
            # process_job is defensive (never raises); this is a final safety net.
            try:
                process_job(job_id)
            except Exception as e:  # pragma: no cover - defensive
                db = SessionLocal()
                try:
                    job = db.get(Job, job_id)
                    if job:
                        job.status = "failed"
                        job.error = f"worker error: {e}"
                        db.commit()
                finally:
                    db.close()


worker = Worker()
