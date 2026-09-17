"""Cost tally — totals for a calendar window (day / week / month / year).

The dashboard shows one number the user actually asks for: what has this cost
*lately*, with a period selector that defaults to today. Windows are CALENDAR
windows in the app's local timezone (today, this week from Monday, this calendar
month, this calendar year), not rolling 24h/7d windows, because that is what
"this week's spend" means to a person reading a bill.

A job's tokens/cost accumulate in its own row (a re-render adds to the same job),
so a job is attributed to the window containing the last thing that happened to
it — `finished_at`, else `updated_at`, else `created_at`.

Also exposes the all-time total so the UI can show "today / all time" together.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Job

router = APIRouter(prefix="/api/stats", tags=["stats"])

PERIODS = ("day", "week", "month", "year")
DEFAULT_PERIOD = "day"

try:  # Python 3.9+ stdlib; the container ships tzdata
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover - defensive
    ZoneInfo = None  # type: ignore[assignment]


def resolve_tz(name: str | None):
    """The zone for the calendar windows, falling back to UTC when unknown."""
    if name and ZoneInfo is not None:
        try:
            return ZoneInfo(name)
        except Exception:
            pass
    return timezone.utc


def parse_dt(value) -> datetime | None:
    """Parse a stored ISO timestamp (tolerating a trailing 'Z' / naive strings)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def period_start(period: str, now: datetime, tz) -> datetime:
    """UTC instant the window opens at: start of today / this week / month / year."""
    local = now.astimezone(tz)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "week":
        start = midnight - timedelta(days=midnight.weekday())  # Monday
    elif period == "month":
        start = midnight.replace(day=1)
    elif period == "year":
        start = midnight.replace(month=1, day=1)
    else:
        start = midnight
    return start.astimezone(timezone.utc)


def job_timestamp(job: Job) -> datetime | None:
    """When a job's spend landed: finished, else last update, else created."""
    for value in (job.finished_at, job.updated_at, job.created_at):
        dt = parse_dt(value)
        if dt is not None:
            return dt
    return None


def _totals(jobs: list[Job]) -> dict:
    return {
        "jobs": len(jobs),
        "pages_done": sum(j.pages_done or 0 for j in jobs),
        "blocks_found": sum(j.blocks_found or 0 for j in jobs),
        "blocks_ok": sum(j.blocks_ok or 0 for j in jobs),
        "tokens": sum(j.tokens_used or 0 for j in jobs),
        "cost_usd": round(sum(j.cost_usd or 0.0 for j in jobs), 6),
    }


@router.get("/cost")
def cost_tally(period: str = DEFAULT_PERIOD, tz: str | None = None,
               db: Session = Depends(get_db)):
    """Totals for the selected calendar window + the all-time total.

    `period` is one of day / week / month / year (default day). `tz` overrides the
    timezone used for the window boundaries (an IANA name, e.g. Australia/Sydney);
    the app's configured timezone is the default so "today" matches the clock the
    user is looking at.
    """
    from app.config import settings as app_settings

    p = (period or DEFAULT_PERIOD).lower()
    if p not in PERIODS:
        p = DEFAULT_PERIOD
    zone = resolve_tz(tz or getattr(app_settings, "timezone", None))
    now = datetime.now(timezone.utc)
    start = period_start(p, now, zone)

    jobs = db.query(Job).all()
    window = [j for j in jobs if (ts := job_timestamp(j)) is not None and ts >= start]
    return {
        "period": p,
        "periods": list(PERIODS),
        "since": start.isoformat(),
        "until": now.isoformat(),
        "timezone": str(zone),
        "window": _totals(window),
        "all_time": _totals(jobs),
    }
