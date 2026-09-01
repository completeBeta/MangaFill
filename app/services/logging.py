"""Logging setup — verbose single-file log (tailed by /api/logs), plus console."""
from __future__ import annotations

import logging
import os

from app.config import settings

LOG_PATH = os.path.join(settings.jobs_dir, "mangafill.log")


def setup_logging() -> None:
    os.makedirs(settings.jobs_dir, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(),
        ],
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"mangafill.{name}")
