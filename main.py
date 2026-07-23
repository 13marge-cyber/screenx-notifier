#!/usr/bin/env python3
"""
Movie Club Ticket Notifier
영화 동아리 알림 봇 - 메인 엔트리포인트

사용법:
  python main.py              # 봇 실행
  python main.py --check      # 1회 즉시 확인 후 종료
  python main.py --config PATH  # 설정 파일 경로 지정
"""

import sys
import argparse
import logging

from src.config import load_config, get_enabled_watchers


def setup_logging(level: str = "INFO"):
    """로깅 설정"""
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def run_bot(config: dict):
    """텔레그램 봇을 실행합니다."""
    from src.bot.telegram_bot import MovieClubBot

    bot = MovieClubBot(config)
    bot.run()


def run_once(config: dict):
    """
    1회 확인 후 변경이 있으면 텔레그램 알림까지 보내고 종료합니다.

    GitHub Actions 등 프로세스를 상주시킬 수 없는 환경에서 사용합니다.
    봇 상주 모드와 동일한 감지/알림 로직을 그대로 재사용합니다.
    """
    import asyncio
    from telegram import Bot
    from src.bot.telegram_bot import MovieClubBot

    bot = MovieClubBot(config)

    class _Context:
        """job_queue 컨텍스트 대신 bot 핸들만 전달하는 최소 어댑터"""
        def __init__(self, tg_bot):
            self.bot = tg_bot

    async def _main() -> int:
        tg_bot = Bot(config["telegram"]["bot_token"])
        ctx = _Context(tg_bot)
        alerted = 0

        async with tg_bot:
            for watcher in bot._get_all_watchers():
                name = watcher["name"]
                info = await bot._run_single_watcher(watcher, ctx, send_alert=True)
                if info.get("error"):
                    logging.error(f"[{name}] 실패: {info['error']}")
                elif info.get("msg"):
                    logging.info(f"[{name}] 🔔 새 일정 감지 → 알림 전송")
                    alerted += 1
                else:
                    logging.info(f"[{name}] 변경 없음")
        return alerted

    count = asyncio.run(_main())
    print(f"확인 완료. 알림 {count}건 전송.")


def run_check(config: dict):
    """1회 즉시 확인 후 종료합니다. (테스트/디버깅용)"""
    from src.state import StateManager
    from src.crawlers.cgv import CGVCrawler
    from src.crawlers.webpage import WebpageCrawler

    advanced = config.get("advanced", {})
    state_dir = advanced.get("state_dir", "./data/state")
    state_mgr = StateManager(state_dir)

    watchers = get_enabled_watchers(config)
    print(f"\n{'='*50}")
    print(f"  Movie Club Ticket Notifier - 즉시 확인 모드")
    print(f"  활성 모니터링: {len(watchers)}개")
    print(f"{'='*50}\n")

    for watcher in watchers:
        name = watcher["name"]
        wtype = watcher["type"]
        settings = watcher.get("settings", {})

        print(f"[{name}] 확인 중...")

        if wtype == "cgv":
            crawler = CGVCrawler(settings)
        elif wtype == "webpage":
            crawler = WebpageCrawler(settings)
        else:
            print(f"  ⚠️ 알 수 없는 타입: {wtype}")
            continue

        try:
            result = crawler.check()
        except Exception as e:
            print(f"  ❌ 크롤링 실패: {e}")
            continue

        raw_data = result.get("raw_data", "")
        changed = state_mgr.has_changed(name, raw_data) if raw_data else False

        if wtype == "cgv":
            schedules = result.get("schedules", [])
            print(f"  📋 발견된 상영 일정: {len(schedules)}개")
            for s in schedules:
                seats = f"{s.get('seats_left')}/{s.get('seats_total')}석"
                print(
                    f"    [{s['date']}] {s['start']} {s['movie']} "
                    f"@ {s['hall']} ({seats})"
                )
        elif wtype == "webpage":
            items = result.get("items", [])
            print(f"  📋 발견된 항목: {len(items)}개")
            for item in items[:5]:
                print(f"    - {item['title'][:60]}")
            if len(items) > 5:
                print(f"    ... 외 {len(items) - 5}건")

        status = "🔔 변경 감지!" if changed else "✅ 변경 없음"
        print(f"  {status}")

        # 상태 저장 (다음 실행 시 비교 기준)
        if raw_data:
            state_mgr.update_hash(name, raw_data)
        print()

    print("확인 완료.")


def main():
    parser = argparse.ArgumentParser(
        description="Movie Club Ticket Notifier - 영화 동아리 알림 봇"
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="설정 파일 경로 (기본: config.yaml)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="1회 즉시 확인 후 종료 (테스트/디버깅용, 알림 없음)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="1회 확인 후 변경이 있으면 알림까지 전송하고 종료 (GitHub Actions용)",
    )
    args = parser.parse_args()

    config = load_config(args.config)

    log_level = config.get("advanced", {}).get("log_level", "INFO")
    setup_logging(log_level)

    if args.once:
        run_once(config)
    elif args.check:
        run_check(config)
    else:
        run_bot(config)


if __name__ == "__main__":
    main()
