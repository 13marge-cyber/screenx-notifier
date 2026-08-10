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
    def __init__(self): self.probed = False
    def probe_writable(self): self.probed = True


class DummyTelegram:
    def __init__(self, status=("ok", "ok")):
        self.status = status; self.closed = False; self.sent = []
    def connection_status(self): return self.status
    def send_plain(self, text): self.sent.append(text); return True
    def close(self): self.closed = True


class DummyFetch:
    def __init__(self, error=None, error_kind=None, rows=None):
        self.error = error; self.error_kind = error_kind; self.rows = list(rows or [])


class DummyCGV:
    def __init__(self, date_error=None, show_error=None, show_kind=None):
        self.date_error = date_error; self.show_error = show_error; self.show_kind = show_kind
        self.probed = []; self.scanned = []
    def start_cycle(self): pass
    def probe_theater(self, code, co_cd="A420"):
        self.probed.append((code, co_cd)); return DummyFetch(self.date_error)
    def active_dates(self, _watcher, today=None): return ["20260821"]
    def fetch_date(self, *_args): return DummyFetch(self.show_error, self.show_kind)
    def scan_watcher(self, watcher, today=None):
        self.scanned.append(watcher["theater_code"])
        if self.show_error:
            raw = watcher["dates"][0].replace("-", "")
            return {"schedules": [], "failed_dates": [raw], "errors": {raw: self.show_error},
                    "error_kinds": {raw: self.show_kind or "http"}, "active_dates": [raw]}
        return {"schedules": [], "failed_dates": [], "errors": {}, "error_kinds": {},
                "active_dates": [d.replace("-", "") for d in watcher["dates"]]}


class DummyMegabox:
    def __init__(self, probe_error=None, scan_error=None, probe_kind=None):
        self.probe_error = probe_error; self.scan_error = scan_error; self.probe_kind = probe_kind
        self.probed = []; self.scanned = []
    def start_cycle(self): pass
    def probe_recent_schema(self, watcher, today=None):
        self.probed.append(watcher["theater_code"])
        kind = self.probe_kind if self.probe_error else None
        if self.probe_error and kind is None:
            kind = "schema"
        return DummyFetch(self.probe_error, kind, rows=[{"ok": 1}] if not self.probe_error else [])
    def active_dates(self, watcher, today=None): return [d.replace("-", "") for d in watcher["dates"]]
    def fetch_date(self, *_args): return DummyFetch()
    def scan_watcher(self, watcher, today=None):
        self.scanned.append(watcher["theater_code"])
        if self.scan_error:
            raw = watcher["dates"][0].replace("-", "")
            return {"schedules": [], "failed_dates": [raw], "errors": {raw: self.scan_error},
                    "error_kinds": {raw: "http"}, "active_dates": [raw]}
        return {"schedules": [], "failed_dates": [], "errors": {}, "error_kinds": {},
                "active_dates": [d.replace("-", "") for d in watcher["dates"]]}


class DummyProviders:
    def __init__(self, cgv=None, megabox=None):
        self.cgv = cgv or DummyCGV(); self.megabox = megabox or DummyMegabox(); self.closed = False
    def start_cycle(self): self.cgv.start_cycle(); self.megabox.start_cycle()
    def scan_watcher(self, watcher, today=None):
        return (self.cgv if watcher.get("provider", "cgv") == "cgv" else self.megabox).scan_watcher(watcher, today=today)
    def close(self): self.closed = True


def cfg():
    return {
        "telegram": {"bot_token": "x", "chat_id": "1"},
        "monitor": {"state_dir": "state", "legacy_state_dirs": [], "interval_seconds": 60,
                    "failure_alert_threshold": 3, "heartbeat": {"enabled": False, "time": "09:00"}},
        "cgv": {"timeout_seconds": 6.0, "request_delay_seconds": 0.0, "circuit_breaker_failures": 3},
        "megabox": {"timeout_seconds": 8.0, "request_delay_seconds": 0.0, "circuit_breaker_failures": 3, "schema_probe_days": 4},
        "watchers": [{"id": "w", "name": "W", "enabled": True, "provider": "cgv",
                      "theater_code": "0013", "theater_name": "용산", "alert_title": "용산",
                      "hall_keywords": ["IMAX"], "movie_keywords": ["오디세이"],
                      "dates": ["2026-08-21"], "co_cd": "A420"}],
        "log_level": "INFO",
    }


def patch_preflight(monkeypatch, telegram_status=("ok", "ok"), date_error=None, show_error=None, show_kind=None):
    config = cfg(); state = DummyState(); cgv = DummyCGV(date_error, show_error, show_kind)
    providers = DummyProviders(cgv=cgv); telegram = DummyTelegram(telegram_status)
    monkeypatch.setattr(app, "load_config", lambda *_a, **_kw: config)
    monkeypatch.setattr(app, "make_clients", lambda _cfg: (state, providers, telegram))
    monkeypatch.setattr(app, "now_kst", lambda: datetime(2026, 8, 9, 12, 0, tzinfo=KST))
    return state, providers, telegram


def test_preflight_transient_telegram_and_cgv_are_warnings_not_fatal(monkeypatch):
    state, providers, telegram = patch_preflight(monkeypatch, telegram_status=("transient", "timeout"),
                                                  date_error="CGV timeout", show_error="CGV 503", show_kind="http")
    assert app.cmd_preflight("x.yml") == 0
    assert state.probed and providers.closed and telegram.closed


def test_preflight_permanent_telegram_is_fatal(monkeypatch):
    _state, providers, telegram = patch_preflight(monkeypatch, telegram_status=("permanent", "getChat 403"))
    with pytest.raises(RuntimeError, match="403"): app.cmd_preflight("x.yml")
    assert providers.closed and telegram.closed


def test_preflight_schema_drift_is_fatal(monkeypatch):
    _state, providers, telegram = patch_preflight(monkeypatch, show_error="SCHEMA_DRIFT", show_kind="schema")
    with pytest.raises(RuntimeError, match="schema guard"): app.cmd_preflight("x.yml")
    assert providers.closed and telegram.closed


def test_main_maps_config_state_and_unknown_errors_to_distinct_exit_codes(monkeypatch):
    monkeypatch.setattr(app, "build_parser", lambda: type("P", (), {"parse_args": lambda self: type("A", (), {"config": "x", "command": "validate"})()})())
    monkeypatch.setattr(app, "load_config", lambda *_a, **_kw: (_ for _ in ()).throw(ConfigError("bad")))
    assert app.main() == 2
    monkeypatch.setattr(app, "load_config", lambda *_a, **_kw: (_ for _ in ()).throw(StateError("bad")))
    assert app.main() == 3
    monkeypatch.setattr(app, "load_config", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("bad")))
    assert app.main() == 4


def test_parser_rejects_unknown_command_before_execution():
    with pytest.raises(SystemExit): app.build_parser().parse_args(["nonsense"])


def mixed_cfg():
    config = cfg(); base = config["watchers"][0]; watchers = []
    for code, theater, title in [("0013", "용산아이파크몰", "용산 IMAX"), ("0199", "천호", "천호 IMAX"), ("0257", "광교", "광교 IMAX")]:
        w = dict(base); w.update({"id": f"w{code}", "name": title, "theater_code": code,
                                  "theater_name": theater, "alert_title": title,
                                  "dates": ["2026-08-29", "2026-08-30"]}); watchers.append(w)
    mb = {"id": "mb0052", "name": "수원AK Dolby", "enabled": True, "provider": "megabox",
          "theater_code": "0052", "area_code": "30", "theater_name": "수원AK플라자(수원역)",
          "alert_title": "수원AK Dolby", "hall_keywords": ["DOLBY CINEMA"],
          "movie_keywords": ["오디세이", "스파이더맨 브랜드 뉴 데이"],
          "dates": ["2026-08-15", "2026-08-16", "2026-08-17"]}
    watchers.append(mb); config["watchers"] = watchers; return config


def test_selftest_probe_covers_all_cgv_and_megabox_targets():
    config = mixed_cfg(); providers = DummyProviders()
    counts = app._probe_all_selftest_watchers(config, providers, datetime(2026, 8, 10, tzinfo=KST).date())
    assert providers.cgv.probed == [("0013", "A420"), ("0199", "A420"), ("0257", "A420")]
    assert providers.megabox.probed == ["0052"]
    assert providers.cgv.scanned == ["0013", "0199", "0257"]
    assert providers.megabox.scanned == ["0052"]
    assert counts == {"w0013": 0, "w0199": 0, "w0257": 0, "mb0052": 0}


def test_selftest_probe_fails_if_megabox_live_schema_probe_fails():
    config = mixed_cfg(); providers = DummyProviders(megabox=DummyMegabox(probe_error="SCHEMA_DRIFT"))
    with pytest.raises(RuntimeError, match="메가박스.*schema"):
        app._probe_all_selftest_watchers(config, providers, datetime(2026, 8, 10, tzinfo=KST).date())


def test_preflight_megabox_probe_empty_is_fatal_and_notifies_telegram(monkeypatch):
    config = mixed_cfg()
    state = DummyState()
    providers = DummyProviders(megabox=DummyMegabox(
        probe_error="MEGABOX_PROBE_EMPTY: 4일 모두 empty",
        probe_kind="probe-empty",
    ))
    telegram = DummyTelegram()
    monkeypatch.setattr(app, "load_config", lambda *_a, **_kw: config)
    monkeypatch.setattr(app, "make_clients", lambda _cfg: (state, providers, telegram))
    monkeypatch.setattr(app, "now_kst", lambda: datetime(2026, 8, 10, 12, 0, tzinfo=KST))

    with pytest.raises(RuntimeError, match="메가박스 live canary 실패"):
        app.cmd_preflight("x.yml")
    assert any("사전 점검 실패" in message and "MEGABOX_PROBE_EMPTY" in message for message in telegram.sent)
    assert providers.closed and telegram.closed
