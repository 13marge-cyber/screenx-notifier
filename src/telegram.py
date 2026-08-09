"""Minimal Telegram Bot API client: one private recipient, Rich -> Plain fallback."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

logger = logging.getLogger(__name__)

TELEGRAM_TEXT_LIMIT = 4096


@dataclass
class ApiResult:
    ok: bool
    error_code: int | None = None
    description: str | None = None
    retry_after: int | None = None
    payload: dict[str, Any] | None = None

    @property
    def transient(self) -> bool:
        code = int(self.error_code or 0)
        description = str(self.description or "")
        return description.startswith(("network:", "transport_backoff:")) or code == 429 or code >= 500

    @property
    def permanent_auth(self) -> bool:
        return int(self.error_code or 0) in {400, 401, 403}


def split_plain_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> list[str]:
    if limit < 1:
        raise ValueError("limit must be positive")
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(line) > limit:
            if current:
                chunks.append(current.rstrip("\n"))
                current = ""
            raw = line.rstrip("\n")
            while len(raw) > limit:
                chunks.append(raw[:limit])
                raw = raw[limit:]
            current = raw + ("\n" if line.endswith("\n") else "")
            continue
        if len(current) + len(line) > limit:
            chunks.append(current.rstrip("\n"))
            current = line
        else:
            current += line
    if current:
        chunks.append(current.rstrip("\n"))
    return [chunk for chunk in chunks if chunk]


class TelegramClient:
    def __init__(
        self,
        token: str,
        chat_id: str,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        timeout_seconds: float = 8.0,
    ):
        self._token = token
        self.chat_id = str(chat_id)
        self.session = session or requests.Session()
        self.sleep = sleep
        self.monotonic = monotonic
        self.timeout_seconds = timeout_seconds
        self.last_fallback_used = False
        self._send_blocked_until = 0.0
        self._send_block_reason = ""

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def _url(self, method: str) -> str:
        return f"https://api.telegram.org/bot{self._token}/{method}"

    @staticmethod
    def _result_from_response(response: requests.Response) -> ApiResult:
        try:
            data = response.json()
        except ValueError:
            data = {}
        if response.status_code == 200 and isinstance(data, dict) and data.get("ok") is True:
            return ApiResult(True, payload=data)
        error_code = None
        description = f"HTTP {response.status_code}"
        retry_after = None
        if isinstance(data, dict):
            raw_code = data.get("error_code")
            if isinstance(raw_code, int):
                error_code = raw_code
            raw_desc = data.get("description")
            if raw_desc:
                description = str(raw_desc)[:240]
            params = data.get("parameters")
            if isinstance(params, dict):
                raw_retry = params.get("retry_after")
                if isinstance(raw_retry, int):
                    retry_after = raw_retry
        return ApiResult(
            False,
            error_code or response.status_code,
            description,
            retry_after,
            data if isinstance(data, dict) else None,
        )

    @staticmethod
    def _is_send_method(method: str) -> bool:
        return method in {"sendMessage", "sendRichMessage"}

    def _block_sends(self, seconds: float, reason: str) -> None:
        seconds = max(1.0, min(float(seconds), 3600.0))
        self._send_blocked_until = max(self._send_blocked_until, self.monotonic() + seconds)
        self._send_block_reason = reason

    def _call(self, method: str, payload: dict[str, Any], max_attempts: int = 3) -> ApiResult:
        if self._is_send_method(method) and self.monotonic() < self._send_blocked_until:
            return ApiResult(False, 503, f"transport_backoff:{self._send_block_reason or 'temporary'}")

        last = ApiResult(False, description="unknown error")
        for attempt in range(1, max_attempts + 1):
            try:
                response = self.session.post(
                    self._url(method), json=payload, timeout=self.timeout_seconds
                )
            except requests.RequestException as exc:
                # Never log str(exc): requests exceptions may contain the token-bearing URL.
                last = ApiResult(False, description=f"network:{type(exc).__name__}")
                if attempt < max_attempts:
                    self.sleep(float(attempt))
                    continue
                if self._is_send_method(method):
                    self._block_sends(15.0, last.description or "network")
                return last

            last = self._result_from_response(response)
            if last.ok:
                return last
            code = int(last.error_code or 0)
            if code == 429:
                wait = max(int(last.retry_after or 1), 1)
                if wait <= 5 and attempt < max_attempts:
                    self.sleep(float(wait))
                    continue
                if self._is_send_method(method):
                    self._block_sends(float(wait), f"429:{wait}s")
                return last
            if code >= 500 and attempt < max_attempts:
                self.sleep(float(attempt))
                continue
            if code >= 500 and self._is_send_method(method):
                self._block_sends(15.0, f"HTTP {code}")
            return last
        return last

    def get_me(self) -> ApiResult:
        return self._call("getMe", {}, max_attempts=2)

    def get_chat(self) -> ApiResult:
        return self._call("getChat", {"chat_id": self.chat_id}, max_attempts=2)

    def connection_status(self) -> tuple[str, str]:
        """Return (ok|transient|permanent, human-readable message)."""
        me = self.get_me()
        if not me.ok:
            kind = "transient" if me.transient else "permanent"
            return kind, f"getMe 실패: code={me.error_code} {me.description}"
        chat = self.get_chat()
        if not chat.ok:
            kind = "transient" if chat.transient else "permanent"
            return kind, f"getChat 실패: code={chat.error_code} {chat.description}"
        result = (chat.payload or {}).get("result")
        chat_type = result.get("type") if isinstance(result, dict) else None
        if chat_type != "private":
            return "permanent", f"Telegram 수신 채팅이 private이 아닙니다: {chat_type or 'unknown'}"
        return "ok", "Telegram getMe/getChat 정상 (private)"

    def validate_connection(self) -> tuple[bool, str]:
        kind, message = self.connection_status()
        return kind == "ok", message

    def send_plain(self, text: str) -> bool:
        chunks = split_plain_text(text)
        for index, chunk in enumerate(chunks, start=1):
            result = self._call(
                "sendMessage", {"chat_id": self.chat_id, "text": chunk}, max_attempts=3
            )
            if not result.ok:
                logger.error(
                    "Telegram 일반 메시지 전송 실패(chunk=%d/%d): code=%s desc=%s",
                    index,
                    len(chunks),
                    result.error_code,
                    result.description,
                )
                return False
        return True

    def send_alert(self, rich_message: dict[str, Any], plain_text: str) -> bool:
        self.last_fallback_used = False
        rich = self._call(
            "sendRichMessage",
            {"chat_id": self.chat_id, "rich_message": rich_message},
            max_attempts=3,
        )
        if rich.ok:
            return True
        if int(rich.error_code or 0) in {401, 403}:
            logger.error(
                "Telegram 인증/권한 오류: code=%s desc=%s", rich.error_code, rich.description
            )
            return False
        if rich.transient:
            logger.error(
                "Telegram 일시 전송 장애: code=%s desc=%s", rich.error_code, rich.description
            )
            return False

        # Non-transient Rich API/format 4xx can be bypassed with ordinary text.
        logger.warning(
            "Rich Message 형식/API 실패, Plain fallback 사용: code=%s desc=%s",
            rich.error_code,
            rich.description,
        )
        self.last_fallback_used = True
        return self.send_plain(plain_text)
