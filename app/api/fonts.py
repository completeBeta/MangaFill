"""Fonts endpoints — list the lettering fonts + render a live preview swatch.

The preview is drawn server-side with the *actual* font file (PIL), so what you
see is exactly what typeset will produce. The sample text is a single generic
word ("Dialogue") — never copyrighted source material.
"""
from __future__ import annotations

import io

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from app.db import get_db
from app.pipeline import fonts as fontlib
from app.settings_store import get_setting

router = APIRouter(prefix="/api/fonts", tags=["fonts"])

PREVIEW_TEXT = "Dialogue"
PREVIEW_FONT_SIZE = 36
PREVIEW_BG = (255, 255, 255)
PREVIEW_INK = (18, 18, 18)


@router.get("")
def list_fonts(db: Session = Depends(get_db)):
    selected = get_setting(db, "font")
    resolved = fontlib.resolve_font(selected)[1]
    return {"fonts": fontlib.list_fonts(), "selected": selected, "resolved": resolved}


@router.get("/preview/{font_id}")
def preview_font(font_id: str):
    path = fontlib.font_path(font_id)
    if path is None:
        raise HTTPException(status_code=404, detail="font unavailable")

    try:
        font = ImageFont.truetype(path, PREVIEW_FONT_SIZE)
    except Exception as exc:  # corrupt/unsupported file
        raise HTTPException(status_code=500, detail=f"font load failed: {exc}")

    # Measure the text so the swatch hugs the glyphs (variable widths are fine).
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    bb = probe.textbbox((0, 0), PREVIEW_TEXT, font=font)
    tw, th = int(bb[2] - bb[0]), int(bb[3] - bb[1])
    pad = 12
    img = Image.new("RGB", (tw + 2 * pad, th + 2 * pad), PREVIEW_BG)
    draw = ImageDraw.Draw(img)
    draw.text((int(pad - bb[0]), int(pad - bb[1])), PREVIEW_TEXT, font=font, fill=PREVIEW_INK)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(content=buf.getvalue(), media_type="image/png")
