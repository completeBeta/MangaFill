"""Unit tests for the GPU-worker client (`app.pipeline.remote`)."""
from __future__ import annotations

import io
from unittest import mock

from PIL import Image

from app.pipeline import remote


class _FakeResponse:
    def __init__(self, content=b"", json_data=None, status_code=200):
        self.content = content
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"http {self.status_code}")

    def json(self):
        return self._json


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "white").save(buf, format="PNG")
    return buf.getvalue()


def test_url_join_strips_trailing_slash():
    assert remote._url("http://h:9001/", "/detect-ocr") == "http://h:9001/detect-ocr"
    assert remote._url("http://h:9001", "/inpaint") == "http://h:9001/inpaint"


def test_remote_detect_ocr_returns_contract():
    img = Image.new("RGB", (8, 8), "white")
    resp = _FakeResponse(json_data={
        "bubble": [[0, 0, 4, 4]],
        "blocks": [{"bbox": [1, 1, 2, 2], "text": "あ", "orientation": "vertical"}],
    })
    with mock.patch("httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.post.return_value = resp
        out = remote.remote_detect_ocr(img, "http://h:9001")
    assert out["bubble"] == [[0, 0, 4, 4]]
    assert len(out["blocks"]) == 1
    assert out["blocks"][0]["text"] == "あ"


def test_remote_inpaint_returns_pil_image():
    img = Image.new("RGB", (8, 8), "white")
    resp = _FakeResponse(content=_png())
    with mock.patch("httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.post.return_value = resp
        out = remote.remote_inpaint(img, [(0, 0, 4, 4)], "http://h:9001")
    assert isinstance(out, Image.Image)
    assert out.size == (8, 8)


def test_remote_raises_on_http_error():
    img = Image.new("RGB", (8, 8), "white")
    resp = _FakeResponse(status_code=500)
    with mock.patch("httpx.Client") as MockClient:
        MockClient.return_value.__enter__.return_value.post.return_value = resp
        try:
            remote.remote_detect_ocr(img, "http://h:9001")
            raised = False
        except Exception:
            raised = True
    assert raised
