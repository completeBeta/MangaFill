"""Logs endpoint — tail the app log file."""
from __future__ import annotations

import os

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from app.services.logging import LOG_PATH

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("", response_class=PlainTextResponse)
def get_logs(lines: int = 200):
    if not os.path.exists(LOG_PATH):
        return "no log yet"
    with open(LOG_PATH, "rb") as f:
        f.seek(0, os.SEEK_END)
        # read up to ~64KB tail, then keep the last `lines` lines
        f.seek(max(0, f.tell() - 65536))
        data = f.read().decode("utf-8", errors="replace")
    return "\n".join(data.splitlines()[-lines:])
