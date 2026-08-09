"""Strict YAML/environment configuration validation for screenx-notifier v4."""
from __future__ import annotations

import os
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


class ConfigError(ValueError):
    pass


_TOP_KEYS = {"telegram", "monitor", "cgv", "watchers", "log_level"}
_MONITOR_KEYS = {
    "interval_seconds",
    "failure_alert_threshold",
    "state_dir",
    "legacy_state_dirs",
    "heartbeat",
}
_HEARTBEAT_KEYS = {"enabled", "time"}
_CGV_KEYS = {"timeout_seconds", "request_delay_seconds", "circuit_breaker_failures"}
_WATCHER_KEYS = {
    "id",
    "name",
    "enabled",
    "theater_code",
    "theater_name",
    "alert_title",
    "hall_keywords",
    "movie_keywords",
    "dates",
    "co_cd",
}
_TELEGRAM_KEYS: set[str] = set()


def _reject_unknown(mapping: dict[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        raise ConfigError(f"{label}에 지원하지 않는 설정 키가 있습니다: {', '.join(unknown)}")


def _require_nonempty_str(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"{label}은 비어 있지 않은 문자열이어야 합니다.")
    text = value.strip()
    if not text:
        raise ConfigError(f"{label} 값이 비어 있습니다.")
    return text


def _as_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ConfigError(f"{label}은 정수여야 합니다: {value}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{label}은 정수여야 합니다: {value}") from exc


def _as_float(value: Any, label: str) -> float:
    if isinstance(value, bool):
        raise ConfigError(f"{label}은 숫자여야 합니다: {value}")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"{label}은 숫자여야 합니다: {value}") from exc


def _as_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ConfigError(f"{label}은 true 또는 false여야 합니다.")
    return value


def _validate_time(value: Any, label: str) -> str:
    text = _require_nonempty_str(value, label)
    try:
        datetime.strptime(text, "%H:%M")
    except ValueError as exc:
        raise ConfigError(f"{label}은 HH:MM 형식이어야 합니다: {text}") from exc
    return text


def _validate_dates(values: Any, label: str) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ConfigError(f"{label}은 날짜가 1개 이상인 리스트여야 합니다.")
    result: list[str] = []
    for raw in values:
        if not isinstance(raw, str):
            raise ConfigError(f"{label}의 모든 날짜는 문자열 YYYY-MM-DD여야 합니다.")
        text = raw.strip()
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d")
        except ValueError as exc:
            raise ConfigError(f"잘못된 날짜 형식: {text} (YYYY-MM-DD 필요)") from exc
        result.append(parsed.strftime("%Y-%m-%d"))
    if len(set(result)) != len(result):
        raise ConfigError(f"{label}에 중복 날짜가 있습니다.")
    return result


def _validate_keywords(values: Any, label: str) -> list[str]:
    if not isinstance(values, list) or not values:
        raise ConfigError(f"{label}은 1개 이상의 문자열 리스트여야 합니다.")
    cleaned: list[str] = []
    for value in values:
        cleaned.append(_require_nonempty_str(value, label))
    return cleaned


def _validate_path_list(values: Any, label: str) -> list[str]:
    if not isinstance(values, list):
        raise ConfigError(f"{label}은 경로 문자열 리스트여야 합니다.")
    result: list[str] = []
    for value in values:
        result.append(_require_nonempty_str(value, label))
    if len(set(result)) != len(result):
        raise ConfigError(f"{label}에 중복 경로가 있습니다.")
    return result


def _parse_single_chat_id(env: dict[str, str]) -> str:
    raw = str(env.get("TELEGRAM_CHAT_IDS", "")).strip()
    if not raw:
        raise ConfigError("TELEGRAM_CHAT_IDS Secret이 없습니다.")
    ids = [item for item in re.split(r"[,;\s]+", raw) if item]
    if len(ids) != 1:
        raise ConfigError(f"v4는 Telegram 개인 수신자 1개만 허용합니다. 현재 {len(ids)}개입니다.")
    chat_id = ids[0]
    if not chat_id.isdigit() or int(chat_id) <= 0:
        raise ConfigError("TELEGRAM_CHAT_IDS에는 개인 채팅의 양수 숫자 chat id 1개만 넣어야 합니다.")
    return chat_id


def validate_config(
    config: dict[str, Any],
    require_secrets: bool = True,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    if not isinstance(config, dict):
        raise ConfigError("설정 최상위는 YAML 객체여야 합니다.")
    _reject_unknown(config, _TOP_KEYS, "최상위 설정")
    cfg = deepcopy(config)

    log_level = str(cfg.get("log_level", "INFO")).strip().upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ConfigError(f"지원하지 않는 log_level입니다: {log_level}")
    cfg["log_level"] = log_level

    monitor = cfg.setdefault("monitor", {})
    if not isinstance(monitor, dict):
        raise ConfigError("monitor 설정이 객체가 아닙니다.")
    _reject_unknown(monitor, _MONITOR_KEYS, "monitor")

    interval = _as_int(monitor.get("interval_seconds", 60), "monitor.interval_seconds")
    if not 30 <= interval <= 3600:
        raise ConfigError("monitor.interval_seconds는 30~3600초여야 합니다.")
    monitor["interval_seconds"] = interval

    threshold = _as_int(
        monitor.get("failure_alert_threshold", 3),
        "monitor.failure_alert_threshold",
    )
    if not 1 <= threshold <= 20:
        raise ConfigError("monitor.failure_alert_threshold는 1~20이어야 합니다.")
    monitor["failure_alert_threshold"] = threshold

    monitor["state_dir"] = _require_nonempty_str(
        monitor.get("state_dir", "./data/notifier-state"),
        "monitor.state_dir",
    )
    monitor["legacy_state_dirs"] = _validate_path_list(
        monitor.get("legacy_state_dirs", ["./data/v3-state", "./data/actions-state"]),
        "monitor.legacy_state_dirs",
    )

    heartbeat = monitor.setdefault("heartbeat", {})
    if not isinstance(heartbeat, dict):
        raise ConfigError("monitor.heartbeat 설정이 객체가 아닙니다.")
    _reject_unknown(heartbeat, _HEARTBEAT_KEYS, "monitor.heartbeat")
    heartbeat["enabled"] = _as_bool(heartbeat.get("enabled", True), "monitor.heartbeat.enabled")
    heartbeat["time"] = _validate_time(heartbeat.get("time", "09:00"), "monitor.heartbeat.time")

    cgv = cfg.setdefault("cgv", {})
    if not isinstance(cgv, dict):
        raise ConfigError("cgv 설정이 객체가 아닙니다.")
    _reject_unknown(cgv, _CGV_KEYS, "cgv")
    timeout = _as_float(cgv.get("timeout_seconds", 6.0), "cgv.timeout_seconds")
    if not 2.0 <= timeout <= 20.0:
        raise ConfigError("cgv.timeout_seconds는 2~20초여야 합니다.")
    cgv["timeout_seconds"] = timeout
    delay = _as_float(cgv.get("request_delay_seconds", 0.2), "cgv.request_delay_seconds")
    if not 0.0 <= delay <= 5.0:
        raise ConfigError("cgv.request_delay_seconds는 0~5초여야 합니다.")
    cgv["request_delay_seconds"] = delay
    breaker = _as_int(cgv.get("circuit_breaker_failures", 3), "cgv.circuit_breaker_failures")
    if not 1 <= breaker <= 10:
        raise ConfigError("cgv.circuit_breaker_failures는 1~10이어야 합니다.")
    cgv["circuit_breaker_failures"] = breaker

    watchers = cfg.get("watchers")
    if not isinstance(watchers, list) or not watchers:
        raise ConfigError("watchers가 비어 있습니다.")
    ids: set[str] = set()
    names: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(watchers, start=1):
        if not isinstance(raw, dict):
            raise ConfigError(f"watchers[{index}]가 객체가 아닙니다.")
        _reject_unknown(raw, _WATCHER_KEYS, f"watchers[{index}]")
        watcher = deepcopy(raw)
        watcher_id = _require_nonempty_str(watcher.get("id"), f"watchers[{index}].id")
        if re.fullmatch(r"[A-Za-z0-9_-]+", watcher_id) is None:
            raise ConfigError(f"watcher id는 영문/숫자/-/_만 사용하세요: {watcher_id}")
        if watcher_id in ids:
            raise ConfigError(f"watcher id 중복: {watcher_id}")
        ids.add(watcher_id)
        watcher["id"] = watcher_id
        watcher["name"] = _require_nonempty_str(watcher.get("name"), f"{watcher_id}.name")
        if watcher["name"] in names:
            raise ConfigError(f"watcher name 중복: {watcher['name']}")
        names.add(watcher["name"])
        watcher["enabled"] = _as_bool(watcher.get("enabled", True), f"{watcher_id}.enabled")
        watcher["theater_code"] = _require_nonempty_str(
            watcher.get("theater_code"), f"{watcher_id}.theater_code"
        )
        if not watcher["theater_code"].isdigit() or len(watcher["theater_code"]) != 4:
            raise ConfigError(f"{watcher_id}.theater_code는 4자리 숫자여야 합니다.")
        watcher["theater_name"] = _require_nonempty_str(
            watcher.get("theater_name"), f"{watcher_id}.theater_name"
        )
        watcher["alert_title"] = _require_nonempty_str(
            watcher.get("alert_title"), f"{watcher_id}.alert_title"
        )
        watcher["hall_keywords"] = _validate_keywords(
            watcher.get("hall_keywords"), f"{watcher_id}.hall_keywords"
        )
        watcher["movie_keywords"] = _validate_keywords(
            watcher.get("movie_keywords"), f"{watcher_id}.movie_keywords"
        )
        watcher["dates"] = _validate_dates(watcher.get("dates"), f"{watcher_id}.dates")
        watcher["co_cd"] = _require_nonempty_str(watcher.get("co_cd", "A420"), f"{watcher_id}.co_cd")
        normalized.append(watcher)
    cfg["watchers"] = normalized
    if not any(watcher["enabled"] for watcher in normalized):
        raise ConfigError("enabled=true인 watcher가 1개 이상 필요합니다.")

    telegram = cfg.setdefault("telegram", {})
    if not isinstance(telegram, dict):
        raise ConfigError("telegram 설정이 객체가 아닙니다.")
    _reject_unknown(telegram, _TELEGRAM_KEYS, "telegram")
    if require_secrets:
        env_map = dict(os.environ if env is None else env)
        token = str(env_map.get("TELEGRAM_BOT_TOKEN", "")).strip()
        if not token:
            raise ConfigError("TELEGRAM_BOT_TOKEN Secret이 없습니다.")
        telegram["bot_token"] = token
        telegram["chat_id"] = _parse_single_chat_id(env_map)
    return cfg


def load_config(
    path: str | Path,
    require_secrets: bool = True,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"설정 파일을 찾을 수 없습니다: {config_path}")
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"YAML 읽기/파싱 실패: {type(exc).__name__}") from exc
    return validate_config(raw, require_secrets=require_secrets, env=env)


def enabled_watchers(config: dict[str, Any]) -> list[dict[str, Any]]:
    return [watcher for watcher in config.get("watchers", []) if watcher.get("enabled", True)]
