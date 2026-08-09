from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.formatter import build_heartbeat, build_plain_alert, build_rich_alert, group_schedules

KST = ZoneInfo("Asia/Seoul")


def schedules():
    return [
        {"date": "2026-08-21 (금)", "movie": "오디세이", "hall": "IMAX관", "start": "07:00"},
        {"date": "2026-08-21 (금)", "movie": "오디세이", "hall": "IMAX관", "start": "10:30"},
    ]


def test_group_schedules_combines_times():
    grouped = group_schedules(schedules())
    assert grouped[0]["times"] == "07:00, 10:30"


def test_plain_uses_kst_time(kst_noon):
    text = build_plain_alert("용산CGV", schedules(), kst_noon)
    assert "2026-08-08 12:34" in text
    assert "NEW · 용산CGV" in text


def test_rich_schema_heading_size_one(kst_noon):
    rich = build_rich_alert("용산CGV", schedules(), kst_noon)
    assert rich["blocks"][0] == {"type": "heading", "text": "🚨 NEW · 용산CGV", "size": 1}
    assert rich["skip_entity_detection"] is True


def test_heartbeat_marks_expired_watcher_instead_of_green(base_config):
    watcher = dict(base_config["watchers"][0])
    watcher["dates"] = ["2026-08-01"]
    text = build_heartbeat(
        [watcher],
        {"watchers": {watcher["id"]: {"health": {"consecutive_failures": 0}}}},
        5,
        0,
        at=datetime(2026, 8, 8, 9, 1, tzinfo=KST),
    )
    assert "⏹" in text
    assert "지정 날짜 종료" in text
    assert f"✅ {watcher['name']}" not in text
