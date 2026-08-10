from __future__ import annotations

from copy import deepcopy
from datetime import date

import requests

from src.megabox import BOOKING_LIST_API, SCHEDULE_DETAIL_API, MegaboxClient, parse_megabox_time
from src.providers import showtime_key
from tests.conftest import FakeHTTPSession, FakeResponse


def watcher():
    return {
        "id": "mb", "name": "수원AK Dolby", "enabled": True, "provider": "megabox",
        "theater_code": "0052", "area_code": "30", "theater_name": "수원AK플라자(수원역)",
        "alert_title": "수원AK Dolby", "hall_keywords": ["DOLBY CINEMA"],
        "movie_keywords": ["오디세이", "스파이더맨 브랜드 뉴 데이"],
        "dates": ["2026-08-14", "2026-08-15", "2026-08-16"],
    }


def row(**updates):
    value = {
        "playSchdlNo": "SCH-001", "movieNo": "M1", "movieNm": "오디세이",
        "brchNo": "0052", "brchNm": "수원AK플라자(수원역)", "playDe": "20260814",
        "playStartTime": "0910", "playEndTime": "1200", "totSeatCnt": 300,
        "restSeatCnt": 298, "theabExpoNm": "DOLBY CINEMA", "playKindNm": "DOLBY CINEMA",
        "theabNo": "1",
    }
    value.update(updates); return value


def client(base_config, responses):
    cfg = deepcopy(base_config)
    session = FakeHTTPSession(post_responses=responses)
    return MegaboxClient(cfg, session=session, sleep=lambda _x: None, monotonic=lambda: 0.0), session


def test_time_parser_preserves_late_night_and_rejects_invalid():
    assert parse_megabox_time("930") == "09:30"
    assert parse_megabox_time("24:30") == "24:30"
    assert parse_megabox_time("2560") is None
    assert parse_megabox_time("bad") is None


def test_current_simple_booking_request_shape(base_config):
    c, session = client(base_config, [FakeResponse(payload={"movieFormList": []})])
    result = c.fetch_date("0052", "30", "20260814")
    assert not result.error and result.rows == []
    url, data, timeout = session.post_calls[0]
    assert url == BOOKING_LIST_API
    assert data == {"playDe": "20260814", "sellChnlCd": "ONLINE", "brchNoListCnt": "1",
                    "areaCd1": "30", "spclbYn1": "N", "theabKindCd1": "", "brchNo1": "0052"}
    assert timeout == 8.0


def test_empty_movie_form_list_is_legitimate_not_schema_drift(base_config):
    c, _ = client(base_config, [FakeResponse(payload={"movieFormList": []})])
    result = c.fetch_date("0052", "30", "20260814")
    assert result.error is None and result.error_kind is None


def test_empty_object_future_envelope_is_legitimate_but_wrong_movie_form_list_fails_closed(base_config):
    c, _ = client(base_config, [FakeResponse(payload={})])
    result = c.fetch_date("0052", "30", "20260814")
    assert result.error is None and result.rows == []
    c, _ = client(base_config, [FakeResponse(payload={"movieFormList": {}})])
    result = c.fetch_date("0052", "30", "20260814")
    assert result.error_kind == "schema"


def test_partial_base_schema_drift_fails_entire_date(base_config):
    bad = row(); bad.pop("playSchdlNo")
    c, _ = client(base_config, [FakeResponse(payload={"movieFormList": [row(), bad]})])
    result = c.fetch_date("0052", "30", "20260814")
    assert result.error_kind == "schema"
    assert result.rows == []


def test_direct_hall_metadata_matches_dolby_cinema_only(base_config):
    rows = [
        row(playSchdlNo="1", movieNm="오디세이", theabExpoNm="DOLBY CINEMA"),
        row(playSchdlNo="2", movieNm="스파이더맨: 브랜드 뉴 데이", theabExpoNm="DOLBY CINEMA"),
        row(playSchdlNo="3", movieNm="오디세이", theabExpoNm="DOLBY ATMOS", playKindNm="DOLBY ATMOS"),
        row(playSchdlNo="4", movieNm="오디세이", theabExpoNm="DOLBY VISION+ATMOS", playKindNm="DOLBY VISION+ATMOS"),
        row(playSchdlNo="5", movieNm="다른 영화", theabExpoNm="DOLBY CINEMA"),
    ]
    c, _ = client(base_config, [FakeResponse(payload={"movieFormList": rows})])
    target = watcher()
    target["dates"] = ["2026-08-14"]
    schedules = c.scan_watcher(target, today=date(2026, 8, 14))["schedules"]
    assert {s["schedule_id"] for s in schedules} == {"1", "2"}
    assert {s["movie"] for s in schedules} == {"오디세이", "스파이더맨: 브랜드 뉴 데이"}
    assert all("DOLBY CINEMA" in s["hall"].upper() for s in schedules)


def test_missing_hall_metadata_is_enriched_by_schedule_id(base_config):
    simple = row(); simple.pop("theabExpoNm"); simple.pop("playKindNm"); simple.pop("theabNo")
    detail = row(theabExpoNm="DOLBY CINEMA 1관", playKindNm="DOLBY CINEMA")
    c, session = client(base_config, [
        FakeResponse(payload={"movieFormList": [simple]}),
        FakeResponse(payload={"megaMap": {"movieFormList": [detail]}}),
    ])
    result = c.fetch_date("0052", "30", "20260814")
    assert result.error is None
    assert result.rows[0]["theabExpoNm"] == "DOLBY CINEMA 1관"
    assert session.post_calls[1][0] == SCHEDULE_DETAIL_API


def test_missing_hall_metadata_never_silently_becomes_not_dolby(base_config):
    simple = row(); simple.pop("theabExpoNm"); simple.pop("playKindNm"); simple.pop("theabNo")
    c, _ = client(base_config, [
        FakeResponse(payload={"movieFormList": [simple]}),
        FakeResponse(payload={"megaMap": {"movieFormList": []}}),
    ])
    result = c.fetch_date("0052", "30", "20260814")
    assert result.error_kind == "schema"
    assert "상영관 식별" in result.error


def test_detail_transport_failure_marks_date_failed_not_empty(base_config):
    simple = row(); simple.pop("theabExpoNm"); simple.pop("playKindNm"); simple.pop("theabNo")
    c, _ = client(base_config, [FakeResponse(payload={"movieFormList": [simple]}), requests.Timeout("x")])
    result = c.fetch_date("0052", "30", "20260814")
    assert result.error_kind == "hall-metadata"
    assert "HALL_METADATA_UNAVAILABLE" in result.error


def test_provider_safe_key_uses_megabox_schedule_id():
    item = {"provider": "megabox", "schedule_id": "SCH-77", "date_raw": "20260814",
            "movie": "오디세이", "hall": "DOLBY CINEMA", "start": "09:10"}
    assert showtime_key(item) == "megabox|20260814|SCH-77"
    changed = dict(item, hall="renamed hall", start="09:11")
    assert showtime_key(changed) == showtime_key(item)


def test_cache_avoids_duplicate_requests_same_theater_date(base_config):
    c, session = client(base_config, [FakeResponse(payload={"movieFormList": []})])
    first = c.fetch_date("0052", "30", "20260814")
    second = c.fetch_date("0052", "30", "20260814")
    assert second.from_cache and not first.from_cache
    assert len(session.post_calls) == 1


def test_http_json_and_network_errors_classified(base_config):
    c, _ = client(base_config, [FakeResponse(status_code=503, payload={})])
    assert c.fetch_date("0052", "30", "20260814").error_kind == "http"
    c, _ = client(base_config, [FakeResponse(json_error=True)])
    assert c.fetch_date("0052", "30", "20260814").error_kind == "json"
    c, _ = client(base_config, [requests.Timeout("x")])
    assert c.fetch_date("0052", "30", "20260814").error_kind == "network"


def test_probe_recent_schema_skips_empty_days_then_succeeds(base_config):
    c, session = client(base_config, [
        FakeResponse(payload={"movieFormList": []}),
        FakeResponse(payload={"movieFormList": [row(playDe="20260811")]}),
    ])
    result = c.probe_recent_schema(watcher(), today=date(2026, 8, 10))
    assert not result.error and len(result.rows) == 1
    assert session.post_calls[0][1]["playDe"] == "20260810"
    assert session.post_calls[1][1]["playDe"] == "20260811"


def test_probe_recent_schema_all_empty_is_explicit_probe_warning(base_config):
    c, _ = client(base_config, [FakeResponse(payload={"movieFormList": []}) for _ in range(4)])
    result = c.probe_recent_schema(watcher(), today=date(2026, 8, 10))
    assert result.error_kind == "probe-empty"
    assert "MEGABOX_PROBE_EMPTY" in result.error


def test_scan_reports_one_failed_date_without_poisoning_other_dates(base_config):
    w = watcher(); w["dates"] = ["2026-08-14", "2026-08-15"]
    c, _ = client(base_config, [
        FakeResponse(status_code=503, payload={}),
        FakeResponse(payload={"movieFormList": [row(playDe="20260815", playSchdlNo="S2")]}),
    ])
    result = c.scan_watcher(w, today=date(2026, 8, 14))
    assert result["failed_dates"] == ["20260814"]
    assert [x["schedule_id"] for x in result["schedules"]] == ["S2"]


def test_optional_movie_name_and_play_date_follow_current_public_shape_safely(base_config):
    simple = row()
    simple.pop("movieNm")
    simple.pop("playDe")
    payload = {
        "movieList": [{"movieNo": "M1", "movieNm": "오디세이"}],
        "movieFormList": [simple],
    }
    c, _ = client(base_config, [FakeResponse(payload=payload)])
    result = c.fetch_date("0052", "30", "20260814")
    assert result.error is None
    assert result.rows[0]["movieNm"] == "오디세이"
    assert result.rows[0]["playDe"] == "20260814"


def test_unknown_movie_name_fails_closed_instead_of_silent_filter_miss(base_config):
    simple = row()
    simple.pop("movieNm")
    c, _ = client(base_config, [FakeResponse(payload={"movieList": [], "movieFormList": [simple]})])
    result = c.fetch_date("0052", "30", "20260814")
    assert result.error_kind == "schema"
    assert "영화명" in result.error


def test_response_date_mismatch_fails_closed(base_config):
    c, _ = client(base_config, [FakeResponse(payload={"movieFormList": [row(playDe="20260815")]})])
    result = c.fetch_date("0052", "30", "20260814")
    assert result.error_kind == "schema"
    assert "상영일 불일치" in result.error


def test_recent_schema_probe_requires_requested_branch_rows(base_config):
    other = row(brchNo="9999", playDe="20260810")
    c, _ = client(base_config, [FakeResponse(payload={"movieFormList": [other]})])
    result = c.probe_recent_schema(watcher(), today=date(2026, 8, 10))
    assert result.error_kind == "schema"
    assert "BRANCH_MISMATCH" in result.error


def test_full_spiderman_title_keyword_does_not_match_unrelated_spiderman(base_config):
    rows = [
        row(playSchdlNo="brand-new-day", movieNm="스파이더맨: 브랜드 뉴 데이"),
        row(playSchdlNo="old-spiderman", movieNm="스파이더맨 2"),
    ]
    c, _ = client(base_config, [FakeResponse(payload={"movieFormList": rows})])
    target = watcher()
    target["dates"] = ["2026-08-14"]
    schedules = c.scan_watcher(target, today=date(2026, 8, 14))["schedules"]
    assert [item["schedule_id"] for item in schedules] == ["brand-new-day"]


def test_runtime_canary_is_cached_until_interval_then_refreshed(base_config):
    cfg = deepcopy(base_config)
    cfg["megabox"]["runtime_canary_interval_seconds"] = 300
    clock = [100.0]
    c = MegaboxClient(
        cfg,
        session=FakeHTTPSession(),
        sleep=lambda _x: None,
        monotonic=lambda: clock[0],
    )
    calls = []

    def fake_probe(_watcher, today=None):
        calls.append(today)
        return type("Probe", (), {"error": None})()

    c.probe_recent_schema = fake_probe
    assert c._runtime_canary(watcher(), today=date(2026, 8, 10)) is None
    clock[0] = 399.9
    assert c._runtime_canary(watcher(), today=date(2026, 8, 10)) is None
    clock[0] = 400.0
    assert c._runtime_canary(watcher(), today=date(2026, 8, 10)) is None
    assert calls == [date(2026, 8, 10), date(2026, 8, 10)]


def test_runtime_canary_error_does_not_hide_target_showtime(base_config):
    cfg = deepcopy(base_config)
    clock = [0.0]
    c = MegaboxClient(
        cfg,
        session=FakeHTTPSession(),
        sleep=lambda _x: None,
        monotonic=lambda: clock[0],
    )
    c.probe_recent_schema = lambda *_a, **_kw: type(
        "Probe", (), {"error": "MEGABOX_PROBE_EMPTY: live canary empty"}
    )()
    c.fetch_date = lambda _theater, _area, raw: type(
        "Result",
        (),
        {
            "error": None,
            "error_kind": None,
            "rows": [row(playDe=raw, playSchdlNo=f"S-{raw}")],
        },
    )()
    target = watcher()
    target["dates"] = ["2026-08-14"]
    result = c.scan_watcher(target, today=date(2026, 8, 14))
    assert "MEGABOX_PROBE_EMPTY" in result["provider_error"]
    assert [item["schedule_id"] for item in result["schedules"]] == ["S-20260814"]
    assert result["failed_dates"] == []


def test_detail_response_schema_guards_and_cache(base_config):
    # Non-object, missing list, wrong list type, and a non-dict row must all fail closed.
    for payload in ([], {}, {"megaMap": {}}, {"megaMap": {"movieFormList": {}}}, {"megaMap": {"movieFormList": ["bad"]}}):
        c, _ = client(base_config, [FakeResponse(payload=payload)])
        result = c._fetch_detail_rows("0052", "20260814")
        assert result.error_kind == "schema"

    c, session = client(base_config, [FakeResponse(payload={"megaMap": {"movieFormList": [row()]}})])
    first = c._fetch_detail_rows("0052", "20260814")
    second = c._fetch_detail_rows("0052", "20260814")
    assert first.error is None and second.from_cache
    assert len(session.post_calls) == 1


def test_movie_list_wrong_type_and_non_dict_entry_handling(base_config):
    c, _ = client(base_config, [FakeResponse(payload={"movieList": {}, "movieFormList": [row()]})])
    assert c.fetch_date("0052", "30", "20260814").error_kind == "schema"

    simple = row(movieNm="")
    payload = {
        "movieList": ["ignore", {"movieNo": "M1", "movieNm": "오디세이"}],
        "movieFormList": [simple],
    }
    c, _ = client(base_config, [FakeResponse(payload=payload)])
    result = c.fetch_date("0052", "30", "20260814")
    assert result.error is None and result.rows[0]["movieNm"] == "오디세이"


def test_circuit_breaker_and_throttle_reset_each_cycle(base_config):
    cfg = deepcopy(base_config)
    cfg["megabox"]["circuit_breaker_failures"] = 2
    cfg["megabox"]["request_delay_seconds"] = 1.0
    clock = [0.0]
    sleeps = []

    def do_sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    session = FakeHTTPSession(post_responses=[
        FakeResponse(status_code=503, payload={}),
        FakeResponse(status_code=503, payload={}),
        FakeResponse(payload={"movieFormList": []}),
    ])
    c = MegaboxClient(cfg, session=session, sleep=do_sleep, monotonic=lambda: clock[0])
    first = c.fetch_date("0052", "30", "20260810")
    second = c.fetch_date("0052", "30", "20260811")
    third = c.fetch_date("0052", "30", "20260812")
    assert first.error_kind == second.error_kind == "http"
    assert third.error_kind == "circuit"
    assert len(session.post_calls) == 2
    assert sleeps == [1.0]

    c.start_cycle()
    recovered = c.fetch_date("0052", "30", "20260812")
    assert recovered.error is None
    assert len(session.post_calls) == 3


def test_http_200_statcd_error_and_area_branch_mismatch_fail_closed(base_config):
    c, _ = client(base_config, [FakeResponse(payload={
        "statCd": -1,
        "msg": "입력 정보가 잘못되었습니다.",
        "movieFormList": [],
    })])
    rejected = c.fetch_date("0052", "30", "20260814")
    assert rejected.error_kind == "schema"
    assert "MEGABOX_API_REJECTED" in rejected.error

    c, _ = client(base_config, [FakeResponse(payload={
        "areaBrchList": [{"brchNo": "9999", "brchNm": "다른 지점"}],
        "movieFormList": [],
    })])
    mismatch = c.fetch_date("0052", "30", "20260814")
    assert mismatch.error_kind == "schema"
    assert "BRANCH_MISMATCH" in mismatch.error

    c, _ = client(base_config, [FakeResponse(payload={
        "areaBrchList": {},
        "movieFormList": [],
    })])
    malformed = c.fetch_date("0052", "30", "20260814")
    assert malformed.error_kind == "schema"
    assert "areaBrchList" in malformed.error


def test_runtime_canary_cache_is_isolated_per_megabox_branch(base_config):
    cfg = deepcopy(base_config)
    clock = [0.0]
    c = MegaboxClient(
        cfg,
        session=FakeHTTPSession(),
        sleep=lambda _x: None,
        monotonic=lambda: clock[0],
    )
    calls = []

    def fake_probe(target, today=None):
        calls.append(target["theater_code"])
        error = None if target["theater_code"] == "0052" else "OTHER_BRANCH_DOWN"
        return type("Probe", (), {"error": error})()

    c.probe_recent_schema = fake_probe
    first = watcher()
    second = deepcopy(first)
    second["theater_code"] = "9999"
    second["area_code"] = "30"

    assert c._runtime_canary(first, today=date(2026, 8, 10)) is None
    assert c._runtime_canary(second, today=date(2026, 8, 10)) == "OTHER_BRANCH_DOWN"
    assert c._runtime_canary(first, today=date(2026, 8, 10)) is None
    assert calls == ["0052", "9999"]


def test_missing_movie_form_list_can_be_legitimate_future_empty_with_known_context(base_config):
    # Current maintained public type marks movieFormList optional. A future date
    # may omit it; recognized branch/movie context or an explicit null is treated
    # as empty while the separate live canary proves the provider remains healthy.
    c, _ = client(base_config, [FakeResponse(payload={
        "areaBrchList": [{"brchNo": "0052", "brchNm": "수원AK플라자(수원역)"}],
        "movieList": [],
    })])
    result = c.fetch_date("0052", "30", "20260814")
    assert result.error is None and result.rows == []

    c, _ = client(base_config, [FakeResponse(payload={"movieFormList": None, "movieList": []})])
    result = c.fetch_date("0052", "30", "20260814")
    assert result.error is None and result.rows == []


def test_unrelated_nonempty_payload_without_movie_form_list_still_fails_closed(base_config):
    c, _ = client(base_config, [FakeResponse(payload={"statCd": "0"})])
    result = c.fetch_date("0052", "30", "20260814")
    assert result.error_kind == "schema"
    assert "정상 빈 응답 문맥" in result.error


def test_nonempty_rows_for_only_wrong_branch_fail_closed(base_config):
    other = row(brchNo="9999")
    c, _ = client(base_config, [FakeResponse(payload={"movieFormList": [other]})])
    result = c.fetch_date("0052", "30", "20260814")
    assert result.error_kind == "schema"
    assert "BRANCH_MISMATCH" in result.error


def test_auditorium_number_alone_never_counts_as_dolby_identity(base_config):
    simple = row()
    simple.pop("theabExpoNm")
    simple.pop("playKindNm")
    # Keep theabNo on purpose: number-only metadata must still trigger enrichment.
    detail = row(theabExpoNm="DOLBY CINEMA", playKindNm="DOLBY CINEMA")
    c, session = client(base_config, [
        FakeResponse(payload={"movieFormList": [simple]}),
        FakeResponse(payload={"megaMap": {"movieFormList": [detail]}}),
    ])
    result = c.fetch_date("0052", "30", "20260814")
    assert result.error is None
    assert result.rows[0]["theabExpoNm"] == "DOLBY CINEMA"
    assert len(session.post_calls) == 2


def test_runtime_canary_error_retries_after_one_minute_not_full_success_interval(base_config):
    cfg = deepcopy(base_config)
    cfg["megabox"]["runtime_canary_interval_seconds"] = 300
    clock = [0.0]
    # First canary probe errors.  At +59s it is cached; at +60s it probes again
    # and succeeds. Target fetches are supplied after each canary response.
    responses = [
        FakeResponse(status_code=503, payload={}),
        FakeResponse(payload={"movieFormList": []}),
        FakeResponse(payload={"movieFormList": []}),
        FakeResponse(payload={"movieFormList": [row(playDe="20260810")]}),
        FakeResponse(payload={"movieFormList": []}),
    ]
    session = FakeHTTPSession(post_responses=responses)
    c = MegaboxClient(cfg, session=session, sleep=lambda _x: None, monotonic=lambda: clock[0])
    w = watcher(); w["dates"] = ["2026-08-14"]

    first = c.scan_watcher(w, today=date(2026, 8, 10))
    assert first["provider_error"] is not None
    first_calls = len(session.post_calls)

    c.start_cycle(); clock[0] = 59.0
    second = c.scan_watcher(w, today=date(2026, 8, 10))
    assert second["provider_error"] is not None
    # Only the target date is fetched; canary remains cached.
    assert len(session.post_calls) == first_calls + 1

    c.start_cycle(); clock[0] = 60.0
    third = c.scan_watcher(w, today=date(2026, 8, 10))
    assert third["provider_error"] is None
