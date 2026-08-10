"""CGV JSON API client with caching, schema guard, and per-theater circuit breaking."""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable

import requests

from .timeutil import today_kst

logger = logging.getLogger(__name__)

API_BASE = "https://cgv.co.kr/api/v1/booking"
DATE_LIST_API = f"{API_BASE}/searchSiteScnscYmdListBySite"
SHOWTIME_API = f"{API_BASE}/searchMovScnInfo"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://cgv.co.kr/cnm/movieBook/cinema",
}


@dataclass
class FetchResult:
    rows: list[dict[str, Any]]
    error: str | None = None
    status_code: int | None = None
    from_cache: bool = False
    error_kind: str | None = None


def _date_raw(iso_date: str) -> str:
    return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%Y%m%d")


def _fmt_date(raw: str) -> str:
    try:
        dt = datetime.strptime(raw, "%Y%m%d")
    except ValueError:
        return raw
    weekday = "월화수목금토일"[dt.weekday()]
    return f"{dt.strftime('%Y-%m-%d')} ({weekday})"


def parse_cgv_time(raw: Any) -> str | None:
    """Normalize CGV business-day time while preserving 24/25-hour notation."""
    text = str(raw or "").strip()
    if not text:
        return None
    compact = text.replace(":", "")
    if not re.fullmatch(r"\d{3,4}", compact):
        return None
    if len(compact) == 3:
        compact = "0" + compact
    hour = int(compact[:2])
    minute = int(compact[2:])
    # CGV can express late-night screenings as 24:xx/25:xx. 47 is a defensive
    # ceiling that still rejects obviously corrupt values without converting the day.
    if not (0 <= hour <= 47 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def showtime_key(item: dict[str, Any]) -> str:
    return f"{item['date_raw']}|{item['movie']}|{item['hall']}|{item['start']}"


def _status_ok(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if value == 0:
        return True
    return isinstance(value, str) and value.strip() == "0"


def _looks_like_showtime_row(row: dict[str, Any]) -> bool:
    movie = row.get("movNm")
    start = parse_cgv_time(row.get("scnsrtTm"))
    hall = row.get("scnsNm") or row.get("tcscnsGradNm")
    return isinstance(movie, str) and bool(movie.strip()) and bool(hall) and start is not None


class CGVClient:
    def __init__(
        self,
        config: dict[str, Any],
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        cgv = config["cgv"]
        self.timeout = float(cgv["timeout_seconds"])
        self.request_delay = float(cgv["request_delay_seconds"])
        self.breaker_limit = int(cgv["circuit_breaker_failures"])
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.sleep = sleep
        self.monotonic = monotonic
        self._cache: dict[tuple[str, str, str], FetchResult] = {}
        self._consecutive_request_failures: dict[tuple[str, str], int] = {}
        self._circuit_open: set[tuple[str, str]] = set()
        self._last_request_at: float | None = None

    def start_cycle(self) -> None:
        self._cache.clear()
        self._consecutive_request_failures.clear()
        self._circuit_open.clear()
        self._last_request_at = None

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def _throttle(self) -> None:
        if self.request_delay <= 0 or self._last_request_at is None:
            return
        remaining = self.request_delay - (self.monotonic() - self._last_request_at)
        if remaining > 0:
            self.sleep(remaining)

    def _record_failure(self, breaker_key: tuple[str, str]) -> None:
        count = self._consecutive_request_failures.get(breaker_key, 0) + 1
        self._consecutive_request_failures[breaker_key] = count
        if count >= self.breaker_limit:
            self._circuit_open.add(breaker_key)

    def _record_success(self, breaker_key: tuple[str, str]) -> None:
        self._consecutive_request_failures[breaker_key] = 0
        self._circuit_open.discard(breaker_key)

    def _get_payload(
        self,
        url: str,
        params: dict[str, Any],
        breaker_key: tuple[str, str],
        *,
        expect_showtimes: bool = False,
    ) -> FetchResult:
        if breaker_key in self._circuit_open:
            return FetchResult([], "이번 사이클 해당 극장 CGV 회로차단기가 열렸습니다.", error_kind="circuit")
        self._throttle()
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
            self._last_request_at = self.monotonic()
        except requests.RequestException as exc:
            self._last_request_at = self.monotonic()
            self._record_failure(breaker_key)
            return FetchResult([], f"네트워크 오류({type(exc).__name__})", error_kind="network")

        if response.status_code != 200:
            self._record_failure(breaker_key)
            return FetchResult(
                [], f"HTTP {response.status_code}", response.status_code, error_kind="http"
            )
        try:
            payload = response.json()
        except ValueError:
            self._record_failure(breaker_key)
            return FetchResult([], "JSON 파싱 실패", response.status_code, error_kind="json")

        if not isinstance(payload, dict) or not _status_ok(payload.get("statusCode")):
            # Transport was healthy; a CGV application-level error can be date-specific.
            self._record_success(breaker_key)
            message = (
                str(payload.get("statusMessage") or "CGV API 오류")
                if isinstance(payload, dict)
                else "CGV API 오류"
            )
            return FetchResult([], message[:160], response.status_code, error_kind="api")

        # `data` must be present and must be a list.  Do not use ``or []`` here:
        # falsey schema-drift values such as {}, "", 0, False or None would then be
        # indistinguishable from a legitimate empty schedule list and could cause a
        # silent miss.  v5 deliberately fails closed instead.
        if "data" not in payload:
            self._record_failure(breaker_key)
            return FetchResult(
                [],
                "SCHEMA_DRIFT: CGV data 필드가 없습니다.",
                response.status_code,
                error_kind="schema",
            )
        data = payload["data"]
        if not isinstance(data, list):
            self._record_failure(breaker_key)
            return FetchResult(
                [],
                "SCHEMA_DRIFT: CGV data 필드가 리스트가 아닙니다.",
                response.status_code,
                error_kind="schema",
            )

        if expect_showtimes and data:
            # Fail closed on partial schema drift too. A mixed old/new response must not
            # silently discard only the rows whose field names changed.
            if not all(isinstance(row, dict) and _looks_like_showtime_row(row) for row in data):
                self._record_failure(breaker_key)
                return FetchResult(
                    [],
                    "SCHEMA_DRIFT: CGV 상영 데이터 필드 구조가 예상과 다릅니다.",
                    response.status_code,
                    error_kind="schema",
                )

        self._record_success(breaker_key)
        return FetchResult(data, None, response.status_code)

    def fetch_date(self, theater_code: str, co_cd: str, date_raw: str) -> FetchResult:
        key = (theater_code, co_cd, date_raw)
        cached = self._cache.get(key)
        if cached is not None:
            return FetchResult(
                list(cached.rows), cached.error, cached.status_code, True, cached.error_kind
            )
        result = self._get_payload(
            SHOWTIME_API,
            {
                "coCd": co_cd,
                "siteNo": theater_code,
                "scnYmd": date_raw,
                "rtctlScopCd": "08",
            },
            (theater_code, co_cd),
            expect_showtimes=True,
        )
        self._cache[key] = result
        return result

    def probe_theater(self, theater_code: str, co_cd: str = "A420") -> FetchResult:
        return self._get_payload(
            DATE_LIST_API,
            {"coCd": co_cd, "siteNo": theater_code},
            (theater_code, co_cd),
        )

    @staticmethod
    def active_dates(watcher: dict[str, Any], today: date | None = None) -> list[str]:
        current = today or today_kst()
        result: list[str] = []
        for iso_date in watcher["dates"]:
            parsed = datetime.strptime(iso_date, "%Y-%m-%d").date()
            if parsed >= current:
                result.append(_date_raw(iso_date))
        return result

    @staticmethod
    def _filter_rows(
        watcher: dict[str, Any],
        date_raw: str,
        rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        hall_keywords = [str(keyword).upper() for keyword in watcher["hall_keywords"]]
        movie_keywords = [str(keyword).upper() for keyword in watcher["movie_keywords"]]
        matched: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            grade = str(row.get("tcscnsGradNm") or "").strip()
            hall_name = str(row.get("scnsNm") or "").strip()
            hall_blob = f"{grade} {hall_name}".upper()
            if not any(keyword in hall_blob for keyword in hall_keywords):
                continue
            movie = str(row.get("movNm") or "").strip()
            if not movie or not any(keyword in movie.upper() for keyword in movie_keywords):
                continue
            start = parse_cgv_time(row.get("scnsrtTm"))
            if start is None:
                continue
            end = parse_cgv_time(row.get("scnendTm")) or "?"
            matched.append(
                {
                    "provider": "cgv",
                    "schedule_id": None,
                    "date_raw": date_raw,
                    "date": _fmt_date(date_raw),
                    "movie": movie,
                    "hall": hall_name or grade or "알 수 없음",
                    "grade": grade,
                    "start": start,
                    "end": end,
                }
            )
        return matched

    def scan_watcher(self, watcher: dict[str, Any], today: date | None = None) -> dict[str, Any]:
        dates = self.active_dates(watcher, today=today)
        schedules: list[dict[str, Any]] = []
        failed_dates: list[str] = []
        errors: dict[str, str] = {}
        error_kinds: dict[str, str] = {}
        for date_raw in dates:
            result = self.fetch_date(
                watcher["theater_code"], watcher.get("co_cd", "A420"), date_raw
            )
            if result.error:
                failed_dates.append(date_raw)
                errors[date_raw] = result.error
                error_kinds[date_raw] = result.error_kind or "unknown"
                continue
            schedules.extend(self._filter_rows(watcher, date_raw, result.rows))
        schedules.sort(key=lambda item: (item["date_raw"], item["movie"], item["hall"], item["start"]))
        return {
            "schedules": schedules,
            "failed_dates": failed_dates,
            "errors": errors,
            "error_kinds": error_kinds,
            "active_dates": dates,
        }
