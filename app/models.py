"""ORM models — jobs, pages, text_blocks, settings, and the user-managed model list."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String, default="upload")
    name: Mapped[str] = mapped_column(String, default="")
    output_mode: Mapped[str] = mapped_column(String, default="folder")  # folder | cbz | mirror
    source_format: Mapped[str] = mapped_column(String, default="folder")  # input: folder | cbz | zip
    model_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # -> models.id (soft ref)
    status: Mapped[str] = mapped_column(String, default="queued")  # queued|running|done|partial|failed|cancelled
    stage: Mapped[str] = mapped_column(String, default="")  # current pipeline stage (detect|ocr|translate|inpaint|typeset)
    pages_total: Mapped[int] = mapped_column(Integer, default=0)
    pages_done: Mapped[int] = mapped_column(Integer, default=0)
    blocks_found: Mapped[int] = mapped_column(Integer, default=0)
    blocks_ok: Mapped[int] = mapped_column(Integer, default=0)
    tokens_used: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String, default=_now)
    updated_at: Mapped[str] = mapped_column(String, default=_now)
    finished_at: Mapped[str | None] = mapped_column(String, nullable=True)

    pages: Mapped[list["Page"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Page.index"
    )


class Page(Base):
    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    index: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|running|done|skipped|failed
    original_path: Mapped[str] = mapped_column(String, default="")
    output_path: Mapped[str] = mapped_column(String, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    job: Mapped["Job"] = relationship(back_populates="pages")
    blocks: Mapped[list["TextBlock"]] = relationship(
        back_populates="page", cascade="all, delete-orphan"
    )


class TextBlock(Base):
    __tablename__ = "text_blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id"))
    box: Mapped[str] = mapped_column(String, default="")
    orientation: Mapped[str] = mapped_column(String, default="vertical")
    jp_text: Mapped[str] = mapped_column(Text, default="")
    en_text: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    balloon_mask: Mapped[str | None] = mapped_column(String, nullable=True)

    page: Mapped["Page"] = relationship(back_populates="blocks")


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[str] = mapped_column(String, default=_now)


class Model(Base):
    """A translation model — any OpenAI-compatible endpoint (name + base_url + api_key)."""

    __tablename__ = "models"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, default="")
    base_url: Mapped[str] = mapped_column(String, default="")
    api_key: Mapped[str] = mapped_column(String, default="")
    # Pricing ($/1M tokens). Peak rates drive the cost display; off-peak rate +
    # window are optional (blank = no off-peak discount).
    price_in: Mapped[float] = mapped_column(Float, default=0.0)
    price_out: Mapped[float] = mapped_column(Float, default=0.0)
    offpeak_in: Mapped[float | None] = mapped_column(Float, nullable=True)
    offpeak_out: Mapped[float | None] = mapped_column(Float, nullable=True)
    offpeak_start: Mapped[str | None] = mapped_column(String, nullable=True)  # "HH:MM" UTC
    offpeak_end: Mapped[str | None] = mapped_column(String, nullable=True)    # "HH:MM" UTC
    created_at: Mapped[str] = mapped_column(String, default=_now)
