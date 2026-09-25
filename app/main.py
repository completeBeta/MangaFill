"""Manga Fill — FastAPI entrypoint.

The Phase 1 pipeline (detect → OCR → translate → inpaint → typeset → composite) is
a headless library under `app/pipeline/` with NO FastAPI imports, so it's testable
without a server. This module is the web wrapper: it serves the dashboard, the REST
API, and starts the single background worker.
"""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.api import (engine as engine_api, fonts as fonts_api, gpu as gpu_api, jobs, logs,
                     models, pages, settings as settings_api, stats as stats_api)
from app.config import settings
from app.db import init_db, SessionLocal
from app.pipeline.device import set_device
from app.services import audit
from app.services.logging import get_logger, setup_logging
from app.settings_store import seed_default_model
from app.worker import worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    set_device(settings.device)  # resolve local CPU/GPU before any model loads
    init_db()
    db = SessionLocal()
    try:
        seed_default_model(db)
    finally:
        db.close()
    worker.start()
    get_logger("app").info("Manga Fill v%s started", __version__)
    yield
    worker.stop()


app = FastAPI(title="Manga Fill", version=__version__, lifespan=lifespan)
templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(jobs.router)
app.include_router(pages.router)
app.include_router(settings_api.router)
app.include_router(engine_api.router)
app.include_router(fonts_api.router)
app.include_router(gpu_api.router)
app.include_router(models.router)
app.include_router(logs.router)
app.include_router(stats_api.router)


@app.middleware("http")
async def access_and_audit(request: Request, call_next):
    """Two surfaces, one pass.

    * access.log — EVERY request with method, path, status and duration. Dashboard
      polling of /api/jobs is logged at DEBUG so a 2-second poll cannot bury the lines
      that matter; everything else is INFO.
    * the structured audit trail — state-changing requests only (unchanged behaviour),
      recorded with status and duration, plus a richer line from endpoints that know
      more (e.g. how many jobs a Clear all removed).
    """
    start = time.perf_counter()
    client = request.client.host if request.client else "-"
    try:
        response = await call_next(request)
    except Exception as exc:  # noqa: BLE001 — log, then let FastAPI turn it into a 500
        get_logger("access").warning(
            "%s %s -> EXC %s (%.0fms) %s",
            request.method, request.url.path, type(exc).__name__,
            (time.perf_counter() - start) * 1000, client)
        raise
    ms = round((time.perf_counter() - start) * 1000)
    lvl = logging.DEBUG if (request.method == "GET"
                            and request.url.path.startswith(("/api/jobs", "/api/logs"))) else logging.INFO
    get_logger("access").log(lvl, "%s %s -> %s (%.0fms) %s",
                             request.method, request.url.path, response.status_code, ms, client)
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        audit.record(
            action=f"{request.method} {request.url.path}",
            target=(request.client.host if request.client else None),
            detail={"status": response.status_code, "ms": ms},
        )
    return response


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/api/audit")
async def get_audit(limit: int = 200) -> dict:
    """Structured audit trail, newest first — the durable record behind the log tail."""
    return {"entries": audit.recent(limit=max(1, min(limit, 2000)))}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {"version": __version__})
