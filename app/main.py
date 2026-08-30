"""Manga Fill — FastAPI entrypoint.

Phase 1 pipeline (detect → OCR → translate → inpaint → typeset → composite) is a
headless library under `app/pipeline/` with NO FastAPI imports, so it's testable
without a server. This module is only the web wrapper.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app import __version__


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Milestone 7+: start the background worker / scheduler here.
    yield


app = FastAPI(title="Manga Fill", version=__version__, lifespan=lifespan)
templates = Jinja2Templates(directory="templates")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/", response_class=HTMLResponse)
async def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "index.html", {"version": __version__}
    )
