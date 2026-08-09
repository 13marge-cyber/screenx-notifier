from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import main as app
from src.config import ConfigError
from src.state import StateError

KST = ZoneInfo("Asia/Seoul")


class DummyState:
    path = Path("state.json")
    backup_path = Path("state.backup.json")

    def __init__(self):
        self.probed = False

    def probe_writable(self):
        self.probed = True


class DummyTelegram:
    def __init__(self, status=("ok", "ok")):
        self.status = status
        self.closed = False

    def connection_status(self):
        return self.status

    def close(self):
        self.closed = True


class DummyFetch:
    def __init__(self, error=None, error_kind=None):
        self.error = error
        self.error_kind = error_kind


class DummyCGV:
    def __init__(self, date_error=None, show_error=None, show_kind=None):
        self.date_error = date_error
        self.show_error = show_error
        self.show_kind = show_kind
        self.closed = False

    def start_cycle(self):
        pass

    def probe_theater(self, *_args):
        return DummyFetch(self.date_error)

    def active_dates(self, _watcher, today=None):
        return ["20260821"]

    def fetch_date(self, *_args):
        return DummyFetch(self.show_error, self.show_kind)

    def close(self):
        self.closed = True


def cfg():
    return {
        "telegram": {"bot_token": "x", "chat_id": "1"},
        "monitor": {
            "state_dir": "state",
            "legacy_state_dirs": [],
            "interval_seconds": 60,
            "failure_alert_threshold": 3,
            "heartbeat": {"enabled": False, "time": "09:00"},
        },
        "cgv": {"timeout_seconds": 6.0, "request_delay_seconds": 0.0, "circuit_breaker_failures": 3},
        "watchers": [{
            "id": "w", "name": "W", "enabled": True,
            "theater_code": "0013", "theater_name": "용산", "alert_title": "용산",
            "hall_keywords": ["IMAX"], "movie_keywords": ["오디세이"],
            "dates": ["2026-08-21"], "co_cd": "A420",
        }],
        "log_level": "INFO",
    }


def patch_preflight(monkeypatch, telegram_status=("ok", "ok"), date_error=None, show_error=None, show_kind=None):
    config = cfg()
    state = DummyState()
    cgv = DummyCGV(date_error, show_error, show_kind)
    telegram = DummyTelegram(telegram_status)
    monkeypatch.setattr(app, "load_config", lambda *_a, **_kw: config)
    monkeypatch.setattr(app, "make_clients", lambda _cfg: (state, cgv, telegram))
    monkeypatch.setattr(app, "now_kst", lambda: datetime(2026, 8, 9, 12, 0, tzinfo=KST))
    return state, cgv, telegram


def test_preflight_transient_telegram_and_cgv_are_warnings_not_fatal(monkeypatch):
    state, cgv, telegram = patch_preflight(
        monkeypatch,
        telegram_status=("transient", "timeout"),
        date_error="CGV timeout",
        show_error="CGV 503",
        show_kind="http",
    )
    assert app.cmd_preflight("x.yml") == 0
    assert state.probed
    assert cgv.closed and telegram.closed


def test_preflight_permanent_telegram_is_fatal(monkeypatch):
    _state, cgv, telegram = patch_preflight(
        monkeypatch, telegram_status=("permanent", "getChat 403")
    )
    with pytest.raises(RuntimeError, match="403"):
        app.cmd_preflight("x.yml")
    assert cgv.closed and telegram.closed


def test_preflight_schema_drift_is_fatal(monkeypatch):
    _state, cgv, telegram = patch_preflight(
        monkeypatch, show_error="SCHEMA_DRIFT", show_kind="schema"
    )
    with pytest.raises(RuntimeError, match="schema guard"):
        app.cmd_preflight("x.yml")
    assert cgv.closed and telegram.closed


def test_main_maps_config_state_and_unknown_errors_to_distinct_exit_codes(monkeypatch):
    monkeypatch.setattr(app, "build_parser", lambda: type("P", (), {"parse_args": lambda self: type("A", (), {"config": "x", "command": "validate"})()})())

    monkeypatch.setattr(app, "load_config", lambda *_a, **_kw: (_ for _ in ()).throw(ConfigError("bad")))
    assert app.main() == 2

    monkeypatch.setattr(app, "load_config", lambda *_a, **_kw: (_ for _ in ()).throw(StateError("bad")))
    assert app.main() == 3

    monkeypatch.setattr(app, "load_config", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("bad")))
    assert app.main() == 4


def test_parser_rejects_unknown_command_before_execution():
    parser = app.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["nonsense"])
