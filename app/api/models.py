"""Model endpoints — list / add / delete the translation models.

Each model is an OpenAI-compatible {name, base_url, api_key}. The worker resolves
a job's `model_id` against this list (falling back to the first model).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.settings_store import add_model, delete_model, list_models

router = APIRouter(prefix="/api/models", tags=["models"])


class ModelIn(BaseModel):
    name: str = ""
    base_url: str = ""
    api_key: str = ""


@router.get("")
def get_models(db: Session = Depends(get_db)):
    return list_models(db)


@router.post("", status_code=201)
def create_model(payload: ModelIn, db: Session = Depends(get_db)):
    if not payload.name.strip() or not payload.base_url.strip():
        raise HTTPException(400, "name and base_url are required")
    return add_model(db, payload.name, payload.base_url, payload.api_key)


@router.delete("/{model_id}")
def remove_model(model_id: int, db: Session = Depends(get_db)):
    if not delete_model(db, model_id):
        raise HTTPException(404, "model not found")
    return {"ok": True}
