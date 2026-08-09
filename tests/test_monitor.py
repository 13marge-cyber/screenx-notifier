from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

from src.monitor import Monitor
from src.state import StateManager

KST = ZoneInfo("Asia/Seoul")


def schedule(start="07:00"):
    return {
        "date_raw": "20260821",
        "date": "2026-08-21 (금)",
        "movie": "오디세이",
        "hall": "IMAX관",
        "grade": "IMAX LASER 2D",
        "start": start,
        "end": "10:30",
    }


class FakeCGV:
    def __init__(self, results):
        self.results = list(results)
        self.cycles = 0

    def start_cycle(self):
        self.cycles += 1

    def scan_watcher(self, watcher, today=None):
        self.last_today = today
        if not self.results:
            raise AssertionError("no fake result")
        return deepcopy(self.results.pop(0))


class FakeTelegram:
    def __init__(self, alert_results=None, plain_results=None):
        self.alert_results = list(alert_results or [True])
        self.plain_results = list(plain_results or [True] * 20)
        self.alert_calls = []
        self.plain_calls = []

    def send_alert(self, rich, plain):
        self.alert_calls.append((rich, plain))
        return self.alert_results.pop(0) if self.alert_results else True

    def send_plain(self, text):
        self.plain_calls.append(text)
        return self.plain_results.pop(0) if self.plain_results else True


def good_result(items=None):
    return {"schedules": list(items or []), "failed_dates": [], "errors": {}, "active_dates": ["20260821"]}


def fail_result():
    return {
        "schedules": [],
        "failed_dates": ["20260821"],
        "errors": {"20260821": "HTTP 503"},
        "active_dates": ["20260821"],
    }


def make_monitor(tmp_path, base_config, cgv_results, telegram=None):
    cfg = deepcopy(base_config)
    cfg["monitor"]["state_dir"] = str(tmp_path)
    state = StateManager(tmp_path)
    state.load()
    tg = telegram or FakeTelegram()
    return Monitor(cfg, state, FakeCGV(cgv_results), tg), state, tg


def test_new_showtime_sends_and_saves(tmp_path, base_config):
    monitor, state, tg = make_monitor(tmp_path, base_config, [good_result([schedule()])])
    info = monitor.run_cycle(datetime(2026, 8, 8, 12, 0, tzinfo=KST))
    assert info["alerts"] == 1
    assert len(tg.alert_calls) == 1
    assert state.watcher("yongsan_test")["known_keys"] == ["20260821|오디세이|IMAX관|07:00"]


def test_same_showtime_not_sent_twice(tmp_path, base_config):
    monitor, state, tg = make_monitor(tmp_path, base_config, [good_result([schedule()]), good_result([schedule()])])
    at = datetime(2026, 8, 8, 12, 0, tzinfo=KST)
    monitor.run_cycle(at)
    monitor.run_cycle(at)
    assert len(tg.alert_calls) == 1


def test_telegram_failure_does_not_mark_known(tmp_path, base_config):
    tg = FakeTelegram(alert_results=[False])
    monitor, state, _ = make_monitor(tmp_path, base_config, [good_result([schedule()])], tg)
    monitor.run_cycle(datetime(2026, 8, 8, 12, 0, tzinfo=KST))
    assert state.watcher("yongsan_test")["known_keys"] == []


def test_telegram_failure_retries_next_cycle(tmp_path, base_config):
    tg = FakeTelegram(alert_results=[False, True])
    monitor, state, _ = make_monitor(tmp_path, base_config, [good_result([schedule()]), good_result([schedule()])], tg)
    at = datetime(2026, 8, 8, 12, 0, tzinfo=KST)
    monitor.run_cycle(at)
    monitor.run_cycle(at)
    assert len(tg.alert_calls) == 2
    assert len(state.watcher("yongsan_test")["known_keys"]) == 1


def test_three_failures_send_one_down_alert(tmp_path, base_config):
    monitor, state, tg = make_monitor(tmp_path, base_config, [fail_result(), fail_result(), fail_result(), fail_result()])
    at = datetime(2026, 8, 8, 12, 0, tzinfo=KST)
    for _ in range(4):
        monitor.run_cycle(at)
    health_messages = [m for m in tg.plain_calls if "감시 상태 변경" in m]
    assert len(health_messages) == 1
    assert "장애" in health_messages[0]
    assert state.watcher("yongsan_test")["health"]["down_notified"] is True


def test_recovery_sends_once(tmp_path, base_config):
    monitor, state, tg = make_monitor(tmp_path, base_config, [fail_result(), fail_result(), fail_result(), good_result(), good_result()])
    at = datetime(2026, 8, 8, 12, 0, tzinfo=KST)
    for _ in range(5):
        monitor.run_cycle(at)
    messages = [m for m in tg.plain_calls if "CGV 감시" in m and ("상태 변경" in m or "감시 복구" in m)]
    assert len(messages) == 2
    assert "복구" in messages[1]
    assert state.watcher("yongsan_test")["health"]["down_notified"] is False


def test_health_message_failure_is_retried(tmp_path, base_config):
    # First two plain calls are not used; third cycle's health alert fails, fourth retries and succeeds.
    tg = FakeTelegram(plain_results=[False, True])
    monitor, state, _ = make_monitor(tmp_path, base_config, [fail_result(), fail_result(), fail_result(), fail_result()], tg)
    at = datetime(2026, 8, 8, 8, 0, tzinfo=KST)  # before heartbeat
    for _ in range(4):
        monitor.run_cycle(at)
    messages = [m for m in tg.plain_calls if "감시 상태 변경" in m]
    assert len(messages) == 2
    assert state.watcher("yongsan_test")["health"]["down_notified"] is True


def test_heartbeat_after_0900_once_per_day(tmp_path, base_config):
    monitor, state, tg = make_monitor(tmp_path, base_config, [good_result(), good_result()])
    at = datetime(2026, 8, 8, 9, 2, tzinfo=KST)
    monitor.run_cycle(at)
    monitor.run_cycle(at)
    heartbeat = [m for m in tg.plain_calls if "CGV 감시 하트비트" in m]
    assert len(heartbeat) == 1
    assert state.meta()["last_heartbeat_date"] == "2026-08-08"


def test_heartbeat_not_before_time(tmp_path, base_config):
    monitor, state, tg = make_monitor(tmp_path, base_config, [good_result()])
    monitor.run_cycle(datetime(2026, 8, 8, 8, 59, tzinfo=KST))
    assert not [m for m in tg.plain_calls if "CGV 감시 하트비트" in m]


def test_partial_failure_still_alerts_successful_date(tmp_path, base_config):
    partial = good_result([schedule()])
    partial["failed_dates"] = ["20260822"]
    partial["errors"] = {"20260822": "HTTP 503"}
    partial["active_dates"] = ["20260821", "20260822"]
    monitor, state, tg = make_monitor(tmp_path, base_config, [partial])
    monitor.run_cycle(datetime(2026, 8, 8, 12, 0, tzinfo=KST))
    assert len(tg.alert_calls) == 1
    assert state.watcher("yongsan_test")["health"]["consecutive_failures"] == 1


def test_run_cycle_passes_kst_date_to_cgv(tmp_path, base_config):
    monitor, state, tg = make_monitor(tmp_path, base_config, [good_result()])
    at = datetime(2026, 8, 8, 23, 59, tzinfo=KST)
    monitor.run_cycle(at)
    assert monitor.cgv.last_today.isoformat() == "2026-08-08"


def test_duplicate_cgv_rows_generate_one_notification_entry(tmp_path, base_config):
    duplicate = schedule()
    monitor, state, tg = make_monitor(
        tmp_path,
        base_config,
        [good_result([duplicate, deepcopy(duplicate)])],
    )
    info = monitor.run_cycle(datetime(2026, 8, 8, 12, 0, tzinfo=KST))
    assert info["new_showtimes"] == 1
    assert len(tg.alert_calls) == 1
    assert state.watcher("yongsan_test")["known_keys"] == ["20260821|오디세이|IMAX관|07:00"]


class FakeClockEvent:
    def __init__(self, clock):
        self.clock = clock
        self._set = False
        self.waits = []

    def is_set(self):
        return self._set

    def set(self):
        self._set = True

    def wait(self, seconds):
        self.waits.append(seconds)
        self.clock[0] += seconds
        return self._set


def test_run_forever_uses_cycle_start_cadence_not_work_plus_interval(tmp_path, base_config):
    clock = [0.0]
    monitor, _state, _tg = make_monitor(tmp_path, base_config, [])
    monitor.monotonic = lambda: clock[0]
    monitor.stop_event = FakeClockEvent(clock)
    monitor.install_signal_handlers = lambda: None
    starts = []

    def fake_cycle():
        starts.append(clock[0])
        clock[0] += 10.0  # pretend one scan took 10 seconds

    monitor.run_cycle = fake_cycle
    monitor.run_forever(runtime_minutes=2.0)
    assert starts == [0.0, 60.0]
    assert monitor.stop_event.waits == [50.0, 50.0]


def test_run_forever_does_not_burst_catch_up_after_overrun(tmp_path, base_config):
    clock = [0.0]
    monitor, _state, _tg = make_monitor(tmp_path, base_config, [])
    monitor.monotonic = lambda: clock[0]
    monitor.stop_event = FakeClockEvent(clock)
    monitor.install_signal_handlers = lambda: None
    starts = []

    def fake_cycle():
        starts.append(clock[0])
        clock[0] += 70.0  # longer than 60-second interval

    monitor.run_cycle = fake_cycle
    monitor.run_forever(runtime_minutes=2.1)
    assert starts == [0.0, 70.0]
    assert monitor.stop_event.waits == []


def test_no_active_dates_does_not_fake_recovery(tmp_path, base_config):
    inactive = {"schedules": [], "failed_dates": [], "errors": {}, "active_dates": []}
    monitor, state, tg = make_monitor(tmp_path, base_config, [inactive])
    health = state.watcher(base_config["watchers"][0]["id"])["health"]
    health["consecutive_failures"] = 3
    health["down_notified"] = True
    state.save()

    info = monitor.run_cycle(at=datetime(2026, 9, 1, 8, 0, tzinfo=KST))

    updated = state.watcher(base_config["watchers"][0]["id"])["health"]
    assert updated["consecutive_failures"] == 3
    assert updated["down_notified"] is True
    assert info["health_events"] == []
    assert tg.plain_calls == []


def schedule_for(date_raw: str, start: str):
    item = schedule(start=start)
    item["date_raw"] = date_raw
    item["date"] = f"{date_raw[:4]}-{date_raw[4:6]}-{date_raw[6:]}"
    return item


def test_new_showtimes_on_different_dates_are_separate_transactions(tmp_path, base_config):
    result = {
        "schedules": [schedule_for("20260821", "07:00"), schedule_for("20260822", "08:00")],
        "failed_dates": [], "errors": {}, "active_dates": ["20260821", "20260822"],
    }
    monitor, state, tg = make_monitor(tmp_path, base_config, [result])
    info = monitor.run_cycle(datetime(2026, 8, 8, 12, 0, tzinfo=KST))
    assert info["new_showtimes"] == 2
    assert info["alerts"] == 2
    assert len(tg.alert_calls) == 2
    assert "2026-08-21" in tg.alert_calls[0][1]
    assert "2026-08-22" in tg.alert_calls[1][1]
    assert len(state.watcher("yongsan_test")["known_keys"]) == 2


def test_second_date_send_failure_commits_only_first_date_and_retries_second(tmp_path, base_config):
    result = {
        "schedules": [schedule_for("20260821", "07:00"), schedule_for("20260822", "08:00")],
        "failed_dates": [], "errors": {}, "active_dates": ["20260821", "20260822"],
    }
    tg = FakeTelegram(alert_results=[True, False, True])
    monitor, state, _ = make_monitor(tmp_path, base_config, [result, deepcopy(result)], tg)
    at = datetime(2026, 8, 8, 12, 0, tzinfo=KST)
    monitor.run_cycle(at)
    keys = state.watcher("yongsan_test")["known_keys"]
    assert keys == ["20260821|오디세이|IMAX관|07:00"]
    monitor.run_cycle(at)
    assert len(tg.alert_calls) == 3
    assert "2026-08-22" in tg.alert_calls[2][1]
    assert len(state.watcher("yongsan_test")["known_keys"]) == 2


class UnexpectedErrorCGV:
    def __init__(self):
        self.calls = []
    def start_cycle(self):
        pass
    @staticmethod
    def active_dates(watcher, today=None):
        return [watcher["dates"][0].replace("-", "")]
    def scan_watcher(self, watcher, today=None):
        self.calls.append(watcher["id"])
        if watcher["id"] == "broken":
            raise KeyError("unexpected parser bug")
        return {"schedules": [], "failed_dates": [], "errors": {}, "active_dates": self.active_dates(watcher, today)}


def test_unexpected_watcher_exception_isolated_and_other_watcher_continues(tmp_path, base_config):
    cfg = deepcopy(base_config)
    cfg["monitor"]["state_dir"] = str(tmp_path)
    broken = deepcopy(cfg["watchers"][0]); broken["id"] = "broken"; broken["name"] = "broken"
    healthy = deepcopy(cfg["watchers"][0]); healthy["id"] = "healthy"; healthy["name"] = "healthy"
    cfg["watchers"] = [broken, healthy]
    state = StateManager(tmp_path); state.load()
    tg = FakeTelegram()
    cgv = UnexpectedErrorCGV()
    monitor = Monitor(cfg, state, cgv, tg)
    info = monitor.run_cycle(datetime(2026, 8, 8, 8, 0, tzinfo=KST))
    assert cgv.calls == ["broken", "healthy"]
    assert state.watcher("broken")["health"]["consecutive_failures"] == 1
    assert state.watcher("healthy")["health"]["consecutive_failures"] == 0
    assert info["alerts"] == 0


def test_run_cycle_without_explicit_time_uses_injected_clock(tmp_path, base_config):
    fixed = datetime(2026, 8, 8, 8, 30, tzinfo=KST)
    cfg = deepcopy(base_config); cfg["monitor"]["state_dir"] = str(tmp_path)
    state = StateManager(tmp_path); state.load()
    cgv = FakeCGV([good_result()]); tg = FakeTelegram()
    monitor = Monitor(cfg, state, cgv, tg, now_fn=lambda: fixed)
    monitor.run_cycle()
    assert cgv.last_today.isoformat() == "2026-08-08"
    assert not [m for m in tg.plain_calls if "하트비트" in m]
