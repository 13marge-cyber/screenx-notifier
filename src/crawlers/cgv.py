"""
CGV 상영 일정 크롤러 (신규 사이트 JSON API 기반)

CGV가 React SPA로 리뉴얼되면서 레거시 HTML 엔드포인트
(/common/showtimes/iframeTheater.aspx)는 홈페이지로 301 리다이렉트됩니다.
현재는 SPA가 사용하는 공개 JSON API를 직접 호출합니다.

  1. searchSiteScnscYmdListBySite : 극장에 등록된 상영 날짜 목록
  2. searchMovScnInfo            : 특정 날짜의 상영 회차 전체

Selenium이 필요 없어 1회 조회가 1초 이내에 끝나므로
1분 간격 감시가 가능합니다.
"""

import time
import logging
import requests
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)

# CGV 극장 코드 (siteNo)
THEATER_CODES = {
    "용산아이파크몰": "0013",
    "영등포": "0059",
    "왕십리": "0074",
    "강남": "0056",
    "여의도": "0112",
    "수원": "0012",
    "부산센텀시티": "0061",
}

API_BASE = "https://cgv.co.kr/api/v1/booking"
DATE_LIST_API = f"{API_BASE}/searchSiteScnscYmdListBySite"
SHOWTIME_API = f"{API_BASE}/searchMovScnInfo"

# 이 헤더가 없으면 CGV가 JSON 대신 에러 페이지를 반환합니다.
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "ko-KR,ko;q=0.9",
    "Referer": "https://cgv.co.kr/cnm/movieBook/cinema",
}


def _norm_date(value) -> str:
    """'2026-08-06' / '20260806' / date 객체를 'YYYYMMDD'로 정규화합니다."""
    if hasattr(value, "strftime"):
        return value.strftime("%Y%m%d")
    return str(value).replace("-", "").replace("/", "").strip()


def _fmt_time(hhmm: str) -> str:
    """'1830' → '18:30'. CGV는 심야 상영을 24:30, 25:30처럼 표기합니다."""
    if not hhmm or len(hhmm) < 4:
        return hhmm or "?"
    return f"{hhmm[:2]}:{hhmm[2:4]}"


def _fmt_date(yyyymmdd: str) -> str:
    """'20260806' → '2026-08-06 (목)'"""
    try:
        dt = datetime.strptime(yyyymmdd, "%Y%m%d")
    except ValueError:
        return yyyymmdd
    weekday = "월화수목금토일"[dt.weekday()]
    return f"{dt.strftime('%Y-%m-%d')} ({weekday})"


class CGVCrawler:
    """CGV 상영 일정 크롤러 (JSON API)"""

    def __init__(self, settings: dict):
        self.site_no = str(settings.get("theater_code", "0013"))
        self.co_cd = settings.get("co_cd", "A420")

        self.hall_keywords = [
            kw.upper() for kw in settings.get("hall_keywords", ["IMAX"])
        ]
        self.movie_keywords = [
            kw.upper() for kw in settings.get("movie_keywords", [])
        ]

        # 감시 날짜 지정 방식
        #   watch_dates    : 매 full scan 때 확인할 날짜 목록
        #   priority_dates : 매 사이클마다 확인할 날짜 (가장 중요한 날짜)
        #   days_ahead     : watch_dates가 없을 때 오늘부터 N일치를 감시
        self.watch_dates = [
            _norm_date(d) for d in settings.get("watch_dates", []) or []
        ]
        self.priority_dates = [
            _norm_date(d) for d in settings.get("priority_dates", []) or []
        ]
        self.days_ahead = settings.get("days_ahead", 14)

        # 우선 날짜만 확인하는 사이클을 몇 번 지나면 전체를 훑을지
        self.full_scan_every = max(1, int(settings.get("full_scan_every", 5)))
        self.request_delay = float(settings.get("request_delay", 0.3))
        self.timeout = int(settings.get("timeout", 15))

        self._cycle = 0
        self._session = requests.Session()
        self._session.headers.update(DEFAULT_HEADERS)

    # ── 내부 API 호출 ──────────────────────────────────────────

    def _get_json(self, url: str, params: dict, retries: int = 2):
        """CGV API를 호출해 data 필드를 반환합니다. 실패 시 None."""
        for attempt in range(retries + 1):
            try:
                resp = self._session.get(url, params=params, timeout=self.timeout)
                if resp.status_code != 200:
                    logger.warning(
                        f"API 응답 코드 {resp.status_code} ({params.get('scnYmd', '')})"
                    )
                else:
                    payload = resp.json()
                    if payload.get("statusCode") == 0:
                        return payload.get("data")
                    logger.warning(f"API 오류: {payload.get('statusMessage')}")
            except Exception as e:
                logger.warning(f"API 호출 실패 ({attempt + 1}/{retries + 1}): {e}")
            if attempt < retries:
                time.sleep(1.0)
        return None

    def get_open_dates(self) -> list[str]:
        """극장에 상영 일정이 등록된 날짜 목록을 반환합니다."""
        data = self._get_json(
            DATE_LIST_API, {"coCd": self.co_cd, "siteNo": self.site_no}
        )
        if not data:
            return []
        return [row.get("scnYmd") for row in data if row.get("scnYmd")]

    def _fetch_date(self, date_raw: str) -> list[dict]:
        """특정 날짜의 상영 회차 중 키워드에 맞는 것만 추립니다."""
        data = self._get_json(
            SHOWTIME_API,
            {
                "coCd": self.co_cd,
                "siteNo": self.site_no,
                "scnYmd": date_raw,
                "rtctlScopCd": "08",
            },
        )
        if not data:
            return []

        matched = []
        for row in data:
            # 상영관: 특별관 등급명(SCREENX/아이맥스/4DX) + 상영관 이름 둘 다 검사
            grade = (row.get("tcscnsGradNm") or "").strip()
            hall_name = (row.get("scnsNm") or "").strip()
            hall_blob = f"{grade} {hall_name}".upper()
            if self.hall_keywords and not any(
                kw in hall_blob for kw in self.hall_keywords
            ):
                continue

            movie = (row.get("movNm") or "알 수 없음").strip()
            if self.movie_keywords and not any(
                kw in movie.upper() for kw in self.movie_keywords
            ):
                continue

            matched.append({
                "date_raw": date_raw,
                "date": _fmt_date(date_raw),
                "movie": movie,
                "hall": hall_name or grade,
                "grade": grade,
                "start": _fmt_time(row.get("scnsrtTm")),
                "end": _fmt_time(row.get("scnendTm")),
                "seats_left": row.get("frSeatCnt"),
                "seats_total": row.get("stcnt"),
            })
        return matched

    # ── 공개 인터페이스 ────────────────────────────────────────

    def target_dates(self) -> list[str]:
        """이번 사이클에 조회할 날짜를 결정합니다."""
        self._cycle += 1
        full_scan = (self._cycle % self.full_scan_every == 1) or not self.priority_dates

        if self.watch_dates:
            dates = self.watch_dates if full_scan else self.priority_dates
        else:
            today = datetime.now()
            span = self.days_ahead if full_scan else min(self.days_ahead, 3)
            dates = [
                (today + timedelta(days=i)).strftime("%Y%m%d")
                for i in range(span + 1)
            ]

        # 우선 날짜는 항상 포함
        merged = list(dict.fromkeys(list(dates) + self.priority_dates))
        return sorted(merged)

    def check(self) -> dict:
        """
        상영 일정을 조회합니다.

        Returns:
            {
                "schedules": [...],   # 키워드에 매칭된 상영 회차
                "keys": [...],        # 회차별 고유 키 (신규 판별용)
                "raw_data": str,      # 변경 감지용 서명
            }
        """
        schedules = []
        dates = self.target_dates()

        for i, date_raw in enumerate(dates):
            if i:
                time.sleep(self.request_delay)
            schedules.extend(self._fetch_date(date_raw))

        keys = sorted(showtime_key(s) for s in schedules)
        return {
            "schedules": schedules,
            "keys": keys,
            # 조회한 날짜를 서명에 포함해야, 우선 날짜만 훑은 사이클과
            # 전체를 훑은 사이클이 서로 '변경'으로 오인되지 않습니다.
            "raw_data": "dates=" + ",".join(dates) + "\n" + "\n".join(keys),
        }

    def close(self):
        """세션을 정리합니다."""
        try:
            self._session.close()
        except Exception:
            pass

    def format_message(self, schedules: list[dict]) -> str:
        """알림 메시지를 포맷팅합니다 (Telegram MarkdownV2)."""
        if not schedules:
            return ""

        now_str = _esc(datetime.now().strftime("%Y-%m-%d %H:%M"))
        lines = [
            "🚨 *CGV 새 상영 일정 오픈\\!*",
            f"⏰ 감지 시각: {now_str}",
            "━━━━━━━━━━━━━━━━━━━━",
            "",
        ]

        by_date_movie = defaultdict(list)
        for item in schedules:
            by_date_movie[(item["date"], item["movie"], item["hall"])].append(item)

        for (date, movie, hall), items in sorted(by_date_movie.items()):
            items.sort(key=lambda x: x["start"])
            times_str = ", ".join(i["start"] for i in items)
            lines.append(f"📅 *{_esc(date)}*")
            lines.append(f"  🎥 {_esc(movie)}")
            lines.append(f"  🏛 {_esc(hall)}")
            lines.append(f"  ⏰ {_esc(times_str)}")
            lines.append("")

        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("👇 *아래 버튼을 눌러 바로 예매하세요\\!*")
        return "\n".join(lines)


def showtime_key(item: dict) -> str:
    """상영 회차 하나를 식별하는 키. 신규 회차 판별에 사용합니다."""
    return f"{item['date_raw']}|{item['movie']}|{item['hall']}|{item['start']}"


def _esc(text: str) -> str:
    """Telegram MarkdownV2 이스케이프"""
    for ch in r"_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text
