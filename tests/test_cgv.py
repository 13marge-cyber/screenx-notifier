from __future__ import annotations

from datetime import date

import requests
import pytest

from src.cgv import CGVClient, showtime_key
from tests.conftest import FakeHTTPSession, FakeResponse


def cgv_payload(rows):
    return {"statusCode": 0, "statusMessage": "OK", "data": rows}


def row(movie="오디세이", grade="IMAX LASER 2D", hall="IMAX관", start="0700"):
    return {
        "movNm": movie,
        "tcscnsGradNm": grade,
        "scnsNm": hall,
        "scnsrtTm": start,
        "scnendTm": "1030",
    }


def test_filter_matching_row(base_config):
    session = FakeHTTPSession(get_responses=[FakeResponse(payload=cgv_payload([row()]))])
    client = CGVClient(base_config, session=session)
    client.start_cycle()
    result = client.scan_watcher(base_config["watchers"][0], today=date(2026, 8, 8))
    assert len(result["schedules"]) == 1
    assert result["schedules"][0]["hall"] == "IMAX관"


def test_filter_wrong_movie(base_config):
    session = FakeHTTPSession(get_responses=[FakeResponse(payload=cgv_payload([row(movie="다른 영화")]))])
    client = CGVClient(base_config, session=session)
    client.start_cycle()
    result = client.scan_watcher(base_config["watchers"][0], today=date(2026, 8, 8))
    assert result["schedules"] == []


def test_filter_wrong_hall(base_config):
    session = FakeHTTPSession(get_responses=[FakeResponse(payload=cgv_payload([row(grade="2D", hall="1관")]))])
    client = CGVClient(base_config, session=session)
    client.start_cycle()
    result = client.scan_watcher(base_config["watchers"][0], today=date(2026, 8, 8))
    assert result["schedules"] == []


def test_cycle_cache_reuses_same_theater_date(base_config):
    session = FakeHTTPSession(get_responses=[FakeResponse(payload=cgv_payload([row()]))])
    client = CGVClient(base_config, session=session)
    client.start_cycle()
    a = client.fetch_date("0013", "A420", "20260821")
    b = client.fetch_date("0013", "A420", "20260821")
    assert a.error is None and b.error is None
    assert b.from_cache is True
    assert len(session.get_calls) == 1


def test_expired_dates_skipped(base_config):
    watcher = dict(base_config["watchers"][0])
    watcher["dates"] = ["2026-08-01", "2026-08-21"]
    assert CGVClient.active_dates(watcher, today=date(2026, 8, 8)) == ["20260821"]


def test_http_failure_recorded(base_config):
    session = FakeHTTPSession(get_responses=[FakeResponse(status_code=503, payload={})])
    client = CGVClient(base_config, session=session)
    client.start_cycle()
    result = client.scan_watcher(base_config["watchers"][0], today=date(2026, 8, 8))
    assert result["failed_dates"] == ["20260821"]
    assert "HTTP 503" in result["errors"]["20260821"]


def test_bad_json_recorded(base_config):
    session = FakeHTTPSession(get_responses=[FakeResponse(status_code=200, json_error=True)])
    client = CGVClient(base_config, session=session)
    client.start_cycle()
    result = client.scan_watcher(base_config["watchers"][0], today=date(2026, 8, 8))
    assert "JSON" in result["errors"]["20260821"]


def test_network_failure_has_safe_type_only(base_config):
    session = FakeHTTPSession(get_responses=[requests.ConnectionError("secret-ish-url")])
    client = CGVClient(base_config, session=session)
    client.start_cycle()
    result = client.scan_watcher(base_config["watchers"][0], today=date(2026, 8, 8))
    assert result["errors"]["20260821"] == "네트워크 오류(ConnectionError)"


def test_circuit_breaker_stops_later_requests(base_config):
    cfg = dict(base_config)
    cfg["cgv"] = dict(base_config["cgv"])
    cfg["cgv"]["circuit_breaker_failures"] = 2
    watcher = dict(base_config["watchers"][0])
    watcher["dates"] = ["2026-08-21", "2026-08-22", "2026-08-23"]
    cfg["watchers"] = [watcher]
    session = FakeHTTPSession(
        get_responses=[FakeResponse(status_code=503, payload={}), FakeResponse(status_code=503, payload={})]
    )
    client = CGVClient(cfg, session=session)
    client.start_cycle()
    result = client.scan_watcher(watcher, today=date(2026, 8, 8))
    assert len(result["failed_dates"]) == 3
    assert len(session.get_calls) == 2
    assert "회로차단기" in result["errors"]["20260823"]


def test_no_immediate_request_retry(base_config):
    session = FakeHTTPSession(get_responses=[FakeResponse(status_code=500, payload={})])
    client = CGVClient(base_config, session=session)
    client.start_cycle()
    client.fetch_date("0013", "A420", "20260821")
    assert len(session.get_calls) == 1


def test_showtime_key_stable():
    item = {"date_raw": "20260821", "movie": "오디세이", "hall": "IMAX관", "start": "07:00"}
    assert showtime_key(item) == "20260821|오디세이|IMAX관|07:00"


def test_25_hour_time_preserved(base_config):
    session = FakeHTTPSession(get_responses=[FakeResponse(payload=cgv_payload([row(start="2530")]))])
    client = CGVClient(base_config, session=session)
    client.start_cycle()
    result = client.scan_watcher(base_config["watchers"][0], today=date(2026, 8, 8))
    assert result["schedules"][0]["start"] == "25:30"


def test_circuit_breaker_isolated_per_theater(base_config):
    cfg = dict(base_config)
    cfg["cgv"] = dict(base_config["cgv"])
    cfg["cgv"]["circuit_breaker_failures"] = 2
    session = FakeHTTPSession(
        get_responses=[
            FakeResponse(status_code=503, payload={}),
            FakeResponse(status_code=503, payload={}),
            FakeResponse(payload=cgv_payload([row()])),
        ]
    )
    client = CGVClient(cfg, session=session)
    client.start_cycle()
    assert client.fetch_date("0013", "A420", "20260821").error
    assert client.fetch_date("0013", "A420", "20260822").error
    blocked = client.fetch_date("0013", "A420", "20260823")
    assert "회로차단기" in (blocked.error or "")

    # A different theater must still be queried and succeed in the same cycle.
    other = client.fetch_date("0059", "A420", "20260821")
    assert other.error is None
    assert len(session.get_calls) == 3


def test_application_level_api_errors_do_not_open_transport_breaker(base_config):
    cfg = dict(base_config)
    cfg["cgv"] = dict(base_config["cgv"])
    cfg["cgv"]["circuit_breaker_failures"] = 2
    watcher = dict(base_config["watchers"][0])
    watcher["dates"] = ["2026-08-21", "2026-08-22", "2026-08-23"]
    bad_payload = {"statusCode": 1, "statusMessage": "date-specific error", "data": []}
    session = FakeHTTPSession(
        get_responses=[FakeResponse(payload=bad_payload), FakeResponse(payload=bad_payload), FakeResponse(payload=bad_payload)]
    )
    client = CGVClient(cfg, session=session)
    client.start_cycle()
    result = client.scan_watcher(watcher, today=date(2026, 8, 8))
    assert len(result["failed_dates"]) == 3
    assert len(session.get_calls) == 3
    assert all("회로차단기" not in msg for msg in result["errors"].values())


from src.cgv import parse_cgv_time


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("700", "07:00"),
        ("0700", "07:00"),
        ("07:00", "07:00"),
        ("2430", "24:30"),
        ("24:30", "24:30"),
        ("2530", "25:30"),
        ("25:30", "25:30"),
        (700, "07:00"),
    ],
)
def test_parse_cgv_time_supported_forms(raw, expected):
    assert parse_cgv_time(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "7", "070", "0760", "4860", "4800", "ab:cd", "07:00:00"])
def test_parse_cgv_time_rejects_bad_values(raw):
    assert parse_cgv_time(raw) is None


def test_string_zero_status_code_is_success(base_config):
    payload = {"statusCode": "0", "statusMessage": "OK", "data": [row()]}
    session = FakeHTTPSession(get_responses=[FakeResponse(payload=payload)])
    client = CGVClient(base_config, session=session)
    client.start_cycle()
    result = client.scan_watcher(base_config["watchers"][0], today=date(2026, 8, 8))
    assert len(result["schedules"]) == 1
    assert result["failed_dates"] == []


def test_empty_showtime_data_is_valid_no_schedule(base_config):
    session = FakeHTTPSession(get_responses=[FakeResponse(payload=cgv_payload([]))])
    client = CGVClient(base_config, session=session)
    client.start_cycle()
    result = client.scan_watcher(base_config["watchers"][0], today=date(2026, 8, 8))
    assert result["schedules"] == []
    assert result["failed_dates"] == []


def test_nonempty_unknown_showtime_schema_fails_closed(base_config):
    payload = cgv_payload([{"movieName": "오디세이", "screenName": "IMAX", "startTime": "0700"}])
    session = FakeHTTPSession(get_responses=[FakeResponse(payload=payload)])
    client = CGVClient(base_config, session=session)
    client.start_cycle()
    result = client.scan_watcher(base_config["watchers"][0], today=date(2026, 8, 8))
    assert result["schedules"] == []
    assert result["failed_dates"] == ["20260821"]
    assert result["error_kinds"]["20260821"] == "schema"
    assert "SCHEMA_DRIFT" in result["errors"]["20260821"]


def test_non_list_data_is_schema_error(base_config):
    payload = {"statusCode": 0, "statusMessage": "OK", "data": {"movNm": "오디세이"}}
    session = FakeHTTPSession(get_responses=[FakeResponse(payload=payload)])
    client = CGVClient(base_config, session=session)
    client.start_cycle()
    result = client.scan_watcher(base_config["watchers"][0], today=date(2026, 8, 8))
    assert result["error_kinds"]["20260821"] == "schema"


def test_schema_failures_contribute_to_theater_circuit_breaker(base_config):
    cfg = dict(base_config)
    cfg["cgv"] = dict(base_config["cgv"])
    cfg["cgv"]["circuit_breaker_failures"] = 2
    watcher = dict(base_config["watchers"][0])
    watcher["dates"] = ["2026-08-21", "2026-08-22", "2026-08-23"]
    drift = cgv_payload([{"movieName": "x"}])
    session = FakeHTTPSession(get_responses=[FakeResponse(payload=drift), FakeResponse(payload=drift)])
    client = CGVClient(cfg, session=session)
    client.start_cycle()
    result = client.scan_watcher(watcher, today=date(2026, 8, 8))
    assert len(session.get_calls) == 2
    assert result["error_kinds"]["20260823"] == "circuit"


def test_colon_time_flows_into_schedule(base_config):
    session = FakeHTTPSession(get_responses=[FakeResponse(payload=cgv_payload([row(start="07:05")]))])
    client = CGVClient(base_config, session=session)
    client.start_cycle()
    result = client.scan_watcher(base_config["watchers"][0], today=date(2026, 8, 8))
    assert result["schedules"][0]["start"] == "07:05"


def test_mixed_old_and_unknown_rows_fail_closed_instead_of_partial_silent_drop(base_config):
    payload = cgv_payload([row(), {"movieName": "오디세이", "startTime": "0900", "screenName": "IMAX"}])
    session = FakeHTTPSession(get_responses=[FakeResponse(payload=payload)])
    client = CGVClient(base_config, session=session)
    client.start_cycle()
    result = client.scan_watcher(base_config["watchers"][0], today=date(2026, 8, 8))
    assert result["schedules"] == []
    assert result["error_kinds"]["20260821"] == "schema"
