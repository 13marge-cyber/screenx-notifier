"""Korea Standard Time helpers for movie booking notifier v5."""
from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    KST = ZoneInfo("Asia/Seoul")
except ZoneInfoNotFoundError as exc:  # pragma: no cover - platform dependency guard
    raise RuntimeError(
        "Asia/Seoul time-zone data is unavailable. Install runtime dependencies "
        "with: python -m pip install -r requirements.txt"
    ) from exc


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> date:
    return now_kst().date()


def ensure_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=KST)
    return value.astimezone(KST)


def format_kst(value: datetime | None = None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return ensure_kst(value or now_kst()).strftime(fmt)
