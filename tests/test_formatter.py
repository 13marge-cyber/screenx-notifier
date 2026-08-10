from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from src.formatter import build_heartbeat, build_plain_alert, build_rich_alert, build_selftest_rich, group_schedules

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
    text = build_plain_alert("용산 IMAX", schedules(), kst_noon)
    assert "2026-08-08 12:34" in text
    assert "NEW · 용산 IMAX" in text


def test_rich_schema_heading_size_one(kst_noon):
    rich = build_rich_alert("용산 IMAX", schedules(), kst_noon)
    assert rich["blocks"][0] == {"type": "heading", "text": "🚨 NEW · 용산 IMAX", "size": 1}
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


def test_imax_alert_title_is_immediately_visible(kst_noon):
    text = build_plain_alert("용산 IMAX", schedules(), kst_noon)
    assert text.splitlines()[0] == "🚨 NEW · 용산 IMAX"
    rich = build_rich_alert("광교 IMAX", schedules(), kst_noon)
    assert rich["blocks"][0] == {"type": "heading", "text": "🚨 NEW · 광교 IMAX", "size": 1}


def test_selftest_rich_lists_all_live_targets(base_config, kst_noon):
    watchers = []
    for code, title in [("0013", "용산 IMAX"), ("0199", "천호 IMAX"), ("0257", "광교 IMAX")]:
        watcher = dict(base_config["watchers"][0])
        watcher["id"] = f"w_{code}"
        watcher["theater_code"] = code
        watcher["alert_title"] = title
        watcher["dates"] = ["2026-08-29", "2026-08-30"]
        watchers.append(watcher)
    counts = {watcher["id"]: index for index, watcher in enumerate(watchers)}
    rich = build_selftest_rich(watchers, kst_noon, match_counts=counts)
    text = "\n".join(str(block.get("text", "")) for block in rich["blocks"])
    assert "용산 IMAX · 오디세이 · 2026-08-29, 2026-08-30" in text
    assert "천호 IMAX · 오디세이 · 2026-08-29, 2026-08-30" in text
    assert "광교 IMAX · 오디세이 · 2026-08-29, 2026-08-30" in text
    assert "현재 매칭 0개" in text
    assert "현재 매칭 2개" in text


def test_megabox_dolby_alert_groups_two_movies_without_losing_title(kst_noon):
    items = [
        {"date": "2026-08-14 (금)", "movie": "오디세이", "hall": "DOLBY CINEMA", "start": "09:10"},
        {"date": "2026-08-14 (금)", "movie": "스파이더맨: 브랜드 뉴 데이", "hall": "DOLBY CINEMA", "start": "12:40"},
    ]
    text = build_plain_alert("수원AK Dolby", items, kst_noon)
    assert text.splitlines()[0] == "🚨 NEW · 수원AK Dolby"
    assert "🎥 오디세이" in text
    assert "🎥 스파이더맨: 브랜드 뉴 데이" in text
    assert text.count("🏛 DOLBY CINEMA") == 2


def test_selftest_rich_lists_megabox_target_alongside_three_cgv_targets(base_config, kst_noon):
    cgv_watchers = []
    for code, title in [("0013", "용산 IMAX"), ("0199", "천호 IMAX"), ("0257", "광교 IMAX")]:
        watcher = dict(base_config["watchers"][0])
        watcher.update({
            "id": f"cgv_{code}", "provider": "cgv", "theater_code": code,
            "alert_title": title, "movie_keywords": ["오디세이"],
            "dates": ["2026-08-29", "2026-08-30"],
        })
        cgv_watchers.append(watcher)
    mb = dict(base_config["watchers"][0])
    mb.update({
        "id": "megabox_0052", "provider": "megabox", "theater_code": "0052",
        "alert_title": "수원AK Dolby", "movie_keywords": ["오디세이", "스파이더맨 브랜드 뉴 데이"],
        "dates": ["2026-08-15", "2026-08-16", "2026-08-17"],
    })
    watchers = cgv_watchers + [mb]
    rich = build_selftest_rich(watchers, kst_noon, match_counts={w["id"]: 0 for w in watchers})
    text = "\n".join(str(block.get("text", "")) for block in rich["blocks"])
    assert "수원AK Dolby · 오디세이, 스파이더맨 브랜드 뉴 데이 · 2026-08-15, 2026-08-16, 2026-08-17" in text
