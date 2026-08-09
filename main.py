#!/usr/bin/env python3
"""screenx-notifier v4 entrypoint."""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import time
from copy import deepcopy
from datetime import datetime
from typing import Any

from src.cgv import CGVClient
from src.config import ConfigError, enabled_watchers, load_config
from src.formatter import build_selftest_rich
from src.monitor import Monitor
from src.state import StateError, StateManager
from src.telegram import TelegramClient
from src.timeutil import KST, format_kst, now_kst


class KSTFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.fromtimestamp(record.created, KST)
        return dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


def setup_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(KSTFormatter("%(asctime)s KST [%(levelname)s] %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def make_clients(config: dict[str, Any]) -> tuple[StateManager, CGVClient, TelegramClient]:
    telegram = TelegramClient(config["telegram"]["bot_token"], config["telegram"]["chat_id"])
    state = StateManager(
        config["monitor"]["state_dir"],
        config["monitor"].get("legacy_state_dirs", []),
    )
    try:
        state.load(enabled_watchers(config))
    except StateError:
        # Best-effort emergency notice. State errors remain fatal even if this fails.
        telegram.send_plain("⛔ CGV 알리미 v4 상태 파일 오류\nActions 로그와 state/backup 파일을 확인해 주세요.")
        telegram.close()
        raise
    return state, CGVClient(config), telegram


def cmd_validate(config_path: str) -> int:
    config = load_config(config_path, require_secrets=False)
    print(f"v4 설정 검증 완료: watcher {len(enabled_watchers(config))}개")
    return 0


def cmd_check(config_path: str) -> int:
    config = load_config(config_path, require_secrets=False)
    cgv = CGVClient(config)
    try:
        cgv.start_cycle()
        failures = 0
        for watcher in enabled_watchers(config):
            try:
                result = cgv.scan_watcher(watcher)
            except Exception as exc:
                print(f"[{watcher['name']}] 내부 오류: {type(exc).__name__}", file=sys.stderr)
                failures += 1
                continue
            print(
                f"[{watcher['name']}] 매칭 {len(result['schedules'])}개 / "
                f"실패 날짜 {len(result['failed_dates'])}개"
            )
            for item in result["schedules"]:
                print(f"  {item['date']} {item['start']} {item['movie']} @ {item['hall']}")
            failures += int(bool(result["failed_dates"]))
        return 1 if failures else 0
    finally:
        cgv.close()


def cmd_preflight(config_path: str) -> int:
    config = load_config(config_path, require_secrets=True)
    state, cgv, telegram = make_clients(config)
    warnings = 0
    try:
        state.probe_writable()
        status, message = telegram.connection_status()
        if status == "permanent":
            raise RuntimeError(message)
        if status == "transient":
            warnings += 1
            logging.getLogger(__name__).warning("Telegram 사전점검 일시장애: %s", message)
        else:
            print(f"Telegram: {message}")

        today = now_kst().date()
        seen_theaters: set[tuple[str, str]] = set()
        seen_showtime_probes: set[tuple[str, str]] = set()
        cgv.start_cycle()
        for watcher in enabled_watchers(config):
            theater_key = (watcher["theater_code"], watcher.get("co_cd", "A420"))
            if theater_key not in seen_theaters:
                seen_theaters.add(theater_key)
                result = cgv.probe_theater(*theater_key)
                if result.error:
                    warnings += 1
                    logging.getLogger(__name__).warning(
                        "CGV 극장 %s 날짜 API 사전점검 경고: %s",
                        theater_key[0], result.error,
                    )
                else:
                    print(f"CGV 극장 {theater_key[0]}: 날짜 API 정상")

            if theater_key not in seen_showtime_probes:
                active_dates = cgv.active_dates(watcher, today=today)
                if active_dates:
                    seen_showtime_probes.add(theater_key)
                    probe_date = active_dates[0]
                    result = cgv.fetch_date(theater_key[0], theater_key[1], probe_date)
                    if result.error_kind == "schema":
                        raise RuntimeError(
                            f"CGV 상영 API schema guard 실패({theater_key[0]} {probe_date}): {result.error}"
                        )
                    if result.error:
                        warnings += 1
                        logging.getLogger(__name__).warning(
                            "CGV 극장 %s 상영 API 사전점검 일시장애(%s): %s",
                            theater_key[0], probe_date, result.error,
                        )
                    else:
                        print(f"CGV 극장 {theater_key[0]}: 상영 API 정상 ({probe_date})")

        print(f"State: {state.path} / backup={state.backup_path} 준비 정상")
        print(f"KST: {format_kst()}")
        print(f"사전 점검 완료: 치명적 오류 0 / 일시 경고 {warnings}")
        return 0
    finally:
        cgv.close()
        telegram.close()


def cmd_monitor(config_path: str, runtime_minutes: float) -> int:
    config = load_config(config_path, require_secrets=True)
    state, cgv, telegram = make_clients(config)
    try:
        Monitor(config, state, cgv, telegram).run_forever(runtime_minutes)
        return 0
    finally:
        cgv.close()
        telegram.close()


def cmd_once(config_path: str) -> int:
    config = load_config(config_path, require_secrets=True)
    state, cgv, telegram = make_clients(config)
    try:
        info = Monitor(config, state, cgv, telegram).run_cycle()
        print(f"1회 검사 완료: 신규 회차 {info['new_showtimes']}개, 알림 {info['alerts']}건")
        return 0
    finally:
        cgv.close()
        telegram.close()


class _FakeOpenCGV:
    def __init__(self, schedule: dict[str, Any]):
        self.schedule = schedule

    def start_cycle(self) -> None:
        return None

    @staticmethod
    def active_dates(watcher: dict[str, Any], today=None) -> list[str]:
        return [watcher["dates"][0].replace("-", "")]

    def scan_watcher(self, watcher: dict[str, Any], today=None) -> dict[str, Any]:
        date_raw = watcher["dates"][0].replace("-", "")
        item = dict(self.schedule)
        item["date_raw"] = date_raw
        return {
            "schedules": [item],
            "failed_dates": [],
            "errors": {},
            "error_kinds": {},
            "active_dates": [date_raw],
        }


def cmd_selftest(config_path: str) -> int:
    config = load_config(config_path, require_secrets=True)
    telegram = TelegramClient(config["telegram"]["bot_token"], config["telegram"]["chat_id"])
    cgv = CGVClient(config)
    try:
        status, message = telegram.connection_status()
        if status != "ok":
            raise RuntimeError(f"Telegram strict selftest 실패: {message}")

        first = enabled_watchers(config)[0]
        probe = cgv.probe_theater(first["theater_code"], first.get("co_cd", "A420"))
        if probe.error:
            raise RuntimeError(f"CGV 날짜 API 자체진단 실패: {probe.error}")
        active_dates = cgv.active_dates(first, today=now_kst().date())
        if active_dates:
            cgv.start_cycle()
            showtime_probe = cgv.fetch_date(first["theater_code"], first.get("co_cd", "A420"), active_dates[0])
            if showtime_probe.error:
                raise RuntimeError(f"CGV 상영 API 자체진단 실패: {showtime_probe.error}")

        rich_ok = telegram.send_alert(
            build_selftest_rich(now_kst()),
            f"🧪 CGV 알리미 v4 자체진단\nRich fallback 메시지\n⏰ 한국시간: {format_kst()}",
        )
        if not rich_ok or telegram.last_fallback_used:
            raise RuntimeError("Rich Message 자체진단이 실제 Rich로 성공하지 못했습니다.")

        time.sleep(1.1)
        fallback_ok = telegram.send_alert(
            {
                "html": "<b>v4 fallback probe</b>",
                "blocks": [{"type": "paragraph", "text": "v4 fallback probe"}],
            },
            f"🧪 v4 Plain fallback 자체진단 성공\n⏰ 한국시간: {format_kst()}",
        )
        if not fallback_ok or not telegram.last_fallback_used:
            raise RuntimeError("의도한 Plain fallback 경로가 실행되지 않았습니다.")

        # Full fake-open E2E with an isolated temporary state. It sends one clearly
        # labelled test alert, then proves the same fake schedule is suppressed.
        time.sleep(1.1)
        fixed_now = now_kst()
        fake_config = deepcopy(config)
        fake_config["monitor"]["heartbeat"]["enabled"] = False
        fake_watcher = deepcopy(first)
        fake_watcher["id"] = "selftest_fake_open"
        fake_watcher["name"] = "v4 자체진단 가짜 오픈"
        fake_watcher["alert_title"] = "🧪 자체진단 · 실제 예매 아님"
        fake_watcher["dates"] = [fixed_now.date().isoformat()]
        fake_config["watchers"] = [fake_watcher]
        schedule = {
            "date": fixed_now.strftime("%Y-%m-%d") + " (자체진단)",
            "movie": "SELFTEST - 실제 예매 아님",
            "hall": "TEST SCREENX",
            "grade": "TEST",
            "start": "23:59",
            "end": "?",
        }
        with tempfile.TemporaryDirectory(prefix="screenx-v4-selftest-") as tmp:
            state = StateManager(tmp)
            state.load([fake_watcher])
            fake_cgv = _FakeOpenCGV(schedule)
            monitor = Monitor(
                fake_config,
                state,
                fake_cgv,  # type: ignore[arg-type]
                telegram,
                now_fn=lambda: fixed_now,
            )
            first_result = monitor.run_cycle(at=fixed_now)
            second_result = monitor.run_cycle(at=fixed_now)
            if first_result["alerts"] != 1 or second_result["alerts"] != 0:
                raise RuntimeError("가짜 오픈 E2E의 신규/중복 억제 검증이 실패했습니다.")
            if not state.watcher(fake_watcher["id"])["known_keys"]:
                raise RuntimeError("가짜 오픈 E2E에서 known_keys가 저장되지 않았습니다.")

        print(
            "자체진단 완료: Telegram private/Rich/Plain fallback, CGV 날짜·상영 API, "
            "atomic state, 가짜 오픈 E2E/중복억제 정상"
        )
        return 0
    finally:
        cgv.close()
        telegram.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CGV ScreenX/IMAX notifier v4")
    parser.add_argument("--config", default="config.loop.yaml", help="YAML 설정 경로")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="Secret 없이 설정 구조만 검증")
    sub.add_parser("check", help="CGV 1회 조회, 알림/상태 변경 없음")
    sub.add_parser("preflight", help="영구 오류 차단 + 외부 일시장애 경고")
    monitor = sub.add_parser("monitor", help="지정 시간 동안 1분 감시")
    monitor.add_argument("--runtime-minutes", type=float, default=315.0)
    sub.add_parser("once", help="실제 알림 포함 1회 감시")
    sub.add_parser("selftest", help="실제 Telegram/CGV + 가짜 오픈 자체진단")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        config = load_config(args.config, require_secrets=args.command not in {"validate", "check"})
        setup_logging(config.get("log_level", "INFO"))
        if args.command == "validate":
            return cmd_validate(args.config)
        if args.command == "check":
            return cmd_check(args.config)
        if args.command == "preflight":
            return cmd_preflight(args.config)
        if args.command == "monitor":
            return cmd_monitor(args.config, args.runtime_minutes)
        if args.command == "once":
            return cmd_once(args.config)
        if args.command == "selftest":
            return cmd_selftest(args.config)
        return 2
    except ConfigError as exc:
        print(f"설정 오류: {exc}", file=sys.stderr)
        return 2
    except StateError as exc:
        logging.getLogger(__name__).critical("상태 오류: %s", exc)
        return 3
    except Exception as exc:
        logging.getLogger(__name__).critical("치명적 오류: %s: %s", type(exc).__name__, exc)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
