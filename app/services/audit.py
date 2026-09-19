"""Audit trail — every user and system action is recorded, on two sinks.

Why two sinks:
  * the **app log file**, which `/api/logs` tails and the Logs tab renders — so an
    action shows up where the user actually looks ("did it mention I cleared the
    jobs?"). Before this, destructive actions left no trace at all.
  * an **`audit_log` table** — the durable, filterable record. The log file is a
    64 KB tail and gets rotated, so it is not a reliable history on its own.

A failing audit write must NEVER break the action it describes, so the DB leg is
best-effort and swallows its own errors (it logs a warning instead).

`actor` is "user" for an API call and "system" for something the engine did on
its own (a job finishing, a page failing).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text

from app.db import engine
from app.services.logging import get_logger

_log = get_logger("audit")

_DDL = """
CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT    NOT NULL,
    actor   TEXT    NOT NULL,
    action  TEXT    NOT NULL,
    target  TEXT,
    detail  TEXT
)
"""

_ready = False


def _ensure_table() -> None:
    """Create the audit table once per process (cheap, idempotent)."""
    global _ready
    if _ready:
        return
    try:
        with engine.begin() as conn:
            conn.execute(text(_DDL))
        _ready = True
    except Exception as exc:  # auditing must never take the app down
        _log.warning("audit: could not create audit_log table: %s", exc)


def record(action: str, target: str | None = None, detail=None, actor: str = "user") -> None:
    """Record one action. Never raises."""
    if detail is not None and not isinstance(detail, str):
        try:
            detail = json.dumps(detail, ensure_ascii=False, sort_keys=True)
        except Exception:
            detail = str(detail)

    ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
    parts = [f"actor={actor}", f"action={action}"]
    if target:
        parts.append(f"target={target}")
    if detail:
        parts.append(f"detail={detail}")
    _log.info("AUDIT %s", " ".join(parts))

    _ensure_table()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO audit_log (ts, actor, action, target, detail) "
                    "VALUES (:ts, :actor, :action, :target, :detail)"
                ),
                {"ts": ts, "actor": actor, "action": action,
                 "target": target, "detail": detail},
            )
    except Exception as exc:
        _log.warning("audit: could not persist %s: %s", action, exc)


def recent(limit: int = 200) -> list[dict]:
    """Most recent audit rows, newest first."""
    _ensure_table()
    try:
        with engine.begin() as conn:
            rows = conn.execute(
                text("SELECT ts, actor, action, target, detail FROM audit_log "
                     "ORDER BY id DESC LIMIT :n"),
                {"n": int(limit)},
            ).mappings().all()
        return [dict(r) for r in rows]
    except Exception:
        return []
