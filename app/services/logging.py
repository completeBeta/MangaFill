"""Logging — the house standard, on several files, all readable from the web UI.

WHY SEVERAL FILES. One log for everything makes a job failure and a key rotation look
alike. Each surface gets its own file so a reader knows where to look:

    mangafill.log    everything the app emits (main log; the Logs page default)
    access.log       HTTP requests: method, path, status, duration
    lifecycle.log    state changes: job/page transitions, engine submissions, restarts
    engine.log       the upstream engine's OWN stdout/stderr (its container tees it into
                     the shared volume) — so the engine's warnings are visible here
    engine-calls.log one line per engine call WE make (page in, ms, status, bytes out)
    errors.log       WARNING and above from anywhere, so one file shows every failure

THE RULES (house standard — "logs that the app sends out
actually show up on the webui and behave as before"):
  * verbose by default: DEBUG (override with MANGA_FILL_LOG_LEVEL)
  * daily rotation, 45-day retention, rotation at local midnight
  * timestamps in the configured IANA timezone (Australia/Sydney by default) — never a
    hardcoded DST offset; unknown zone falls back to UTC
  * secrets redacted on the way into every file (api keys, bearer tokens, passwords)
  * every file is served by /api/logs (see app/api/logs.py)

Nothing here changes what the app does — only where and how it writes it down.
"""
from __future__ import annotations

import logging
import logging.handlers
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings

# --------------------------------------------------------------------------- paths
LOG_DIR = settings.logs_dir
# The engine container tees its stdout/stderr here through a shared volume.
ENGINE_DIR = settings.engine_logs_dir

MAIN_LOG = os.path.join(LOG_DIR, "mangafill.log")
ACCESS_LOG = os.path.join(LOG_DIR, "access.log")
LIFECYCLE_LOG = os.path.join(LOG_DIR, "lifecycle.log")
ENGINE_LOG = os.path.join(ENGINE_DIR, "engine.log")
ENGINE_CALLS_LOG = os.path.join(LOG_DIR, "engine-calls.log")
ERRORS_LOG = os.path.join(LOG_DIR, "errors.log")

# Kept for backward compatibility: the old single-file location, and the name older
# code paths import. `LOG_PATH` still points at the main log.
LOG_PATH = MAIN_LOG

# name -> path, for the API and the Logs page. Order is the order shown in the UI.
LOG_FILES: dict[str, str] = {
    "mangafill": MAIN_LOG,
    "access": ACCESS_LOG,
    "lifecycle": LIFECYCLE_LOG,
    "engine": ENGINE_LOG,
    "engine-calls": ENGINE_CALLS_LOG,
    "errors": ERRORS_LOG,
}

RETENTION_DAYS = 45
FMT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"

# --------------------------------------------------------------------------- redaction
_SECRET_PATTERNS = [
    # OpenAI-style keys. The `\b` matters: without it the old pattern ate the "sk-" in
    # ordinary stage names ("ma sk-generation" → "ma***REDACTED***"), corrupting the very
    # lines we need to read. Real keys are 20+ chars of [A-Za-z0-9] after the prefix.
    re.compile(r"(\bsk-[A-Za-z0-9]{20,}\b)"),
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.I),
    re.compile(r"((?:api[_-]?key|apikey|token|secret|password|passwd|auth[_-]?key)\s*[=:]\s*)([^\s,;\"')]{6,})", re.I),
    re.compile(r"(\"(?:api_?key|token|secret|password)\"\s*:\s*\")([^\"]{6,})", re.I),
    re.compile(r"\b([A-Za-z0-9_\-]{32,})\b"),                    # long opaque tokens
]


def redact(text: str) -> str:
    """Mask anything that looks like a credential. Conservative: never shortens prose."""
    out = text
    for i, pat in enumerate(_SECRET_PATTERNS):
        if i == 1:
            out = pat.sub(lambda m: f"{m.group(1)}***REDACTED***", out)
        elif i in (2, 3):
            out = pat.sub(lambda m: f"{m.group(1)}***REDACTED***", out)
        elif i == 4:
            out = pat.sub("***REDACTED***", out)
        else:
            out = pat.sub("***REDACTED***", out)
    return out


class _RedactingFilter(logging.Filter):
    """Formats the record once and scrubs it, so handlers cannot leak a key."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


def _zone(name: str):
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("UTC")


class _TZFormatter(logging.Formatter):
    """Timestamps in the configured IANA timezone (no hardcoded offsets)."""

    def __init__(self, fmt: str, tz_name: str):
        super().__init__(fmt)
        self._tz = _zone(tz_name)

    def formatTime(self, record, datefmt=None):  # noqa: N802 (logging API)
        dt = datetime.fromtimestamp(record.created, tz=self._tz)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S%z")


# --------------------------------------------------------------------------- setup
def _file_handler(path: str, level: int) -> logging.Handler:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    h = logging.handlers.TimedRotatingFileHandler(
        path, when="midnight", backupCount=RETENTION_DAYS, encoding="utf-8", delay=True
    )
    h.setFormatter(_TZFormatter(FMT, settings.timezone))
    h.setLevel(level)
    h.addFilter(_RedactingFilter())
    return h


_configured = False


def setup_logging() -> None:
    """Idempotent: safe to call from the app, the worker and tests."""
    global _configured
    if _configured:
        return
    _configured = True

    os.makedirs(LOG_DIR, exist_ok=True)
    os.makedirs(ENGINE_DIR, exist_ok=True)

    level = getattr(logging, str(settings.log_level or "DEBUG").upper(), logging.DEBUG)
    # Verbose by default per the house standard; MANGA_FILL_LOG_LEVEL raises/lowers it.
    if level == logging.INFO and os.environ.get("MANGA_FILL_LOG_LEVEL") is None:
        level = logging.DEBUG

    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    root.addHandler(_file_handler(MAIN_LOG, level))
    console = logging.StreamHandler()
    console.setFormatter(_TZFormatter(FMT, settings.timezone))
    console.setLevel(level)
    console.addFilter(_RedactingFilter())
    root.addHandler(console)

    # One logger per surface, each with its own file, none duplicating into main.
    for name, path, lvl in (
        ("mangafill.access", ACCESS_LOG, logging.INFO),
        ("mangafill.lifecycle", LIFECYCLE_LOG, level),
        ("mangafill.engine", ENGINE_CALLS_LOG, level),
    ):
        lg = logging.getLogger(name)
        lg.setLevel(lvl)
        lg.propagate = False
        for h in list(lg.handlers):
            lg.removeHandler(h)
        lg.addHandler(_file_handler(path, lvl))

    # errors.log: WARNING+ from EVERY logger, so one file holds every failure. Added to
    # the root logger (which catches everything that propagates) and to our own
    # non-propagating surfaces, which would otherwise never reach the root.
    err = logging.getLogger("mangafill.errors")
    err.setLevel(logging.WARNING)
    err.propagate = False
    err.addHandler(_file_handler(ERRORS_LOG, logging.WARNING))
    root.addHandler(_ErrorRouter())
    for name in ("mangafill.access", "mangafill.lifecycle", "mangafill.engine"):
        logging.getLogger(name).addHandler(_ErrorRouter())

    # Third-party noise: keep INFO from the server, but don't let chatty libs set DEBUG.
    for noisy in ("urllib3", "httpx", "httpcore", "PIL", "matplotlib"):
        logging.getLogger(noisy).setLevel(logging.INFO)

    logging.getLogger("mangafill").info(
        "logging started — dir=%s level=%s tz=%s retention=%dd files=%s",
        LOG_DIR, logging.getLevelName(level), settings.timezone, RETENTION_DAYS,
        ",".join(LOG_FILES),
    )


class _ErrorRouter(logging.Handler):
    """Forwards WARNING+ records to errors.log so every failure is in one file."""

    def __init__(self):
        super().__init__(logging.WARNING)
        self._inner = _file_handler(ERRORS_LOG, logging.WARNING)

    def emit(self, record: logging.LogRecord) -> None:
        if record.levelno >= logging.WARNING:
            self._inner.emit(record)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"mangafill.{name}")


def lifecycle(message: str, *args) -> None:
    """A state change worth being able to find later (job/page/engine transitions)."""
    get_logger("lifecycle").info(message, *args)


def engine_call(message: str, *args) -> None:
    """One line per engine request we make."""
    get_logger("engine").info(message, *args)


def access(message: str, *args) -> None:
    get_logger("access").info(message, *args)
