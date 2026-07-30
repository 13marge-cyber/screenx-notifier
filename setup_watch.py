#!/usr/bin/env python3
"""
감시 설정 마법사

새로운 영화 / 상영관 / 극장으로 감시를 다시 세팅할 때 쓰는 대화형 도구입니다.
극장 코드나 상영관 이름을 직접 찾을 필요 없이, CGV에서 실제 목록을
가져와 고르기만 하면 config를 만들어 줍니다.

    python setup_watch.py                  # 대화형으로 config.yaml 생성
    python setup_watch.py --loop           # GitHub Actions용 config.loop.yaml도 함께
    python setup_watch.py --list-movies    # 현재 상영작만 확인
"""

import sys
import argparse
from datetime import datetime, timedelta

import requests
import yaml

from src.crawlers.cgv import (
    DEFAULT_HEADERS,
    THEATER_CODES,
    SHOWTIME_API,
    DATE_LIST_API,
)

API_BASE = "https://cgv.co.kr/api/v1/booking"
MOVIE_LIST_API = f"{API_BASE}/searchAtktTopPostrList"
HALL_LIST_API = f"{API_BASE}/searchSscnsCdList"


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(DEFAULT_HEADERS)
    return s


def fetch_movies(s: requests.Session) -> list[dict]:
    """현재 예매율 상위 상영작 목록"""
    r = s.get(
        MOVIE_LIST_API,
        params={"coCd": "A420", "movNm": "", "div": "", "attrCd": ""},
        timeout=15,
    )
    r.raise_for_status()
    return r.json().get("data") or []


def fetch_halls(s: requests.Session) -> list[str]:
    """특별관 종류 목록 (SCREENX, IMAX, 4DX ...)"""
    r = s.get(HALL_LIST_API, params={"coCd": "A420"}, timeout=15)
    r.raise_for_status()
    data = r.json().get("data") or {}
    names = {row.get("sscnsNm") for row in data.get("tcscnsCdList", [])}
    return sorted(n for n in names if n)


def fetch_open_dates(s: requests.Session, site_no: str) -> list[str]:
    """해당 극장에 상영 일정이 등록된 날짜"""
    r = s.get(
        DATE_LIST_API, params={"coCd": "A420", "siteNo": site_no}, timeout=15
    )
    r.raise_for_status()
    return [x["scnYmd"] for x in (r.json().get("data") or []) if x.get("scnYmd")]


def probe(s: requests.Session, site_no: str, date_raw: str,
          hall_kw: str, movie_kw: str) -> int:
    """지정 조건으로 해당 날짜에 몇 회차가 잡히는지 확인"""
    r = s.get(
        SHOWTIME_API,
        params={
            "coCd": "A420", "siteNo": site_no,
            "scnYmd": date_raw, "rtctlScopCd": "08",
        },
        timeout=20,
    )
    if r.status_code != 200:
        return -1
    rows = r.json().get("data") or []
    n = 0
    for row in rows:
        blob = f"{row.get('tcscnsGradNm') or ''} {row.get('scnsNm') or ''}".upper()
        if hall_kw and hall_kw.upper() not in blob:
            continue
        if movie_kw and movie_kw.upper() not in (row.get("movNm") or "").upper():
            continue
        n += 1
    return n


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{prompt}{suffix}: ").strip()
    return val or default


def pick(title: str, options: list[str], allow_free: bool = True) -> str:
    print(f"\n── {title} ──")
    for i, o in enumerate(options, 1):
        print(f"  {i}. {o}")
    hint = "번호 선택" + (" 또는 직접 입력" if allow_free else "")
    while True:
        raw = input(f"{hint}: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        if allow_free and raw:
            return raw
        print("  다시 입력해주세요.")


def main():
    ap = argparse.ArgumentParser(description="CGV 감시 설정 마법사")
    ap.add_argument("--loop", action="store_true",
                    help="GitHub Actions용 config.loop.yaml도 함께 생성")
    ap.add_argument("--list-movies", action="store_true",
                    help="현재 상영작 목록만 출력하고 종료")
    args = ap.parse_args()

    s = _session()

    print("=" * 56)
    print("  CGV 예매 오픈 감시 설정 마법사")
    print("=" * 56)

    # ── 현재 상영작 ────────────────────────────────────────
    try:
        movies = fetch_movies(s)
    except Exception as e:
        print(f"\n[오류] CGV 접속 실패: {e}")
        print("  네트워크를 확인하세요. 해외 IP는 CGV가 차단할 수 있습니다.")
        return 1

    if args.list_movies:
        print("\n── 현재 상영작 ──")
        for m in movies:
            print(f"  {m['movNm']}  (예매율 {m.get('atktRate','?')}%)")
        return 0

    movie = pick(
        "감시할 영화 (직접 입력 시 제목 일부만 적어도 됩니다)",
        [m["movNm"] for m in movies[:12]],
    )

    # ── 극장 ───────────────────────────────────────────────
    theater_names = list(THEATER_CODES.keys())
    theater = pick("극장", theater_names, allow_free=False)
    site_no = THEATER_CODES[theater]

    # ── 상영관 ─────────────────────────────────────────────
    try:
        halls = fetch_halls(s)
    except Exception:
        halls = ["SCREENX", "IMAX", "4DX", "DOLBY ATMOS"]
    hall = pick("상영관 종류", halls)

    # ── 감시 날짜 ──────────────────────────────────────────
    print("\n── 감시할 날짜 ──")
    print("  예: 2026-08-06  또는  2026-08-06~2026-08-15 (기간)")
    print("  비워두면 오늘부터 14일간 자동 설정")
    raw_dates = ask("날짜")

    if not raw_dates:
        today = datetime.now()
        dates = [(today + timedelta(days=i)).strftime("%Y-%m-%d")
                 for i in range(15)]
    elif "~" in raw_dates:
        a, b = [x.strip() for x in raw_dates.split("~", 1)]
        d0 = datetime.strptime(a, "%Y-%m-%d")
        d1 = datetime.strptime(b, "%Y-%m-%d")
        dates = [(d0 + timedelta(days=i)).strftime("%Y-%m-%d")
                 for i in range((d1 - d0).days + 1)]
    else:
        dates = [x.strip() for x in raw_dates.split(",") if x.strip()]

    interval = ask("확인 주기(분)", "1")

    # ── 현재 상태 진단 ─────────────────────────────────────
    print("\n── 설정 확인 중 ──")
    open_dates = set(fetch_open_dates(s, site_no))
    already, not_yet = [], []
    for d in dates:
        raw = d.replace("-", "")
        if raw not in open_dates:
            not_yet.append(d)
            continue
        n = probe(s, site_no, raw, hall, movie)
        (already if n > 0 else not_yet).append(f"{d} ({n}회)" if n > 0 else d)

    if already:
        print(f"  ⚠️  이미 예매가 열린 날짜: {', '.join(already)}")
        print("      → 이 날짜들은 기준선에 포함되어 알림이 오지 않습니다.")
    if not_yet:
        print(f"  ✅ 아직 안 열린 날짜: {len(not_yet)}개 → 열리는 즉시 알림")
    if not not_yet:
        print("  ⚠️  모든 날짜가 이미 열려 있습니다. 감시 의미가 없을 수 있습니다.")

    # ── 텔레그램 ───────────────────────────────────────────
    print("\n── 텔레그램 ──")
    existing = {}
    try:
        with open("config.yaml", encoding="utf-8") as f:
            existing = yaml.safe_load(f) or {}
    except FileNotFoundError:
        pass
    tg = existing.get("telegram", {})

    token = ask("봇 토큰", tg.get("bot_token", ""))
    chat_default = ",".join(str(c) for c in tg.get("chat_ids", []))
    chat_ids = ask("채팅 ID (쉼표로 여러 개)", chat_default)

    # ── config 생성 ────────────────────────────────────────
    name = f"CGV {theater} {hall} {movie}"
    watcher = {
        "name": name,
        "type": "cgv",
        "enabled": True,
        "interval_minutes": int(interval),
        "settings": {
            "theater_code": site_no,
            "hall_keywords": [hall],
            "movie_keywords": [movie],
            # priority_dates는 비워둡니다. 특정 날짜만 자주 보고 나머지를
            # 드물게 보면, 정작 다른 날짜가 열렸을 때 알림이 늦습니다.
            "priority_dates": [],
            "watch_dates": dates,
            "full_scan_every": 1,
            "request_delay": 0.3,
            "open_mode": "both",
            "web_url": "https://cgv.co.kr/cnm/movieBook/cinema",
            "app_store_url": "https://apps.apple.com/kr/app/cgv/id388521649",
        },
    }
    advanced = {
        "heartbeat": {"enabled": True, "time": "09:00", "timezone": "Asia/Seoul"},
        "state_dir": "./data/state",
        "log_level": "INFO",
        "max_retries": 3,
    }
    config = {
        "telegram": {
            "bot_token": token,
            "chat_ids": [c.strip() for c in chat_ids.split(",") if c.strip()],
            "language": "ko",
        },
        "watchers": [watcher],
        "advanced": advanced,
    }

    with open("config.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, allow_unicode=True, sort_keys=False)
    print("\n✅ config.yaml 생성 완료")

    if args.loop:
        loop = {
            "telegram": {"bot_token": "SET_VIA_ENV", "chat_ids": [], "language": "ko"},
            "watchers": [watcher],
            "advanced": {**advanced, "state_dir": "./data/actions-state"},
        }
        with open("config.loop.yaml", "w", encoding="utf-8") as f:
            yaml.safe_dump(loop, f, allow_unicode=True, sort_keys=False)
        print("✅ config.loop.yaml 생성 완료 (토큰은 GitHub Secrets에서 주입)")

    n_req = len(dates)
    print(f"\n── 요약 ──")
    print(f"  {name}")
    print(f"  {len(dates)}개 날짜를 {interval}분마다 확인 "
          f"(CGV 요청 약 {n_req}회/{interval}분)")
    if n_req > 20 and int(interval) <= 1:
        print("  ⚠️  요청이 많습니다. 날짜를 줄이거나 주기를 늘리는 걸 권합니다.")
    print("\n다음 단계:")
    print("  python main.py --check     # 설정이 맞는지 1회 확인")
    print("  python main.py             # 감시 시작")
    return 0


if __name__ == "__main__":
    sys.exit(main())
