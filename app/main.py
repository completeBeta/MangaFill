"""Manga Fill — FastAPI entrypoint.

The Phase 1 pipeline (detect → OCR → translate → inpaint → typeset → composite) is
a headless library under `app/pipeline/` with NO FastAPI imports, so it's testable
without a server. This module is the web wrapper: it serves the dashboard, the REST
API, and starts the single background worker.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import __version__
from app.api import fonts as fonts_api, gpu as gpu_api, jobs, logs, models, pages, settings as settings_api
from app.config import settings
from app.db import init_db, SessionLocal
from app.pipeline.device import set_device
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


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html", {"version": __version__})
