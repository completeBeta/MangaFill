"""/api/logs — the Logs page backend: several named surfaces, all tail-able.

Backward compatible on purpose: `GET /api/logs?lines=N` still returns the tail of the
MAIN log exactly as before, so the existing page keeps working. Everything else is new:

    GET /api/logs/files            -> [{name, size, modified, exists}] for the picker
    GET /api/logs/view?file=<name> -> tail of that surface (whitelisted names only)

Names come from app/services/logging.LOG_FILES; an unknown name is a 404, never a path
traversal.
"""
from __future__ import annotations

import os
from datetime import datetime

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from app.services.logging import LOG_FILES, MAIN_LOG

router = APIRouter(prefix="/api/logs", tags=["logs"])


def _tail(path: str, lines: int) -> str:
    if not os.path.exists(path):
        return ""
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        # read up to ~256KB tail, then keep the last `lines` lines
        f.seek(max(0, f.tell() - 262144))
        data = f.read().decode("utf-8", errors="replace")
    return "\n".join(data.splitlines()[-lines:])


@router.get("", response_class=PlainTextResponse)
def get_logs(lines: int = 200):
    """Main log tail — the original endpoint, unchanged."""
    if not os.path.exists(MAIN_LOG):
        return "no log yet"
    return _tail(MAIN_LOG, lines)


@router.get("/files")
def list_logs():
    out = []
    for name, path in LOG_FILES.items():
        try:
            st = os.stat(path)
            out.append({
                "name": name,
                "size": st.st_size,
                "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
                "exists": True,
            })
        except OSError:
            out.append({"name": name, "size": 0, "modified": None, "exists": False})
    return JSONResponse(out)


@router.get("/view", response_class=PlainTextResponse)
def view_log(file: str = "mangafill", lines: int = 500):
    path = LOG_FILES.get(file)
    if path is None:
        raise HTTPException(404, f"unknown log '{file}' (have: {', '.join(LOG_FILES)})")
    if not os.path.exists(path):
        return f"no {file} log yet"
    return _tail(path, lines)
