"""The engine's own language answer is per PAGE, and only an explicit setting outranks it.

THE DEFECT THIS PINS (measured in prod 2026-09-25, job 4)
A 2-page job whose FIRST page is Japanese left `lang="ja"` set for the whole job, so when the
SECOND page (Korean, a colour page) came back from the engine with `source=ko`, the guard
`lang not in ("ja","ko","zh")` refused to take the engine's answer. The render then went down
the ja path, its colour-page skip fired, and the Korean page came back with 0 blocks and no log
line at all — a silently blanked page, on a job that reported "done (2/2 pages, 0 failed)".

The engine READ that page; it is the authority on what language it is. Only a language the user
picked in Settings may override it.
"""
from __future__ import annotations

import pytest
from PIL import Image

from app.db import SessionLocal, init_db
from app.models import Job, Page
from app import settings_store
from app.services import job_engine


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    """Same shape as the other job tests: a throwaway jobs dir and real (created) tables."""
    monkeypatch.setattr(job_engine.settings, "jobs_dir", str(tmp_path))
    init_db()


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    session.close()


def _job_with_two_pages(db, tmp_path, name: str) -> int:
    job = Job(source="upload", name=name, output_mode="folder", status="queued", pages_total=2)
    db.add(job)
    db.commit()
    db.refresh(job)
    for i in range(2):
        path = tmp_path / f"p{i}.png"
        Image.new("RGB", (60, 80), (255, 0, 0)).save(path)
        db.add(Page(job_id=job.id, index=i, original_path=str(path)))
    db.commit()
    return job.id


def _configure(db, source_lang: str) -> None:
    settings_store.set_setting(db, "engine_enabled", "true")
    settings_store.set_setting(db, "engine_url", "http://engine.test:8000")
    settings_store.set_setting(db, "engine_lettering", "ours")
    settings_store.set_setting(db, "engine_preset_mode", "off")   # no probe (memory guard)
    settings_store.set_setting(db, "engine_second_pass", "off")
    settings_store.set_setting(db, "source_lang", source_lang)
    settings_store.set_setting(db, "dry_run", "false")
    db.commit()


def _stub_engine(monkeypatch, langs: list[str], seen: list[str]):
    """The engine answers with a different language per page; the render records its `lang`."""
    calls = {"n": 0}

    def fake_regions(image_path, config=None, timeout=None, tag=""):
        lang = langs[calls["n"]]
        calls["n"] += 1
        return ([{"x0": 1, "y0": 2, "x1": 30, "y1": 12, "text": "ソース",
                  "translation": "Source", "angle": 0.0, "prob": 0.9}], lang)

    def fake_render(image_path, *a, **kw):
        seen.append(kw.get("lang"))
        return Image.new("RGB", (60, 80), (0, 0, 0)), [], 0, 0, []

    monkeypatch.setattr(job_engine.engine_client, "translate_page_regions", fake_regions)
    monkeypatch.setattr(job_engine, "render_translated_page", fake_render)


def test_engine_language_is_taken_per_page_when_the_setting_is_auto(db, tmp_path, monkeypatch):
    job_id = _job_with_two_pages(db, tmp_path, "lang-auto")
    _configure(db, "auto")
    seen: list[str] = []
    _stub_engine(monkeypatch, ["ja", "ko"], seen)

    job_engine.process_job(job_id)

    # the SECOND page must be rendered as Korean — not with the first page's stale "ja"
    assert seen == ["ja", "ko"]


def test_an_explicit_setting_outranks_the_engine(db, tmp_path, monkeypatch):
    job_id = _job_with_two_pages(db, tmp_path, "lang-explicit")
    _configure(db, "ko")
    seen: list[str] = []
    _stub_engine(monkeypatch, ["ja", "ja"], seen)

    job_engine.process_job(job_id)

    assert seen == ["ko", "ko"]      # the user's setting is an instruction, not a guess
