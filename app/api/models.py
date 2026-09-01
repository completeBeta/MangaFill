"""Model endpoints — list / add / edit / delete the translation models.

Each model is an OpenAI-compatible {name, base_url, api_key} with optional
peak/off-peak pricing (all optional except name + base_url). The worker resolves
a job's `model_id` against this list (falling back to the first model).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.settings_store import add_model, delete_model, list_models, update_model

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelIn(BaseModel):
    name: str = ""
    base_url: str = ""
    api_key: str = ""
    price_in: float = 0.0
    price_out: float = 0.0
    offpeak_in: float | None = None
    offpeak_out: float | None = None
    offpeak_start: str | None = None
    offpeak_end: str | None = None


@router.get("")
def get_models(db: Session = Depends(get_db)):
    return list_models(db)


@router.post("", status_code=201)
def create_model(payload: ModelIn, db: Session = Depends(get_db)):
    if not payload.name.strip() or not payload.base_url.strip():
        raise HTTPException(400, "name and base_url are required")
    return add_model(
        db, payload.name, payload.base_url, payload.api_key,
        payload.price_in, payload.price_out,
        payload.offpeak_in, payload.offpeak_out,
        payload.offpeak_start, payload.offpeak_end,
    )


@router.put("/{model_id}")
def edit_model(model_id: int, payload: ModelIn, db: Session = Depends(get_db)):
    if not payload.name.strip() or not payload.base_url.strip():
        raise HTTPException(400, "name and base_url are required")
    m = update_model(
        db, model_id,
        name=payload.name.strip(),
        base_url=payload.base_url.strip(),
        api_key=payload.api_key,
        price_in=payload.price_in,
        price_out=payload.price_out,
        offpeak_in=payload.offpeak_in,
        offpeak_out=payload.offpeak_out,
        offpeak_start=payload.offpeak_start,
        offpeak_end=payload.offpeak_end,
    )
    if m is None:
        raise HTTPException(404, "model not found")
    return m


@router.delete("/{model_id}")
def remove_model(model_id: int, db: Session = Depends(get_db)):
    if not delete_model(db, model_id):
        raise HTTPException(404, "model not found")
    return {"ok": True}
