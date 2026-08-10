"""Message formatting, separated from delivery logic."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from .timeutil import format_kst, now_kst


def group_schedules(schedules: list[dict[str, Any]]) -> list[dict[str, str]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in schedules:
        grouped[(item["date"], item["movie"], item["hall"])].append(item)
    result: list[dict[str, str]] = []
    for (date_text, movie, hall), items in sorted(grouped.items()):
        items.sort(key=lambda item: item["start"])
        result.append(
            {
                "date": date_text,
                "movie": movie,
                "hall": hall,
                "times": ", ".join(item["start"] for item in items),
            }
        )
    return result


def build_plain_alert(
    alert_title: str,
    schedules: list[dict[str, Any]],
    detected_at: datetime | None = None,
) -> str:
    at = detected_at or now_kst()
    lines = [
        f"🚨 NEW · {alert_title}",
        "",
        "새 상영 일정 오픈",
        f"⏰ 감지 시각: {format_kst(at)}",
        "━━━━━━━━━━━━━━━━━━━━",
        "",
    ]
    for group in group_schedules(schedules):
        lines.extend(
            [
                f"📅 {group['date']}",
                f"🎥 {group['movie']}",
                f"🏛 {group['hall']}",
                f"⏰ {group['times']}",
                "",
            ]
        )
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def build_rich_alert(
    alert_title: str,
    schedules: list[dict[str, Any]],
    detected_at: datetime | None = None,
) -> dict[str, Any]:
    at = detected_at or now_kst()
    blocks: list[dict[str, Any]] = [
        {"type": "heading", "text": f"🚨 NEW · {alert_title}", "size": 1},
        {
            "type": "paragraph",
            "text": f"새 상영 일정 오픈\n⏰ 감지 시각: {format_kst(at)}",
        },
        {"type": "divider"},
    ]
    for group in group_schedules(schedules):
        blocks.append(
            {
                "type": "paragraph",
                "text": (
                    f"📅 {group['date']}\n"
                    f"🎥 {group['movie']}\n"
                    f"🏛 {group['hall']}\n"
                    f"⏰ {group['times']}"
                ),
            }
        )
    blocks.append({"type": "divider"})
    return {"blocks": blocks, "skip_entity_detection": True}


def build_health_message(events: list[dict[str, str]], at: datetime | None = None) -> str:
    now = at or now_kst()
    has_down = any(event.get("kind") == "down" for event in events)
    title = "⚠️ 영화 예매 감시 상태 변경" if has_down else "✅ 영화 예매 감시 복구"
    lines = [title, f"⏰ {format_kst(now)}", ""]
    for event in events:
        if event["kind"] == "down":
            lines.append(f"❌ 장애: {event['name']}")
            lines.append(f"   {event['detail']}")
        else:
            lines.append(f"✅ 복구: {event['name']}")
        lines.append("")
    return "\n".join(lines).rstrip()


def build_heartbeat(
    watchers: list[dict[str, Any]],
    state: dict[str, Any],
    cycles: int,
    alerts: int,
    at: datetime | None = None,
) -> str:
    now = at or now_kst()
    lines = [
        "💓 영화 예매 감시 하트비트",
        f"⏰ 확인 시각: {format_kst(now)}",
        f"🔁 이번 세션 검사: {cycles}회",
        f"🔔 이번 세션 알림: {alerts}건",
        "",
    ]
    state_watchers = state.get("watchers", {})
    today_iso = now.date().isoformat()
    for watcher in watchers:
        active = any(str(iso_date) >= today_iso for iso_date in watcher.get("dates", []))
        if not active:
            lines.append(f"⏹ {watcher['name']}: 지정 날짜 종료")
            continue
        health = state_watchers.get(watcher["id"], {}).get("health", {})
        failures = int(health.get("consecutive_failures", 0) or 0)
        if failures:
            lines.append(f"⚠️ {watcher['name']}: 연속 실패 {failures}회")
        else:
            lines.append(f"✅ {watcher['name']}")
    return "\n".join(lines)


def build_selftest_rich(
    watchers: list[dict[str, Any]],
    at: datetime | None = None,
    *,
    match_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    now = at or now_kst()
    target_lines: list[str] = []
    for watcher in watchers:
        dates = ", ".join(str(value) for value in watcher.get("dates", []))
        movie = ", ".join(str(value) for value in watcher.get("movie_keywords", []))
        suffix = ""
        if match_counts is not None:
            suffix = f" · 현재 매칭 {int(match_counts.get(watcher['id'], 0))}개"
        target_lines.append(
            f"• {watcher['alert_title']} · {movie} · {dates}{suffix}"
        )
    targets = "\n".join(target_lines)
    return {
        "blocks": [
            {"type": "heading", "text": "🧪 영화 예매 알리미 v5 자체진단", "size": 1},
            {"type": "paragraph", "text": f"Rich Message 테스트\n⏰ 한국시간: {format_kst(now)}"},
            {"type": "divider"},
            {"type": "paragraph", "text": f"현재 감시 대상\n{targets}"},
            {"type": "divider"},
            {"type": "paragraph", "text": "이 메시지가 큰 제목으로 보이면 Rich Message 경로가 정상입니다."},
        ],
        "skip_entity_detection": True,
    }
