"""Core v4 monitor loop: detection, date-transaction alerts, health, and heartbeat."""
from __future__ import annotations

import logging
import signal
import threading
import time
from collections import defaultdict
from datetime import datetime
from typing import Any, Callable

from .cgv import CGVClient, showtime_key
from .config import enabled_watchers
from .formatter import build_health_message, build_heartbeat, build_plain_alert, build_rich_alert
from .state import StateError, StateManager
from .telegram import TelegramClient
from .timeutil import now_kst

logger = logging.getLogger(__name__)


class Monitor:
    def __init__(
        self,
        config: dict[str, Any],
        state: StateManager,
        cgv: CGVClient,
        telegram: TelegramClient,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        now_fn: Callable[[], datetime] = now_kst,
    ):
        self.config = config
        self.state = state
        self.cgv = cgv
        self.telegram = telegram
        self.monotonic = monotonic
        self.now_fn = now_fn
        self.watchers = enabled_watchers(config)
        self.failure_threshold = int(config["monitor"]["failure_alert_threshold"])
        self.interval = int(config["monitor"]["interval_seconds"])
        self.stop_event = threading.Event()
        self.session_cycles = 0
        self.session_alerts = 0

    def request_stop(self, *_args: Any) -> None:
        logger.info("종료 신호를 받았습니다. 현재 작업 후 안전하게 종료합니다.")
        self.stop_event.set()

    def install_signal_handlers(self) -> None:
        try:
            signal.signal(signal.SIGTERM, self.request_stop)
            signal.signal(signal.SIGINT, self.request_stop)
        except ValueError:
            pass

    @staticmethod
    def _health_detail(result: dict[str, Any]) -> str:
        failed = result.get("failed_dates", [])
        errors = result.get("errors", {})
        if not failed:
            return ""
        first = failed[0]
        first_error = errors.get(first, "조회 실패")
        return f"실패 날짜 {len(failed)}개 ({first}: {first_error})"

    def _update_health(
        self,
        watcher: dict[str, Any],
        result: dict[str, Any],
        at: datetime,
        events: list[dict[str, str]],
    ) -> None:
        item = self.state.watcher(watcher["id"])
        health = item["health"]
        active_dates = result.get("active_dates", [])
        if not active_dates:
            return
        failed_dates = result.get("failed_dates", [])
        if failed_dates:
            health["consecutive_failures"] = int(health.get("consecutive_failures", 0) or 0) + 1
            health["last_error"] = self._health_detail(result)
            if (
                health["consecutive_failures"] >= self.failure_threshold
                and not health.get("down_notified", False)
            ):
                events.append(
                    {
                        "kind": "down",
                        "id": watcher["id"],
                        "name": watcher["name"],
                        "detail": f"{health['consecutive_failures']}회 연속 · {health['last_error']}",
                    }
                )
        else:
            was_down = bool(health.get("down_notified", False))
            health["consecutive_failures"] = 0
            health["last_error"] = None
            health["last_success_at"] = at.isoformat()
            if was_down:
                events.append(
                    {
                        "kind": "recovered",
                        "id": watcher["id"],
                        "name": watcher["name"],
                        "detail": "",
                    }
                )

    def _apply_health_event_delivery(self, events: list[dict[str, str]]) -> None:
        for event in events:
            health = self.state.watcher(event["id"])["health"]
            health["down_notified"] = event["kind"] == "down"
        self.state.save()

    def _heartbeat_due(self, at: datetime) -> bool:
        heartbeat = self.config["monitor"]["heartbeat"]
        if not heartbeat["enabled"]:
            return False
        hour, minute = (int(part) for part in heartbeat["time"].split(":", 1))
        if (at.hour, at.minute) < (hour, minute):
            return False
        return self.state.meta().get("last_heartbeat_date") != at.date().isoformat()

    def _maybe_heartbeat(self, at: datetime) -> None:
        if not self._heartbeat_due(at):
            return
        message = build_heartbeat(
            self.watchers,
            self.state.snapshot(),
            self.session_cycles,
            self.session_alerts,
            at=at,
        )
        if self.telegram.send_plain(message):
            self.state.meta()["last_heartbeat_date"] = at.date().isoformat()
            self.state.save()
            logger.info("하트비트 전송 완료")
        else:
            logger.error("하트비트 전송 실패 - 다음 사이클에서 재시도")

    def _isolated_scan(self, watcher: dict[str, Any], at: datetime) -> dict[str, Any]:
        try:
            return self.cgv.scan_watcher(watcher, today=at.date())
        except StateError:
            raise
        except Exception as exc:
            # A parser/CGV bug in one watcher must not kill the other watchers.
            try:
                active_dates = self.cgv.active_dates(watcher, today=at.date())
            except Exception:
                active_dates = [value.replace("-", "") for value in watcher.get("dates", []) if value >= at.date().isoformat()]
            error = f"내부 watcher 오류({type(exc).__name__})"
            logger.exception("[%s] watcher 내부 오류를 격리했습니다.", watcher["name"])
            return {
                "schedules": [],
                "failed_dates": list(active_dates),
                "errors": {date_raw: error for date_raw in active_dates},
                "error_kinds": {date_raw: "internal" for date_raw in active_dates},
                "active_dates": list(active_dates),
            }

    @staticmethod
    def _unique_new_by_date(
        schedules: list[dict[str, Any]], known: set[str]
    ) -> dict[str, list[dict[str, Any]]]:
        by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
        seen: set[str] = set()
        for item in schedules:
            key = showtime_key(item)
            if key in seen or key in known:
                continue
            seen.add(key)
            by_date[item["date_raw"]].append(item)
        return dict(sorted(by_date.items()))

    def run_cycle(self, at: datetime | None = None) -> dict[str, Any]:
        detected_at = at or self.now_fn()
        self.cgv.start_cycle()
        self.session_cycles += 1
        health_events: list[dict[str, str]] = []
        cycle_new = 0
        cycle_alerts = 0

        for watcher in self.watchers:
            result = self._isolated_scan(watcher, detected_at)
            state_item = self.state.watcher(watcher["id"])
            known = set(state_item["known_keys"])
            schedules = result["schedules"]
            new_by_date = self._unique_new_by_date(schedules, known)
            cycle_new += sum(len(items) for items in new_by_date.values())

            if not result.get("active_dates"):
                logger.info("[%s] 활성 감시 날짜 없음 (지정 날짜 종료)", watcher["name"])

            if new_by_date:
                for date_raw, date_schedules in new_by_date.items():
                    logger.info(
                        "[%s] %s 신규 회차 %d개 감지",
                        watcher["name"],
                        date_raw,
                        len(date_schedules),
                    )
                    rich = build_rich_alert(watcher["alert_title"], date_schedules, detected_at)
                    plain = build_plain_alert(watcher["alert_title"], date_schedules, detected_at)
                    if self.telegram.send_alert(rich, plain):
                        delivered_keys = {showtime_key(item) for item in date_schedules}
                        known.update(delivered_keys)
                        # StateManager.save()/watcher() validate and normalize the tree, so a
                        # previously-held nested dict reference may become stale after a save.
                        # Re-acquire the watcher for every date transaction and union keys.
                        latest_item = self.state.watcher(watcher["id"])
                        latest_known = set(latest_item["known_keys"])
                        latest_item["known_keys"] = sorted(latest_known | delivered_keys)
                        self.state.save()  # Commit only this successfully delivered date.
                        cycle_alerts += 1
                        self.session_alerts += 1
                        logger.info(
                            "[%s] %s Telegram 알림 성공 및 해당 날짜 상태 저장 완료",
                            watcher["name"],
                            date_raw,
                        )
                    else:
                        logger.error(
                            "[%s] %s Telegram 알림 실패 - 해당 날짜 known_keys 미반영",
                            watcher["name"],
                            date_raw,
                        )
            else:
                logger.info(
                    "[%s] 신규 회차 없음 (매칭 %d개, 실패 날짜 %d개)",
                    watcher["name"],
                    len(schedules),
                    len(result["failed_dates"]),
                )

            self._update_health(watcher, result, detected_at, health_events)

        self.state.save()
        if health_events:
            if self.telegram.send_plain(build_health_message(health_events, detected_at)):
                self._apply_health_event_delivery(health_events)
                logger.info("감시 장애/복구 상태 알림 전송 완료")
            else:
                logger.error("감시 장애/복구 상태 알림 실패 - 다음 사이클에서 재시도")
        self._maybe_heartbeat(detected_at)
        return {
            "new_showtimes": cycle_new,
            "alerts": cycle_alerts,
            "health_events": health_events,
        }

    def run_forever(self, runtime_minutes: float) -> None:
        self.install_signal_handlers()
        started = self.monotonic()
        runtime_seconds = max(1.0, float(runtime_minutes) * 60.0)
        next_tick = started
        logger.info(
            "v4 감시 시작: %d초 주기, 최대 %.1f분, watcher %d개",
            self.interval,
            runtime_minutes,
            len(self.watchers),
        )
        while not self.stop_event.is_set():
            if self.monotonic() - started >= runtime_seconds:
                break
            cycle_started = self.monotonic()
            self.run_cycle()
            if self.monotonic() - started >= runtime_seconds:
                break

            next_tick += self.interval
            now_mono = self.monotonic()
            if next_tick <= now_mono:
                cycle_duration = now_mono - cycle_started
                logger.warning(
                    "감시 1회가 %.1f초 걸려 %d초 주기를 넘었습니다. 밀린 횟수는 몰아 실행하지 않습니다.",
                    cycle_duration,
                    self.interval,
                )
                # Run one fresh cycle immediately, but discard every missed historical tick.
                # This keeps outage recovery prompt without a burst of catch-up requests.
                next_tick = now_mono
            wait = max(0.0, next_tick - self.monotonic())
            remaining_runtime = runtime_seconds - (self.monotonic() - started)
            if remaining_runtime <= 0:
                break
            sleep_for = min(wait, remaining_runtime)
            if sleep_for > 0:
                self.stop_event.wait(sleep_for)
        logger.info(
            "v4 감시 정상 종료: 검사 %d회, 알림 %d건",
            self.session_cycles,
            self.session_alerts,
        )
