"""Database engine + session (SQLite, single-worker — state lives in the DB, not process memory)."""
from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    pass


def _db_url() -> str:
    path = settings.state_db
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    return f"sqlite:///{path}"


engine = create_engine(_db_url(), connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401  (register tables)

    Base.metadata.create_all(engine)
    # Lightweight, idempotent migrations (SQLite has no ALTER via create_all).
    with engine.begin() as conn:
        cols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(jobs)")]
        if "model_id" not in cols:
            conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN model_id INTEGER")
        if "source_format" not in cols:
            conn.exec_driver_sql("ALTER TABLE jobs ADD COLUMN source_format VARCHAR DEFAULT 'folder'")

        mcols = [r[1] for r in conn.exec_driver_sql("PRAGMA table_info(models)")]
        for col, ddl in [
            ("price_in", "REAL DEFAULT 0"),
            ("price_out", "REAL DEFAULT 0"),
            ("offpeak_in", "REAL"),
            ("offpeak_out", "REAL"),
            ("offpeak_start", "VARCHAR"),
            ("offpeak_end", "VARCHAR"),
        ]:
            if col not in mcols:
                conn.exec_driver_sql(f"ALTER TABLE models ADD COLUMN {col} {ddl}")
