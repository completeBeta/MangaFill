"""Upload ingest streams to disk + detects source format (never whole-file in RAM)."""
from __future__ import annotations

import io
import os
import zipfile

from PIL import Image

from app.config import settings
from app.services.job_engine import _assemble, _save_output, ingest_upload


class _FakeUpload:
    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._io = io.BytesIO(data)

    @property
    def file(self):
        return self._io


def _archive(pages: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
        for name, data in pages.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_ingest_cbz_streams_and_detects_format(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "jobs_dir", str(tmp_path))
    cbz = _archive({"page-001.jpg": b"FAKEJPG1", "page-002.jpg": b"FAKEJPG2"})
    paths, fmt = ingest_upload(1, [_FakeUpload("vol1.cbz", cbz)])
    assert fmt == "cbz"
    assert len(paths) == 2
    assert all(os.path.exists(p) for p in paths)
    # staging archive is removed after expansion
    assert "vol1.cbz" not in os.listdir(os.path.dirname(paths[0]))


def test_ingest_zip_detects_zip(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "jobs_dir", str(tmp_path))
    paths, fmt = ingest_upload(1, [_FakeUpload("vol1.zip", _archive({"a.png": b"FAKEPNG"}))])
    assert fmt == "zip"
    assert len(paths) == 1


def test_ingest_images_detects_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "jobs_dir", str(tmp_path))
    paths, fmt = ingest_upload(1, [_FakeUpload("p1.jpg", b"FAKEJPG")])
    assert fmt == "folder"
    assert len(paths) == 1


def test_assemble_mirrors_input_format(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "jobs_dir", str(tmp_path))
    out_dir = os.path.join(str(tmp_path), "1", "output")
    os.makedirs(out_dir)
    for n in ("0000.png", "0001.png"):
        with open(os.path.join(out_dir, n), "wb") as f:
            f.write(b"fake-page")

    # mirror + cbz input -> .cbz
    assert _assemble(1, "mirror", "cbz") is None
    assert os.path.exists(os.path.join(str(tmp_path), "1", "translated.cbz"))

    # mirror + zip input -> .zip
    assert _assemble(1, "mirror", "zip") is None
    assert os.path.exists(os.path.join(str(tmp_path), "1", "translated.zip"))

    # mirror + folder input -> no archive (leave as-is)
    assert _assemble(1, "mirror", "folder") is None

    # explicit cbz mode always -> .cbz
    assert _assemble(1, "cbz", "folder") is None
    assert os.path.exists(os.path.join(str(tmp_path), "1", "translated.cbz"))


def test_save_output_preserves_filename(tmp_path):
    img = Image.new("RGB", (12, 12), "white")
    out = _save_output(img, str(tmp_path), "/some/job/original/page-033.jpg")
    assert os.path.basename(out) == "page-033.jpg"
    assert os.path.exists(out)


def test_download_ext_round_trips_upload_format():
    from app.api.jobs import _download_ext

    # folder mode (default): round-trip the upload format
    assert _download_ext("folder", "cbz") == "cbz"
    assert _download_ext("folder", "zip") == "zip"
    assert _download_ext("folder", "folder") == "cbz"  # images -> .cbz default

    # mirror mode: match the input format
    assert _download_ext("mirror", "cbz") == "cbz"
    assert _download_ext("mirror", "zip") == "zip"
    assert _download_ext("mirror", "folder") == "cbz"

    # explicit cbz mode always -> .cbz
    assert _download_ext("cbz", "zip") == "cbz"
    assert _download_ext("cbz", "folder") == "cbz"
