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
