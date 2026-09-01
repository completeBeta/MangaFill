"""Model store + translation resolution (no ML model loading)."""
from __future__ import annotations

from app.db import SessionLocal, init_db
from app.services.job_engine import resolve_translation
from app.settings_store import add_model, default_model, delete_model, list_models


def test_model_crud_and_resolve():
    init_db()
    db = SessionLocal()
    try:
        m = add_model(db, "test-model", "https://example.test/v1", "sk-abc")
        assert m["id"] > 0
        assert m["name"] == "test-model"

        models = list_models(db)
        assert len(models) == 1 and models[0]["base_url"] == "https://example.test/v1"

        # resolve with an explicit id
        model, base_url, key, dry = resolve_translation(db, m["id"])
        assert model == "test-model"
        assert base_url == "https://example.test/v1"
        assert key == "sk-abc"
        assert dry is True  # dry_run defaults true

        # no id -> falls back to the first (default) model
        model2, _, _, _ = resolve_translation(db, None)
        assert model2 == "test-model"
        assert default_model(db).name == "test-model"

        # no id -> resolves to empty when the model was deleted
        assert delete_model(db, m["id"]) is True
        assert list_models(db) == []
        model3, _, _, _ = resolve_translation(db, None)
        assert model3 == ""
    finally:
        db.close()
