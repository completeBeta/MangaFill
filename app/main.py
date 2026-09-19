"""Manga Fill — FastAPI entrypoint.

The Phase 1 pipeline (detect → OCR → translate → inpaint → typeset → composite) is
a headless library under `app/pipeline/` with NO FastAPI imports, so it's testable
without a server. This module is the web wrapper: it serves the dashboard, the REST
API, and starts the single background worker.
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.api import (fonts as fonts_api, gpu as gpu_api, jobs, logs, models, pages,
                     settings as settings_api, stats as stats_api)
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
app.include_router(fonts_api.router)
app.include_router(gpu_api.router)
app.include_router(models.router)
app.include_router(logs.router)
app.include_router(stats_api.router)


@app.middleware("http")
async def audit_mutations(request: Request, call_next):
    """Record every state-changing request so no action goes unlogged.

    Reads (GET) are deliberately excluded: the dashboard polls /api/jobs every few
    seconds, and logging those would bury the real actions. Every POST/PUT/PATCH/
    DELETE is recorded with its status and duration, which guarantees coverage even
    for an endpoint nobody remembered to instrument. Endpoints that know more about
    what happened (e.g. how many jobs a Clear all removed) add a richer AUDIT line
    of their own, right next to this one.
    """
    start = time.perf_counter()
    response = await call_next(request)
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        audit.record(
            action=f"{request.method} {request.url.path}",
            target=(request.client.host if request.client else None),
            detail={"status": response.status_code,
                    "ms": round((time.perf_counter() - start) * 1000)},
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
