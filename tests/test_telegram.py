from __future__ import annotations

import logging

import requests

from src.telegram import TelegramClient
from tests.conftest import FakeHTTPSession, FakeResponse


def ok_payload():
    return {"ok": True, "result": {"message_id": 1}}


def error_payload(code, desc="error", retry_after=None):
    data = {"ok": False, "error_code": code, "description": desc}
    if retry_after is not None:
        data["parameters"] = {"retry_after": retry_after}
    return data


def test_rich_success_no_plain():
    session = FakeHTTPSession(post_responses=[FakeResponse(200, ok_payload())])
    tg = TelegramClient("TOKEN", "1", session=session, sleep=lambda _: None)
    assert tg.send_alert({"blocks": []}, "plain") is True
    assert len(session.post_calls) == 1
    assert session.post_calls[0][0].endswith("/sendRichMessage")
    assert tg.last_fallback_used is False


def test_rich_400_falls_back_plain():
    session = FakeHTTPSession(
        post_responses=[
            FakeResponse(400, error_payload(400, "bad rich")),
            FakeResponse(200, ok_payload()),
        ]
    )
    tg = TelegramClient("TOKEN", "1", session=session, sleep=lambda _: None)
    assert tg.send_alert({"bad": True}, "plain") is True
    assert session.post_calls[1][0].endswith("/sendMessage")
    assert tg.last_fallback_used is True


def test_auth_error_does_not_plain_fallback():
    session = FakeHTTPSession(post_responses=[FakeResponse(401, error_payload(401, "unauthorized"))])
    tg = TelegramClient("TOKEN", "1", session=session, sleep=lambda _: None)
    assert tg.send_alert({"blocks": []}, "plain") is False
    assert len(session.post_calls) == 1


def test_short_429_honors_retry_after():
    waits = []
    session = FakeHTTPSession(
        post_responses=[
            FakeResponse(429, error_payload(429, "slow", 3)),
            FakeResponse(200, ok_payload()),
        ]
    )
    tg = TelegramClient("TOKEN", "1", session=session, sleep=waits.append)
    assert tg.send_plain("x") is True
    assert waits == [3.0]


def test_long_429_is_deferred_without_blocking_monitor_cycle():
    waits = []
    now = [100.0]
    session = FakeHTTPSession(post_responses=[FakeResponse(429, error_payload(429, "slow", 40))])
    tg = TelegramClient(
        "TOKEN", "1", session=session, sleep=waits.append, monotonic=lambda: now[0]
    )
    assert tg.send_plain("x") is False
    assert waits == []
    assert len(session.post_calls) == 1

    # A second send in the same cycle fails fast instead of making another HTTP call.
    assert tg.send_plain("y") is False
    assert len(session.post_calls) == 1

    now[0] = 141.0
    session.post_responses.append(FakeResponse(200, ok_payload()))
    assert tg.send_plain("z") is True
    assert len(session.post_calls) == 2


def test_network_error_retries_without_logging_token(caplog):
    session = FakeHTTPSession(
        post_responses=[requests.ConnectionError("https://api.telegram.org/botSECRETTOKEN/sendMessage"), FakeResponse(200, ok_payload())]
    )
    tg = TelegramClient("SECRETTOKEN", "1", session=session, sleep=lambda _: None)
    with caplog.at_level(logging.WARNING):
        assert tg.send_plain("x") is True
    assert "SECRETTOKEN" not in caplog.text


def test_validate_connection_getme_getchat():
    session = FakeHTTPSession(
        post_responses=[FakeResponse(200, {"ok": True, "result": {"id": 1}}), FakeResponse(200, {"ok": True, "result": {"id": 2, "type": "private"}})]
    )
    tg = TelegramClient("TOKEN", "99", session=session, sleep=lambda _: None)
    ok, message = tg.validate_connection()
    assert ok is True
    assert "정상" in message
    assert session.post_calls[1][1]["chat_id"] == "99"


def test_non_private_chat_rejected():
    session = FakeHTTPSession(
        post_responses=[
            FakeResponse(200, {"ok": True, "result": {"id": 1}}),
            FakeResponse(200, {"ok": True, "result": {"id": -1001, "type": "supergroup"}}),
        ]
    )
    tg = TelegramClient("TOKEN", "99", session=session, sleep=lambda _: None)
    ok, message = tg.validate_connection()
    assert ok is False
    assert "private" in message


def test_rich_network_failure_does_not_double_attempt_with_plain():
    session = FakeHTTPSession(
        post_responses=[
            requests.ConnectionError("x"),
            requests.ConnectionError("x"),
            requests.ConnectionError("x"),
        ]
    )
    tg = TelegramClient("TOKEN", "1", session=session, sleep=lambda _: None)
    assert tg.send_alert({"blocks": []}, "plain") is False
    assert len(session.post_calls) == 3
    assert all(call[0].endswith("/sendRichMessage") for call in session.post_calls)
    assert tg.last_fallback_used is False
    assert tg.send_plain("later in same cycle") is False
    assert len(session.post_calls) == 3


def test_rich_500_failure_does_not_plain_fallback():
    session = FakeHTTPSession(
        post_responses=[
            FakeResponse(500, error_payload(500, "server")),
            FakeResponse(500, error_payload(500, "server")),
            FakeResponse(500, error_payload(500, "server")),
        ]
    )
    tg = TelegramClient("TOKEN", "1", session=session, sleep=lambda _: None)
    assert tg.send_alert({"blocks": []}, "plain") is False
    assert len(session.post_calls) == 3


def test_long_retry_after_is_honored_across_multiple_cycles_without_sleeping():
    waits = []
    now = [100.0]
    session = FakeHTTPSession(post_responses=[FakeResponse(429, error_payload(429, "slow", 120))])
    tg = TelegramClient(
        "TOKEN", "1", session=session, sleep=waits.append, monotonic=lambda: now[0]
    )
    assert tg.send_plain("first") is False
    assert waits == []
    assert len(session.post_calls) == 1

    # One minute later the server-requested 120-second backoff is still active.
    now[0] = 161.0
    assert tg.send_plain("still blocked") is False
    assert len(session.post_calls) == 1

    now[0] = 221.0
    session.post_responses.append(FakeResponse(200, ok_payload()))
    assert tg.send_plain("after backoff") is True
    assert len(session.post_calls) == 2


from src.telegram import split_plain_text


def test_plain_text_under_limit_not_split():
    assert split_plain_text("abc", limit=10) == ["abc"]


def test_plain_text_splits_on_line_boundaries_when_possible():
    chunks = split_plain_text("12345\n67890\nabc", limit=8)
    assert chunks == ["12345", "67890", "abc"]
    assert all(len(chunk) <= 8 for chunk in chunks)


def test_single_long_line_is_hard_split_safely():
    chunks = split_plain_text("abcdefghij", limit=4)
    assert chunks == ["abcd", "efgh", "ij"]


def test_send_plain_sends_all_chunks_in_order():
    session = FakeHTTPSession(post_responses=[FakeResponse(200, ok_payload()) for _ in range(3)])
    tg = TelegramClient("TOKEN", "1", session=session, sleep=lambda _: None)
    text = "a" * 9000
    assert tg.send_plain(text) is True
    assert len(session.post_calls) == 3
    joined = "".join(call[1]["text"] for call in session.post_calls)
    assert joined == text
    assert all(len(call[1]["text"]) <= 4096 for call in session.post_calls)


def test_send_plain_stops_after_failed_chunk():
    session = FakeHTTPSession(
        post_responses=[FakeResponse(200, ok_payload()), FakeResponse(401, error_payload(401, "no"))]
    )
    tg = TelegramClient("TOKEN", "1", session=session, sleep=lambda _: None)
    assert tg.send_plain("a" * 5000) is False
    assert len(session.post_calls) == 2


def test_connection_status_network_is_transient():
    session = FakeHTTPSession(post_responses=[requests.ConnectionError("token-url"), requests.ConnectionError("token-url")])
    tg = TelegramClient("TOKEN", "1", session=session, sleep=lambda _: None)
    kind, message = tg.connection_status()
    assert kind == "transient"
    assert "getMe" in message


def test_connection_status_auth_is_permanent():
    session = FakeHTTPSession(post_responses=[FakeResponse(401, error_payload(401, "unauthorized"))])
    tg = TelegramClient("TOKEN", "1", session=session, sleep=lambda _: None)
    kind, _ = tg.connection_status()
    assert kind == "permanent"


def test_connection_status_bad_request_is_permanent():
    session = FakeHTTPSession(post_responses=[FakeResponse(400, error_payload(400, "bad token"))])
    tg = TelegramClient("TOKEN", "1", session=session, sleep=lambda _: None)
    kind, _ = tg.connection_status()
    assert kind == "permanent"
