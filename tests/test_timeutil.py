from __future__ import annotations

from datetime import datetime, timezone

from src.timeutil import KST, format_kst, now_kst, today_kst


def test_utc_time_is_converted_to_kst_exactly_nine_hours_ahead():
    utc = datetime(2026, 8, 7, 2, 32, tzinfo=timezone.utc)
    assert format_kst(utc) == "2026-08-07 11:32"


def test_naive_time_is_treated_as_kst_for_display():
    naive = datetime(2026, 8, 7, 11, 32)
    assert format_kst(naive) == "2026-08-07 11:32"


def test_now_and_today_helpers_use_kst():
    current = now_kst()
    assert current.tzinfo == KST
    assert today_kst() == current.date()
