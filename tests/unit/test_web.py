"""Web-app backend tests — models, DB, ingest, natural sort. No ML model loading."""
from __future__ import annotations

import io
import os

from app.db import SessionLocal, init_db
from app.models import Job, Page, TextBlock
from app.services.job_engine import _natural_key, ingest_upload


class FakeUpload:
    def __init__(self, name: str, data: bytes = b"fake-image-bytes"):
        self.filename = name
        self.file = io.BytesIO(data)  # mimic FastAPI UploadFile (has .file + .filename)


def test_natural_key_sorts_pages_numerically():
    names = ["page-10.jpg", "page-2.jpg", "page-1.jpg", "page-20.png"]
    assert sorted(names, key=_natural_key) == ["page-1.jpg", "page-2.jpg", "page-10.jpg", "page-20.png"]


def test_init_db_and_crud():
    init_db()
    db = SessionLocal()
    try:
        job = Job(name="test", status="done")  # "done" so the worker never claims it
        db.add(job)
        db.commit()
        db.add(Page(job_id=job.id, index=0, original_path="/tmp/x/y.jpg"))
        db.add(Page(job_id=job.id, index=1, original_path="/tmp/x/z.jpg"))
        db.commit()
        assert db.query(Page).filter(Page.job_id == job.id).count() == 2
        assert db.query(Job).filter(Job.name == "test").count() == 1

        db.add(TextBlock(page_id=1, jp_text="ほえ", en_text="Huh?", box="[0,0,10,10]"))
        db.commit()
    finally:
        db.close()


def test_ingest_upload_saves_and_sorts():
    init_db()
    db = SessionLocal()
    try:
        job = Job(name="ingest", status="done")
        db.add(job)
        db.commit()
        db.refresh(job)
        files = [FakeUpload("page-002.png"), FakeUpload("page-010.png"), FakeUpload("page-001.png")]
        paths = ingest_upload(job.id, files)
        assert len(paths) == 3
        assert [os.path.basename(p) for p in paths] == ["page-001.png", "page-002.png", "page-010.png"]
        assert all(os.path.exists(p) for p in paths)
    finally:
        db.close()


def test_ingest_upload_rejects_non_images():
    init_db()
    db = SessionLocal()
    try:
        job = Job(name="ingest2", status="done")
        db.add(job)
        db.commit()
        db.refresh(job)
        paths = ingest_upload(job.id, [FakeUpload("notes.txt")])
        assert paths == []
    finally:
        db.close()
