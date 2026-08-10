from __future__ import annotations

from datetime import date

import pytest

from src.providers import ProviderRouter, showtime_key


class DummyClient:
    def __init__(self, label):
        self.label = label
        self.started = 0
        self.closed = 0
        self.active_calls = []
        self.scan_calls = []

    def start_cycle(self):
        self.started += 1

    def close(self):
        self.closed += 1

    def active_dates(self, watcher, today=None):
        self.active_calls.append((watcher["id"], today))
        return [self.label]

    def scan_watcher(self, watcher, today=None):
        self.scan_calls.append((watcher["id"], today))
        return {"provider": self.label}


def test_showtime_key_provider_fallback_and_legacy_compatibility():
    base = {
        "date_raw": "20260829",
        "movie": "오디세이",
        "hall": "IMAX관",
        "start": "07:00",
    }
    assert showtime_key(base) == "20260829|오디세이|IMAX관|07:00"
    assert showtime_key(dict(base, provider="cgv")) == "cgv|20260829|오디세이|IMAX관|07:00"


def test_provider_router_dispatch_lifecycle_and_unknown_provider():
    cgv = DummyClient("cgv")
    megabox = DummyClient("megabox")
    router = ProviderRouter({}, cgv=cgv, megabox=megabox)
    cgv_watcher = {"id": "c", "provider": "cgv"}
    mb_watcher = {"id": "m", "provider": "megabox"}
    today = date(2026, 8, 10)

    assert router.client_for(cgv_watcher) is cgv
    assert router.client_for(mb_watcher) is megabox
    assert router.active_dates(cgv_watcher, today=today) == ["cgv"]
    assert router.active_dates(mb_watcher, today=today) == ["megabox"]
    assert router.scan_watcher(cgv_watcher, today=today) == {"provider": "cgv"}
    assert router.scan_watcher(mb_watcher, today=today) == {"provider": "megabox"}

    router.start_cycle()
    router.close()
    assert (cgv.started, megabox.started, cgv.closed, megabox.closed) == (1, 1, 1, 1)
    with pytest.raises(ValueError, match="지원하지 않는 provider"):
        router.client_for({"id": "x", "provider": "lotte"})
