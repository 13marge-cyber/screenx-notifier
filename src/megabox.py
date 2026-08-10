"""Megabox showtime client for Suwon AK Dolby Cinema monitoring.

Primary endpoint and request shape follow the current public Megabox client in
hmmhmmhm/daiso-mcp.  Hall metadata is validated fail-closed; when the current
SimpleBooking response omits hall fields, a legacy schedule endpoint is used
only as an enrichment source keyed by the same playSchdlNo.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Callable

import requests

from .timeutil import today_kst

logger = logging.getLogger(__name__)

BASE_URL = "https://www.megabox.co.kr"
BOOKING_LIST_API = f"{BASE_URL}/on/oh/ohb/SimpleBooking/selectBokdList.do"
SCHEDULE_DETAIL_API = f"{BASE_URL}/on/oh/ohc/Brch/schedulePage.do"
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/151.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE_URL,
    "Referer": f"{BASE_URL}/booking",
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


def parse_megabox_time(raw: Any) -> str | None:
    text = str(raw or "").strip()
    if not text:
        return None
    compact = text.replace(":", "")
    if not re.fullmatch(r"\d{3,4}", compact):
        return None
    if len(compact) == 3:
        compact = "0" + compact
    hour, minute = int(compact[:2]), int(compact[2:])
    if not (0 <= hour <= 47 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def _norm(text: Any) -> str:
    return re.sub(r"[^A-Z0-9가-힣]+", " ", str(text or "").upper()).strip()


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        result = int(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _base_row_valid(row: dict[str, Any]) -> bool:
    # Current maintained public client filters showtimes on these identifiers and
    # treats movieNm/playDe as optional.  v5 accepts that shape, then enriches or
    # defaults optional fields fail-closed before any target filtering.
    return (
        bool(str(row.get("playSchdlNo") or "").strip())
        and bool(str(row.get("movieNo") or "").strip())
        and bool(str(row.get("brchNo") or "").strip())
        and parse_megabox_time(row.get("playStartTime")) is not None
    )


def _keys_preview(row: dict[str, Any]) -> str:
    return ",".join(sorted(str(key) for key in row.keys())[:24])


def _hall_metadata(row: dict[str, Any]) -> tuple[str, str, str]:
    expo = str(row.get("theabExpoNm") or "").strip()
    kind = str(row.get("playKindNm") or "").strip()
    number = str(row.get("theabNo") or "").strip()
    return expo, kind, number


def _has_hall_metadata(row: dict[str, Any]) -> bool:
    # ``theabNo`` alone identifies only an auditorium number; it cannot prove
    # whether that auditorium is DOLBY CINEMA.  At least one textual hall/format
    # field is required before target filtering is allowed.
    expo, kind, _number = _hall_metadata(row)
    return bool(expo or kind)


class MegaboxClient:
    def __init__(
        self,
        config: dict[str, Any],
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        mb = config["megabox"]
        self.timeout = float(mb["timeout_seconds"])
        self.request_delay = float(mb["request_delay_seconds"])
        self.breaker_limit = int(mb["circuit_breaker_failures"])
        self.probe_days = int(mb.get("schema_probe_days", 4))
        self.canary_interval = int(mb.get("runtime_canary_interval_seconds", 900))
        self.session = session or requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self.sleep = sleep
        self.monotonic = monotonic
        self._cache: dict[tuple[str, str, str], FetchResult] = {}
        self._detail_cache: dict[tuple[str, str], FetchResult] = {}
        self._consecutive_request_failures: dict[tuple[str, str], int] = {}
        self._circuit_open: set[tuple[str, str]] = set()
        self._last_request_at: float | None = None
        self._canary_checked_at: dict[tuple[str, str], float] = {}
        self._canary_error: dict[tuple[str, str], str | None] = {}

    def start_cycle(self) -> None:
        self._cache.clear()
        self._detail_cache.clear()
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

    def _record_failure(self, key: tuple[str, str]) -> None:
        count = self._consecutive_request_failures.get(key, 0) + 1
        self._consecutive_request_failures[key] = count
        if count >= self.breaker_limit:
            self._circuit_open.add(key)

    def _record_success(self, key: tuple[str, str]) -> None:
        self._consecutive_request_failures[key] = 0
        self._circuit_open.discard(key)

    def _post_json(
        self,
        url: str,
        data: dict[str, str],
        breaker_key: tuple[str, str],
    ) -> tuple[Any | None, str | None, str | None, int | None]:
        if breaker_key in self._circuit_open:
            return None, "이번 사이클 해당 극장 메가박스 회로차단기가 열렸습니다.", "circuit", None
        self._throttle()
        try:
            response = self.session.post(url, data=data, timeout=self.timeout)
            self._last_request_at = self.monotonic()
        except requests.RequestException as exc:
            self._last_request_at = self.monotonic()
            self._record_failure(breaker_key)
            return None, f"네트워크 오류({type(exc).__name__})", "network", None
        if response.status_code != 200:
            self._record_failure(breaker_key)
            return None, f"HTTP {response.status_code}", "http", response.status_code
        try:
            payload = response.json()
        except ValueError:
            self._record_failure(breaker_key)
            return None, "JSON 파싱 실패", "json", response.status_code
        return payload, None, None, response.status_code

    @staticmethod
    def _booking_form(theater_code: str, area_code: str, date_raw: str) -> dict[str, str]:
        return {
            "playDe": date_raw,
            "sellChnlCd": "ONLINE",
            "brchNoListCnt": "1",
            "areaCd1": area_code,
            "spclbYn1": "N",
            "theabKindCd1": "",
            "brchNo1": theater_code,
        }

    def _fetch_detail_rows(self, theater_code: str, date_raw: str) -> FetchResult:
        cache_key = (theater_code, date_raw)
        cached = self._detail_cache.get(cache_key)
        if cached is not None:
            return FetchResult(list(cached.rows), cached.error, cached.status_code, True, cached.error_kind)
        breaker_key = (theater_code, "detail")
        payload, error, kind, status = self._post_json(
            SCHEDULE_DETAIL_API,
            {"brchNo": theater_code, "brchNo1": theater_code, "playDe": date_raw},
            breaker_key,
        )
        if error:
            result = FetchResult([], error, status, error_kind=kind)
            self._detail_cache[cache_key] = result
            return result
        if not isinstance(payload, dict):
            self._record_failure(breaker_key)
            result = FetchResult([], "SCHEMA_DRIFT: 메가박스 상세 응답이 객체가 아닙니다.", status, error_kind="schema")
            self._detail_cache[cache_key] = result
            return result
        container = payload.get("megaMap", payload)
        if not isinstance(container, dict) or "movieFormList" not in container:
            self._record_failure(breaker_key)
            result = FetchResult([], "SCHEMA_DRIFT: 메가박스 상세 movieFormList가 없습니다.", status, error_kind="schema")
            self._detail_cache[cache_key] = result
            return result
        rows = container["movieFormList"]
        if not isinstance(rows, list):
            self._record_failure(breaker_key)
            result = FetchResult([], "SCHEMA_DRIFT: 메가박스 상세 movieFormList가 리스트가 아닙니다.", status, error_kind="schema")
            self._detail_cache[cache_key] = result
            return result
        if rows and not all(isinstance(row, dict) for row in rows):
            self._record_failure(breaker_key)
            result = FetchResult([], "SCHEMA_DRIFT: 메가박스 상세 row 구조가 잘못됐습니다.", status, error_kind="schema")
            self._detail_cache[cache_key] = result
            return result
        self._record_success(breaker_key)
        result = FetchResult(rows, status_code=status)
        self._detail_cache[cache_key] = result
        return result

    def fetch_date(self, theater_code: str, area_code: str, date_raw: str) -> FetchResult:
        cache_key = (theater_code, area_code, date_raw)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return FetchResult(list(cached.rows), cached.error, cached.status_code, True, cached.error_kind)
        breaker_key = (theater_code, area_code)
        payload, error, kind, status = self._post_json(
            BOOKING_LIST_API,
            self._booking_form(theater_code, area_code, date_raw),
            breaker_key,
        )
        if error:
            result = FetchResult([], error, status, error_kind=kind)
            self._cache[cache_key] = result
            return result
        if not isinstance(payload, dict):
            self._record_failure(breaker_key)
            result = FetchResult([], "SCHEMA_DRIFT: 메가박스 응답이 객체가 아닙니다.", status, error_kind="schema")
            self._cache[cache_key] = result
            return result
        if str(payload.get("statCd", "")).strip() == "-1":
            self._record_failure(breaker_key)
            message = str(payload.get("msg") or payload.get("reason") or "입력/요청 거부").strip()
            result = FetchResult(
                [],
                f"MEGABOX_API_REJECTED: statCd=-1 ({message})",
                status,
                error_kind="schema",
            )
            self._cache[cache_key] = result
            return result

        area_branches = payload.get("areaBrchList")
        if area_branches is not None:
            if not isinstance(area_branches, list):
                self._record_failure(breaker_key)
                result = FetchResult([], "SCHEMA_DRIFT: 메가박스 areaBrchList가 리스트가 아닙니다.", status, error_kind="schema")
                self._cache[cache_key] = result
                return result
            branch_ids = {
                str(item.get("brchNo") or "").strip()
                for item in area_branches
                if isinstance(item, dict) and str(item.get("brchNo") or "").strip()
            }
            if branch_ids and theater_code not in branch_ids:
                self._record_failure(breaker_key)
                result = FetchResult(
                    [],
                    f"MEGABOX_BRANCH_MISMATCH: 요청 지점 {theater_code}가 areaBrchList에 없습니다.",
                    status,
                    error_kind="schema",
                )
                self._cache[cache_key] = result
                return result

        # The current public Megabox response type declares movieFormList as
        # optional and its maintained client treats omission as an empty list.
        # Future, not-yet-open dates can therefore legitimately be an empty object,
        # omit movieFormList while carrying area/movie lists, or expose it as null.
        # Accept only those narrow empty-envelope shapes.  An unrelated non-empty
        # object still fails closed.  A separate non-empty runtime canary proves the
        # provider is still returning real schedules, preventing provider-wide empty
        # responses from looking healthy forever.
        if "movieFormList" not in payload:
            has_known_context = (not payload) or isinstance(payload.get("areaBrchList"), list) or isinstance(
                payload.get("movieList"), list
            )
            if not has_known_context:
                self._record_failure(breaker_key)
                result = FetchResult(
                    [],
                    "SCHEMA_DRIFT: 메가박스 movieFormList가 없고 정상 빈 응답 문맥도 확인할 수 없습니다.",
                    status,
                    error_kind="schema",
                )
                self._cache[cache_key] = result
                return result
            rows = []
        elif payload.get("movieFormList") is None:
            rows = []
        else:
            rows = payload["movieFormList"]
            if not isinstance(rows, list):
                self._record_failure(breaker_key)
                result = FetchResult([], "SCHEMA_DRIFT: 메가박스 movieFormList가 리스트가 아닙니다.", status, error_kind="schema")
                self._cache[cache_key] = result
                return result
        if rows and not all(isinstance(row, dict) and _base_row_valid(row) for row in rows):
            self._record_failure(breaker_key)
            bad = next((row for row in rows if not isinstance(row, dict) or not _base_row_valid(row)), None)
            keys = _keys_preview(bad) if isinstance(bad, dict) else type(bad).__name__
            result = FetchResult(
                [],
                f"SCHEMA_DRIFT: 메가박스 상영 데이터 핵심 필드 구조가 예상과 다릅니다. keys={keys}",
                status,
                error_kind="schema",
            )
            self._cache[cache_key] = result
            return result

        movie_names: dict[str, str] = {}
        movie_list = payload.get("movieList", [])
        if movie_list is not None and not isinstance(movie_list, list):
            self._record_failure(breaker_key)
            result = FetchResult([], "SCHEMA_DRIFT: 메가박스 movieList가 리스트가 아닙니다.", status, error_kind="schema")
            self._cache[cache_key] = result
            return result
        if isinstance(movie_list, list):
            for movie in movie_list:
                if not isinstance(movie, dict):
                    continue
                number = str(movie.get("movieNo") or "").strip()
                name = str(movie.get("movieNm") or "").strip()
                if number and name:
                    movie_names[number] = name

        normalized_rows: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            movie_no = str(item.get("movieNo") or "").strip()
            movie_name = str(item.get("movieNm") or "").strip() or movie_names.get(movie_no, "")
            if not movie_name:
                self._record_failure(breaker_key)
                result = FetchResult(
                    [],
                    f"SCHEMA_DRIFT: movieNo={movie_no}의 영화명을 확인할 수 없습니다. keys={_keys_preview(item)}",
                    status,
                    error_kind="schema",
                )
                self._cache[cache_key] = result
                return result
            item["movieNm"] = movie_name
            play_de = str(item.get("playDe") or "").strip() or date_raw
            if not re.fullmatch(r"\d{8}", play_de) or play_de != date_raw:
                self._record_failure(breaker_key)
                result = FetchResult(
                    [],
                    f"SCHEMA_DRIFT: 메가박스 상영일 불일치(query={date_raw}, row={play_de or 'empty'}).",
                    status,
                    error_kind="schema",
                )
                self._cache[cache_key] = result
                return result
            item["playDe"] = play_de
            normalized_rows.append(item)
        rows = normalized_rows
        if rows and not any(str(row.get("brchNo") or "").strip() == theater_code for row in rows):
            self._record_failure(breaker_key)
            result = FetchResult(
                [],
                f"MEGABOX_BRANCH_MISMATCH: 상영행에 요청 지점 {theater_code}가 하나도 없습니다.",
                status,
                error_kind="schema",
            )
            self._cache[cache_key] = result
            return result

        # Current public client confirms the base fields, but its published type does
        # not declare auditorium metadata.  Never silently treat missing hall metadata
        # as "not Dolby".  Enrich by playSchdlNo and fail the date if that cannot prove
        # the auditorium identity.
        if rows and any(not _has_hall_metadata(row) for row in rows):
            detail = self._fetch_detail_rows(theater_code, date_raw)
            if detail.error:
                self._record_failure(breaker_key)
                result = FetchResult(
                    [],
                    f"HALL_METADATA_UNAVAILABLE: {detail.error}",
                    status,
                    error_kind="schema" if detail.error_kind == "schema" else "hall-metadata",
                )
                self._cache[cache_key] = result
                return result
            by_schedule = {
                str(row.get("playSchdlNo") or "").strip(): row
                for row in detail.rows
                if isinstance(row, dict) and str(row.get("playSchdlNo") or "").strip()
            }
            enriched: list[dict[str, Any]] = []
            for row in rows:
                item = dict(row)
                if not _has_hall_metadata(item):
                    extra = by_schedule.get(str(item.get("playSchdlNo") or "").strip(), {})
                    for field in ("theabExpoNm", "playKindNm", "theabNo"):
                        if not item.get(field) and extra.get(field) is not None:
                            item[field] = extra[field]
                if not _has_hall_metadata(item):
                    self._record_failure(breaker_key)
                    result = FetchResult(
                        [],
                        "SCHEMA_DRIFT: 메가박스 상영관 식별 필드를 확인할 수 없습니다.",
                        status,
                        error_kind="schema",
                    )
                    self._cache[cache_key] = result
                    return result
                enriched.append(item)
            rows = enriched

        self._record_success(breaker_key)
        result = FetchResult(rows, status_code=status)
        self._cache[cache_key] = result
        return result

    @staticmethod
    def active_dates(watcher: dict[str, Any], today: date | None = None) -> list[str]:
        current = today or today_kst()
        return [
            _date_raw(iso_date)
            for iso_date in watcher["dates"]
            if datetime.strptime(iso_date, "%Y-%m-%d").date() >= current
        ]

    @staticmethod
    def _filter_rows(watcher: dict[str, Any], date_raw: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        hall_keywords = [_norm(value) for value in watcher["hall_keywords"]]
        movie_keywords = [_norm(value) for value in watcher["movie_keywords"]]
        matched: list[dict[str, Any]] = []
        for row in rows:
            if str(row.get("brchNo") or "").strip() != watcher["theater_code"]:
                continue
            movie = str(row.get("movieNm") or "").strip()
            if not movie or not any(keyword in _norm(movie) for keyword in movie_keywords):
                continue
            expo, kind, number = _hall_metadata(row)
            hall_blob = _norm(f"{expo} {kind}")
            if not any(keyword and keyword in hall_blob for keyword in hall_keywords):
                continue
            start = parse_megabox_time(row.get("playStartTime"))
            if start is None:
                continue
            end = parse_megabox_time(row.get("playEndTime")) or "?"
            # Prefer the field that actually names Dolby Cinema.  This avoids an
            # unhelpful hall-number-only display if the other field carries the brand.
            hall = expo or kind or (f"{number}관" if number else "DOLBY CINEMA")
            for candidate in (expo, kind):
                if "DOLBY CINEMA" in _norm(candidate):
                    hall = candidate
                    break
            matched.append(
                {
                    "provider": "megabox",
                    "schedule_id": str(row.get("playSchdlNo") or "").strip(),
                    "date_raw": date_raw,
                    "date": _fmt_date(date_raw),
                    "movie": movie,
                    "hall": hall,
                    "grade": kind,
                    "start": start,
                    "end": end,
                    "total_seats": _to_int(row.get("totSeatCnt")),
                    "remaining_seats": _to_int(row.get("restSeatCnt")),
                }
            )
        return matched

    def _runtime_canary(self, watcher: dict[str, Any], today: date | None = None) -> str | None:
        """Periodically prove that a structurally valid *non-empty* branch schedule still exists.

        Future target dates are legitimately empty before booking opens.  Without this
        independent canary, a provider-side block that starts returning an empty list
        could look identical to "not opened yet" forever.
        """
        key = (watcher["theater_code"], watcher["area_code"])
        now = self.monotonic()
        checked_at = self._canary_checked_at.get(key)
        due = checked_at is None or (now - checked_at) >= self.canary_interval
        if due:
            probe = self.probe_recent_schema(watcher, today=today)
            self._canary_error[key] = probe.error
            if probe.error:
                # Do not pin one transient canary failure for the full 5-minute
                # success interval.  Retry it after at most 60 seconds so a brief
                # network hiccup does not manufacture a stale 3-cycle health-down.
                retry_seconds = min(float(self.canary_interval), 60.0)
                self._canary_checked_at[key] = now - max(0.0, self.canary_interval - retry_seconds)
            else:
                self._canary_checked_at[key] = now
        return self._canary_error.get(key)

    def scan_watcher(self, watcher: dict[str, Any], today: date | None = None) -> dict[str, Any]:
        dates = self.active_dates(watcher, today=today)
        schedules: list[dict[str, Any]] = []
        failed_dates: list[str] = []
        errors: dict[str, str] = {}
        error_kinds: dict[str, str] = {}
        area_code = watcher["area_code"]
        provider_error = self._runtime_canary(watcher, today=today) if dates else None
        for date_raw in dates:
            result = self.fetch_date(watcher["theater_code"], area_code, date_raw)
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
            "provider_error": provider_error,
        }

    def probe_recent_schema(self, watcher: dict[str, Any], today: date | None = None) -> FetchResult:
        """Find one non-empty recent schedule response to prove hall metadata live.

        Empty future target dates are expected during booking-open monitoring and do not
        prove parser compatibility.  The strict selftest therefore checks today through
        a short configured horizon and requires at least one non-empty valid row.
        """
        current = today or today_kst()
        last_empty: FetchResult | None = None
        for offset in range(self.probe_days):
            raw = (current + timedelta(days=offset)).strftime("%Y%m%d")
            result = self.fetch_date(watcher["theater_code"], watcher["area_code"], raw)
            if result.error:
                return result
            if result.rows:
                branch_rows = [
                    row for row in result.rows
                    if str(row.get("brchNo") or "").strip() == watcher["theater_code"]
                ]
                if not branch_rows:
                    return FetchResult(
                        [],
                        f"MEGABOX_BRANCH_MISMATCH: 요청 지점 {watcher['theater_code']}의 상영행이 없습니다.",
                        result.status_code,
                        error_kind="schema",
                    )
                return FetchResult(branch_rows, status_code=result.status_code, from_cache=result.from_cache)
            last_empty = result
        return FetchResult(
            [],
            f"MEGABOX_PROBE_EMPTY: {self.probe_days}일 범위에서 비어 있지 않은 상영표를 찾지 못했습니다.",
            last_empty.status_code if last_empty else None,
            error_kind="probe-empty",
        )
