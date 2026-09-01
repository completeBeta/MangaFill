"""Model store + translation resolution (no ML model loading)."""
from __future__ import annotations

from app.db import SessionLocal, init_db
from app.services.job_engine import resolve_translation
from app.settings_store import add_model, default_model, delete_model, list_models, set_setting, update_model


def test_model_crud_and_resolve():
    init_db()
    db = SessionLocal()
    try:
        m = add_model(db, "test-model", "https://example.test/v1", "sk-abc",
                      price_in=2.0, price_out=8.0)
        assert m["id"] > 0
        assert m["name"] == "test-model"
        assert m["price_in"] == 2.0

        models = list_models(db)
        assert len(models) == 1 and models[0]["base_url"] == "https://example.test/v1"

        # resolve with an explicit id -> (model, dry_run)
        model_obj, dry = resolve_translation(db, m["id"])
        assert model_obj.name == "test-model"
        assert model_obj.base_url == "https://example.test/v1"
        assert model_obj.api_key == "sk-abc"
        assert dry is True  # dry_run defaults true

        # update pricing
        up = update_model(db, m["id"], price_in=1.5, offpeak_in=0.75,
                          offpeak_start="00:30", offpeak_end="16:30")
        assert up["price_in"] == 1.5
        assert up["offpeak_start"] == "00:30"

        # no id -> falls back to the first (default) model
        model2, _ = resolve_translation(db, None)
        assert model2.name == "test-model"
        assert default_model(db).name == "test-model"

        # no id -> resolves to None when the model was deleted
        assert delete_model(db, m["id"]) is True
        assert list_models(db) == []
        model3, _ = resolve_translation(db, None)
        assert model3 is None
    finally:
        db.close()


def test_output_mode_allows_mirror():
    init_db()
    db = SessionLocal()
    try:
        assert set_setting(db, "output_mode", "mirror") is True
        assert set_setting(db, "output_mode", "bogus") is False
        # restore the default so other tests see the expected value
        set_setting(db, "output_mode", "folder")
        db.commit()
    finally:
        db.close()
