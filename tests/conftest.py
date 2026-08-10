from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

KST = ZoneInfo("Asia/Seoul")


@pytest.fixture
def base_config():
    return {
        "telegram": {"bot_token": "123:TESTTOKEN", "chat_id": "999"},
        "monitor": {
            "interval_seconds": 60,
            "failure_alert_threshold": 3,
            "state_dir": "unused",
            "legacy_state_dirs": [],
            "heartbeat": {"enabled": True, "time": "09:00"},
        },
        "cgv": {
            "timeout_seconds": 6.0,
            "request_delay_seconds": 0.0,
            "circuit_breaker_failures": 3,
        },
        "megabox": {
            "timeout_seconds": 8.0,
            "request_delay_seconds": 0.0,
            "circuit_breaker_failures": 3,
            "schema_probe_days": 4,
        },
        "watchers": [
            {
                "id": "yongsan_test",
                "name": "용산 테스트",
                "enabled": True,
                "provider": "cgv",
                "theater_code": "0013",
                "theater_name": "용산아이파크몰",
                "alert_title": "용산 IMAX",
                "hall_keywords": ["IMAX"],
                "movie_keywords": ["오디세이"],
                "dates": ["2026-08-21"],
                "co_cd": "A420",
            }
        ],
    }


@pytest.fixture
def config_copy(base_config):
    return deepcopy(base_config)


@pytest.fixture
def kst_noon():
    return datetime(2026, 8, 8, 12, 34, tzinfo=KST)


class FakeResponse:
    def __init__(self, status_code=200, payload=None, json_error=False):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"ok": True, "result": {}}
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("bad json")
        return self._payload


class FakeHTTPSession:
    def __init__(self, get_responses=None, post_responses=None):
        self.headers = {}
        self.get_responses = list(get_responses or [])
        self.post_responses = list(post_responses or [])
        self.get_calls = []
        self.post_calls = []
        self.closed = False

    def get(self, url, params=None, timeout=None):
        self.get_calls.append((url, params, timeout))
        if not self.get_responses:
            raise AssertionError("unexpected GET")
        item = self.get_responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def post(self, url, json=None, data=None, timeout=None, **kwargs):
        payload = data if data is not None else json
        self.post_calls.append((url, payload, timeout))
        if not self.post_responses:
            raise AssertionError("unexpected POST")
        item = self.post_responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def close(self):
        self.closed = True
