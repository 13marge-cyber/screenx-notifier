#!/usr/bin/env python3
"""CGV + Megabox movie booking notifier v5 entrypoint."""
from __future__ import annotations

import argparse
import logging
import sys
import tempfile
import time
from copy import deepcopy
from datetime import datetime
from typing import Any

from src.config import ConfigError, enabled_watchers, load_config
from src.formatter import build_selftest_rich
from src.monitor import Monitor
from src.providers import ProviderRouter
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


def make_clients(config: dict[str, Any]) -> tuple[StateManager, ProviderRouter, TelegramClient]:
    telegram = TelegramClient(config["telegram"]["bot_token"], config["telegram"]["chat_id"])
    state = StateManager(
        config["monitor"]["state_dir"],
        config["monitor"].get("legacy_state_dirs", []),
    )
    try:
        state.load(enabled_watchers(config))
    except StateError:
        telegram.send_plain(
            "⛔ 영화 예매 알리미 v5 상태 파일 오류\nActions 로그와 state/backup 파일을 확인해 주세요."
        )
        telegram.close()
        raise
    return state, ProviderRouter(config), telegram


def cmd_validate(config_path: str) -> int:
    config = load_config(config_path, require_secrets=False)
    providers = {w["provider"] for w in enabled_watchers(config)}
    print(
        f"v5 설정 검증 완료: watcher {len(enabled_watchers(config))}개 / "
        f"provider {', '.join(sorted(providers))}"
    )
    return 0


def cmd_check(config_path: str) -> int:
    config = load_config(config_path, require_secrets=False)
    providers = ProviderRouter(config)
    try:
        providers.start_cycle()
        failures = 0
        for watcher in enabled_watchers(config):
            try:
                result = providers.scan_watcher(watcher)
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
            if result.get("provider_error"):
                print(f"  PROVIDER ERROR: {result['provider_error']}", file=sys.stderr)
                failures += 1
            if result["failed_dates"]:
                for raw in result["failed_dates"]:
                    print(f"  ERROR {raw}: {result['errors'].get(raw, '조회 실패')}", file=sys.stderr)
                failures += 1
        return 1 if failures else 0
    finally:
        providers.close()


def _preflight_cgv(watcher: dict[str, Any], providers: Any, today, seen: set[tuple[str, str]]) -> int:
    warnings = 0
    client = providers.cgv
    key = (watcher["theater_code"], watcher.get("co_cd", "A420"))
    if key not in seen:
        seen.add(key)
        result = client.probe_theater(*key)
        if result.error_kind == "schema":
            raise RuntimeError(f"CGV 날짜 API schema guard 실패({key[0]}): {result.error}")
        if result.error:
            warnings += 1
            logging.getLogger(__name__).warning("CGV 극장 %s 날짜 API 사전점검 경고: %s", key[0], result.error)
        else:
            print(f"CGV 극장 {key[0]}: 날짜 API 정상")
    active = client.active_dates(watcher, today=today)
    if active:
        result = client.fetch_date(key[0], key[1], active[0])
        if result.error_kind == "schema":
            raise RuntimeError(f"CGV 상영 API schema guard 실패({key[0]} {active[0]}): {result.error}")
        if result.error:
            warnings += 1
            logging.getLogger(__name__).warning(
                "CGV 극장 %s 상영 API 사전점검 일시장애(%s): %s", key[0], active[0], result.error
            )
        else:
            print(f"CGV 극장 {key[0]}: 상영 API 정상 ({active[0]})")
    return warnings


def _preflight_megabox(watcher: dict[str, Any], providers: Any, today, seen: set[tuple[str, str]]) -> int:
    warnings = 0
    client = providers.megabox
    key = (watcher["theater_code"], watcher["area_code"])
    if key not in seen:
        seen.add(key)
        probe = client.probe_recent_schema(watcher, today=today)
        if probe.error_kind in {"schema", "probe-empty"}:
            raise RuntimeError(f"메가박스 live canary 실패({key[0]}): {probe.error}")
        if probe.error:
            warnings += 1
            logging.getLogger(__name__).warning(
                "메가박스 극장 %s 현재 상영표 probe 경고: %s", key[0], probe.error
            )
        else:
            print(f"메가박스 극장 {key[0]}: 실상영표 schema/hall 필드 정상 ({len(probe.rows)}행)")
    active = client.active_dates(watcher, today=today)
    if active:
        result = client.fetch_date(key[0], key[1], active[0])
        if result.error_kind == "schema":
            raise RuntimeError(f"메가박스 상영 API schema guard 실패({key[0]} {active[0]}): {result.error}")
        if result.error:
            warnings += 1
            logging.getLogger(__name__).warning(
                "메가박스 극장 %s 상영 API 사전점검 일시장애(%s): %s", key[0], active[0], result.error
            )
        else:
            print(f"메가박스 극장 {key[0]}: 대상 날짜 API 정상 ({active[0]}, raw {len(result.rows)}행)")
    return warnings


def cmd_preflight(config_path: str) -> int:
    config = load_config(config_path, require_secrets=True)
    state, providers, telegram = make_clients(config)
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
        seen_cgv: set[tuple[str, str]] = set()
        seen_mb: set[tuple[str, str]] = set()
        providers.start_cycle()
        try:
            for watcher in enabled_watchers(config):
                if watcher["provider"] == "cgv":
                    warnings += _preflight_cgv(watcher, providers, today, seen_cgv)
                elif watcher["provider"] == "megabox":
                    warnings += _preflight_megabox(watcher, providers, today, seen_mb)
        except RuntimeError as exc:
            telegram.send_plain(
                "⛔ 영화 예매 알리미 v5 사전 점검 실패\n"
                f"{exc}\n"
                "GitHub Actions 로그를 확인해 주세요."
            )
            raise

        print(f"State: {state.path} / backup={state.backup_path} 준비 정상")
        print(f"KST: {format_kst()}")
        print(f"사전 점검 완료: 치명적 오류 0 / 일시 경고 {warnings}")
        return 0
    finally:
        providers.close()
        telegram.close()


def cmd_monitor(config_path: str, runtime_minutes: float) -> int:
    config = load_config(config_path, require_secrets=True)
    state, providers, telegram = make_clients(config)
    try:
        Monitor(config, state, providers, telegram).run_forever(runtime_minutes)
        return 0
    finally:
        providers.close()
        telegram.close()


def cmd_once(config_path: str) -> int:
    config = load_config(config_path, require_secrets=True)
    state, providers, telegram = make_clients(config)
    try:
        info = Monitor(config, state, providers, telegram).run_cycle()
        print(f"1회 검사 완료: 신규 회차 {info['new_showtimes']}개, 알림 {info['alerts']}건")
        return 0
    finally:
        providers.close()
        telegram.close()


class _FakeOpenProvider:
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
            "schedules": [item], "failed_dates": [], "errors": {}, "error_kinds": {},
            "active_dates": [date_raw],
        }


def _probe_all_selftest_watchers(config: dict[str, Any], providers: Any, today) -> dict[str, int]:
    """Strict live gate for every configured provider and every active target date."""
    watchers = enabled_watchers(config)
    counts: dict[str, int] = {}
    providers.start_cycle()
    seen_cgv: set[tuple[str, str]] = set()
    seen_mb: set[tuple[str, str]] = set()

    for watcher in watchers:
        if watcher["provider"] == "cgv":
            key = (watcher["theater_code"], watcher.get("co_cd", "A420"))
            if key not in seen_cgv:
                seen_cgv.add(key)
                probe = providers.cgv.probe_theater(*key)
                if probe.error:
                    raise RuntimeError(
                        f"CGV 날짜 API 자체진단 실패({watcher['theater_name']} {key[0]}): {probe.error}"
                    )
                print(f"CGV 자체진단: {watcher['theater_name']}({key[0]}) 날짜 API 정상")
        elif watcher["provider"] == "megabox":
            key = (watcher["theater_code"], watcher["area_code"])
            if key not in seen_mb:
                seen_mb.add(key)
                probe = providers.megabox.probe_recent_schema(watcher, today=today)
                if probe.error:
                    raise RuntimeError(
                        f"메가박스 실상영표 schema 자체진단 실패({watcher['theater_name']} {key[0]}): {probe.error}"
                    )
                print(
                    f"메가박스 자체진단: {watcher['theater_name']}({key[0]}) "
                    f"실상영표 schema/hall 필드 정상 ({len(probe.rows)}행)"
                )

    for watcher in watchers:
        result = providers.scan_watcher(watcher, today=today)
        if result.get("provider_error"):
            raise RuntimeError(
                f"{watcher['provider']} provider canary 자체진단 실패({watcher['theater_name']}): "
                f"{result['provider_error']}"
            )
        if result["failed_dates"]:
            first_failed = result["failed_dates"][0]
            detail = result["errors"].get(first_failed, "조회 실패")
            raise RuntimeError(
                f"{watcher['provider']} 상영 API 자체진단 실패({watcher['theater_name']} {first_failed}): {detail}"
            )
        count = len(result["schedules"])
        counts[watcher["id"]] = count
        print(
            f"{watcher['provider']} 자체진단: {watcher['theater_name']}({watcher['theater_code']}) "
            f"지정 날짜 전체 정상 / 현재 조건 매칭 {count}개"
        )
    return counts


def cmd_selftest(config_path: str) -> int:
    config = load_config(config_path, require_secrets=True)
    telegram = TelegramClient(config["telegram"]["bot_token"], config["telegram"]["chat_id"])
    providers = ProviderRouter(config)
    try:
        status, message = telegram.connection_status()
        if status != "ok":
            raise RuntimeError(f"Telegram strict selftest 실패: {message}")

        watchers = enabled_watchers(config)
        first = watchers[0]
        fixed_now = now_kst()
        match_counts = _probe_all_selftest_watchers(config, providers, fixed_now.date())

        rich_ok = telegram.send_alert(
            build_selftest_rich(watchers, fixed_now, match_counts=match_counts),
            f"🧪 영화 예매 알리미 v5 자체진단\nRich fallback 메시지\n⏰ 한국시간: {format_kst()}",
        )
        if not rich_ok or telegram.last_fallback_used:
            raise RuntimeError("Rich Message 자체진단이 실제 Rich로 성공하지 못했습니다.")

        time.sleep(1.1)
        fallback_ok = telegram.selftest_plain_fallback(
            f"🧪 v5 Plain fallback 자체진단 성공\n⏰ 한국시간: {format_kst()}"
        )
        if not fallback_ok or not telegram.last_fallback_used:
            raise RuntimeError("의도한 Plain fallback 경로가 실행되지 않았습니다.")

        time.sleep(1.1)
        fake_config = deepcopy(config)
        fake_config["monitor"]["heartbeat"]["enabled"] = False
        fake_watcher = deepcopy(first)
        fake_watcher["id"] = "selftest_fake_open"
        fake_watcher["name"] = "v5 자체진단 가짜 오픈"
        fake_watcher["alert_title"] = "🧪 자체진단 · 실제 예매 아님"
        fake_watcher["dates"] = [fixed_now.date().isoformat()]
        fake_config["watchers"] = [fake_watcher]
        schedule = {
            "provider": "selftest",
            "schedule_id": "SELFTEST-001",
            "date": fixed_now.strftime("%Y-%m-%d") + " (자체진단)",
            "movie": "SELFTEST - 실제 예매 아님",
            "hall": "TEST PREMIUM",
            "grade": "TEST",
            "start": "23:59",
            "end": "?",
        }
        with tempfile.TemporaryDirectory(prefix="movie-notifier-v5-selftest-") as tmp:
            state = StateManager(tmp)
            state.load([fake_watcher])
            fake_provider = _FakeOpenProvider(schedule)
            monitor = Monitor(fake_config, state, fake_provider, telegram, now_fn=lambda: fixed_now)
            first_result = monitor.run_cycle(at=fixed_now)
            second_result = monitor.run_cycle(at=fixed_now)
            if first_result["alerts"] != 1 or second_result["alerts"] != 0:
                raise RuntimeError("가짜 오픈 E2E의 신규/중복 억제 검증이 실패했습니다.")
            if not state.watcher(fake_watcher["id"])["known_keys"]:
                raise RuntimeError("가짜 오픈 E2E에서 known_keys가 저장되지 않았습니다.")

        print(
            "자체진단 완료: Telegram private/Rich/Plain fallback, CGV+Megabox live API/schema, "
            "atomic state, 가짜 오픈 E2E/중복억제 정상"
        )
        return 0
    finally:
        providers.close()
        telegram.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="영화 예매 알리미 v5 (CGV IMAX + Megabox Dolby Cinema)")
    parser.add_argument("--config", default="config.loop.yaml", help="YAML 설정 경로")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="Secret 없이 설정 구조만 검증")
    sub.add_parser("check", help="CGV+Megabox 1회 조회, 알림/상태 변경 없음")
    sub.add_parser("preflight", help="영구 오류 차단 + 외부 일시장애 경고")
    monitor = sub.add_parser("monitor", help="지정 시간 동안 1분 감시")
    monitor.add_argument("--runtime-minutes", type=float, default=315.0)
    sub.add_parser("once", help="실제 알림 포함 1회 감시")
    sub.add_parser("selftest", help="실제 Telegram/CGV/Megabox + 가짜 오픈 자체진단")
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
