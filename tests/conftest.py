"""Test setup — point the app at a throwaway DB + dirs before any app import.

`app.config.settings` is read at import time, so these env vars MUST be set before
any test module imports `app` (pytest imports conftest first). The test dir is wiped
each session so runs don't leak state into each other.
"""
import os
import shutil

import pytest

_TMP = "/tmp/mangafill-test"
shutil.rmtree(_TMP, ignore_errors=True)

os.environ["MANGA_FILL_STATE_DB"] = f"{_TMP}/test.db"
os.environ["MANGA_FILL_JOBS_DIR"] = f"{_TMP}/jobs"
os.environ["MANGA_FILL_RAW_DIR"] = f"{_TMP}/input"
os.environ["MANGA_FILL_OUTPUT_DIR"] = f"{_TMP}/output"


@pytest.fixture(autouse=True)
def _isolate_persisted_state():
    """Snapshot + restore the persisted settings/model rows around EVERY test.

    Every test module shares ONE database (`/tmp/mangafill-test/test.db`, wiped only
    once per session), so a test that writes a persisted setting — `engine_enabled`,
    `engine_url`, `dry_run`, `source_lang`, … — changed what every test running AFTER
    it saw. The suite therefore passed or failed depending on collection order:
    v0.30.7's per-page-language test (`test_engine_language_per_page.py`) set
    `dry_run=false` + engine-on and made 4 unrelated tests fail — and the same 4
    passed as soon as that module ran last (measured 2026-09-25, clean trees, same
    runner: v0.30.6 = 402 passed/0 failed, v0.30.7 = 400/4).

    Restoring the rows here makes the run order-independent by construction — for
    every present and future test — instead of asking each test to clean up after
    itself and forgetting to. Jobs/pages are deliberately NOT restored: each test
    creates the rows it asserts on.
    """
    try:
        from sqlalchemy import inspect as _inspect

        from app.db import SessionLocal, init_db
        from app.models import Model, Setting
    except Exception:  # a test that never touches the database
        yield
        return

    init_db()
    db = SessionLocal()

    def snapshot(model):
        cols = [c.key for c in _inspect(model).mapper.column_attrs]
        return cols, [tuple(getattr(r, c) for c in cols) for r in db.query(model).all()]

    def restore(model, snap):
        cols, rows = snap
        db.query(model).delete()
        db.flush()
        for row in rows:
            db.add(model(**dict(zip(cols, row))))
        db.commit()

    snaps = [(m, snapshot(m)) for m in (Setting, Model)]
    try:
        yield
    finally:
        try:
            db.rollback()
            for model, snap in snaps:
                restore(model, snap)
        finally:
            db.close()
