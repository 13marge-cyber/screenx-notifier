"""
텔레그램 봇 핵심 엔진

기능:
  - /start: 봇 시작 및 채팅 ID 자동 등록
  - /status: 현재 모니터링 상태 확인
  - /check: 즉시 전체 모니터링 실행
  - /help: 도움말
  - 주기적 모니터링 및 알림 전송
"""

import logging
import asyncio
from datetime import datetime
from typing import Any

from telegram import Update, BotCommand
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)


class MovieClubBot:
    """영화 동아리 알림 텔레그램 봇"""

    def __init__(self, config: dict):
        self.config = config
        self.token = config["telegram"]["bot_token"]
        self.chat_ids: set[str] = set(config["telegram"].get("chat_ids", []))
        self.watchers_config = config.get("watchers", [])
        self.advanced = config.get("advanced", {})
        self.app: Application | None = None

        # 크롤러 인스턴스 캐시
        self._crawlers: dict[str, Any] = {}

        # 상태 관리자
        from src.state import StateManager
        state_dir = self.advanced.get("state_dir", "./data/state")
        self.state_mgr = StateManager(state_dir)

    def _get_crawler(self, watcher: dict):
        """watcher 설정에 따라 적절한 크롤러를 반환합니다."""
        name = watcher["name"]
        if name in self._crawlers:
            return self._crawlers[name]

        wtype = watcher["type"]
        settings = watcher.get("settings", {})

        if wtype == "cgv":
            from src.crawlers.cgv import CGVCrawler
            crawler = CGVCrawler(settings)
        elif wtype == "webpage":
            from src.crawlers.webpage import WebpageCrawler
            crawler = WebpageCrawler(settings)
        else:
            logger.error(f"알 수 없는 watcher 타입: {wtype}")
            return None

        self._crawlers[name] = crawler
        return crawler

    async def _send_alert(self, text: str, context: ContextTypes.DEFAULT_TYPE):
        """등록된 모든 채팅방에 알림을 전송합니다."""
        for chat_id in self.chat_ids:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    disable_web_page_preview=False,
                )
            except Exception as e:
                logger.error(f"알림 전송 실패 (chat_id={chat_id}): {e}")
                # MarkdownV2 파싱 실패 시 일반 텍스트로 재시도
                try:
                    plain = text.replace("\\", "")
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=plain,
                    )
                except Exception as e2:
                    logger.error(f"일반 텍스트 전송도 실패: {e2}")

    async def _run_single_watcher(
        self, watcher: dict, context: ContextTypes.DEFAULT_TYPE
    ) -> str | None:
        """단일 watcher를 실행하고, 변경이 감지되면 알림 메시지를 반환합니다."""
        name = watcher["name"]
        wtype = watcher["type"]
        crawler = self._get_crawler(watcher)
        if not crawler:
            return None

        try:
            result = crawler.check()
        except Exception as e:
            logger.error(f"[{name}] 크롤링 실패: {e}")
            return None

        raw_data = result.get("raw_data", "")
        if not raw_data:
            logger.debug(f"[{name}] 데이터 없음")
            return None

        # 변경 감지
        if not self.state_mgr.has_changed(name, raw_data):
            logger.debug(f"[{name}] 변경 없음")
            return None

        logger.info(f"[{name}] 변경 감지!")

        # 알림 메시지 생성
        if wtype == "cgv":
            schedules = result.get("schedules", [])
            if schedules:
                msg = crawler.format_message(schedules)
            else:
                msg = None
        elif wtype == "webpage":
            items = result.get("items", [])
            # 이전 상태에서 새로운 항목만 필터링
            old_state = self.state_mgr.get_state(name)
            old_titles = set(old_state.get("titles", []))
            new_items = [
                item for item in items if item["title"] not in old_titles
            ]
            if new_items:
                msg = crawler.format_message(name, new_items)
            else:
                msg = None

            # 현재 제목 목록 저장
            state = self.state_mgr.get_state(name)
            state["titles"] = [item["title"] for item in items]
            self.state_mgr.save_state(name, state)
        else:
            msg = None

        # 상태 업데이트
        self.state_mgr.update_hash(name, raw_data)

        return msg

    async def _scheduled_check(self, context: ContextTypes.DEFAULT_TYPE):
        """스케줄러에 의해 호출되는 주기적 모니터링"""
        job_data = context.job.data or {}
        watcher = job_data.get("watcher")
        if not watcher:
            return

        name = watcher["name"]
        logger.info(f"[{name}] 주기적 모니터링 실행")

        msg = await self._run_single_watcher(watcher, context)
        if msg:
            await self._send_alert(msg, context)

    # ── 명령어 핸들러 ──────────────────────────────────────

    async def cmd_start(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """봇 시작 및 채팅 ID 자동 등록"""
        chat_id = str(update.effective_chat.id)
        self.chat_ids.add(chat_id)
        logger.info(f"새 채팅 등록: {chat_id}")

        welcome = (
            "🎬 *영화 동아리 알림 봇*에 오신 것을 환영합니다\\!\n\n"
            "이 봇은 다음 알림을 자동으로 보내드립니다:\n\n"
            "🎟 *CGV 특별관 예매 오픈* \\- 새 상영 일정 등록 시 즉시 알림\n"
            "📢 *영화제 공지* \\- 새 공지사항 등록 시 알림\n"
            "🔔 *커스텀 알림* \\- 설정한 웹페이지 변경 감지\n\n"
            f"📌 채팅 ID: `{_esc(chat_id)}`\n\n"
            "명령어:\n"
            "/status \\- 모니터링 상태 확인\n"
            "/check \\- 즉시 전체 확인\n"
            "/help \\- 도움말"
        )
        await update.message.reply_text(welcome, parse_mode=ParseMode.MARKDOWN_V2)

    async def cmd_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """현재 모니터링 상태 확인"""
        lines = ["📊 *모니터링 상태*\n"]

        enabled_watchers = [
            w for w in self.watchers_config if w.get("enabled", True)
        ]

        if not enabled_watchers:
            lines.append("활성화된 모니터링이 없습니다\\.")
        else:
            for w in enabled_watchers:
                name = _esc(w["name"])
                interval = w.get("interval_minutes", 5)
                state = self.state_mgr.get_state(w["name"])
                last_hash = state.get("hash", "없음")[:8]
                status_icon = "🟢" if w.get("enabled", True) else "🔴"
                lines.append(
                    f"{status_icon} *{name}*\n"
                    f"   간격: {interval}분 \\| 마지막 해시: `{last_hash}`"
                )

        lines.append(f"\n👥 등록된 채팅: {len(self.chat_ids)}개")
        lines.append(f"⏰ 현재 시각: {_esc(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}")

        await update.message.reply_text(
            "\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2
        )

    async def cmd_check(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """즉시 전체 모니터링 실행"""
        await update.message.reply_text("🔍 전체 모니터링을 실행합니다...")

        enabled_watchers = [
            w for w in self.watchers_config if w.get("enabled", True)
        ]

        found_any = False
        for watcher in enabled_watchers:
            msg = await self._run_single_watcher(watcher, context)
            if msg:
                found_any = True
                await self._send_alert(msg, context)

        if not found_any:
            await update.message.reply_text(
                "✅ 모든 모니터링 대상에서 변경 사항이 없습니다."
            )

    async def cmd_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """도움말"""
        help_text = (
            "📖 *영화 동아리 알림 봇 도움말*\n\n"
            "*명령어:*\n"
            "/start \\- 봇 시작 및 알림 등록\n"
            "/status \\- 모니터링 상태 확인\n"
            "/check \\- 즉시 전체 확인 실행\n"
            "/help \\- 이 도움말\n\n"
            "*알림 종류:*\n"
            "🎟 CGV 특별관 \\(IMAX 등\\) 새 상영 일정\n"
            "📢 영화제 공지사항 새 글\n"
            "🔔 설정된 웹페이지 변경 감지\n\n"
            "*설정 변경:*\n"
            "관리자가 `config\\.yaml` 파일을 수정하여\n"
            "모니터링 대상을 추가/삭제할 수 있습니다\\.\n\n"
            "GitHub: [movie\\-club\\-ticket\\-notifier]"
            "(https://github.com/kimble125/movie\\-club\\-ticket\\-notifier)"
        )
        await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN_V2)

    # ── 봇 실행 ──────────────────────────────────────────

    async def _post_init(self, application: Application):
        """봇 초기화 후 명령어 목록 설정 및 스케줄러 등록"""
        # 명령어 목록 설정
        commands = [
            BotCommand("start", "봇 시작 및 알림 등록"),
            BotCommand("status", "모니터링 상태 확인"),
            BotCommand("check", "즉시 전체 확인"),
            BotCommand("help", "도움말"),
        ]
        await application.bot.set_my_commands(commands)

        # 활성화된 watcher에 대해 반복 작업 등록
        job_queue = application.job_queue
        enabled_watchers = [
            w for w in self.watchers_config if w.get("enabled", True)
        ]

        for watcher in enabled_watchers:
            interval = watcher.get("interval_minutes", 5) * 60
            name = watcher["name"]

            job_queue.run_repeating(
                self._scheduled_check,
                interval=interval,
                first=10,  # 시작 10초 후 첫 실행
                name=f"watch_{name}",
                data={"watcher": watcher},
            )
            logger.info(
                f"스케줄 등록: [{name}] 매 {watcher.get('interval_minutes', 5)}분"
            )

        # 시작 알림 전송
        if self.chat_ids:
            bot = application.bot
            start_msg = (
                "🚀 *영화 동아리 알림 봇이 시작되었습니다\\!*\n\n"
                f"모니터링 대상: {len(enabled_watchers)}개\n"
                f"등록된 채팅: {len(self.chat_ids)}개"
            )
            for chat_id in self.chat_ids:
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=start_msg,
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                except Exception as e:
                    logger.warning(f"시작 알림 전송 실패 ({chat_id}): {e}")

    def run(self):
        """봇을 실행합니다."""
        logger.info("텔레그램 봇 시작 중...")

        self.app = (
            Application.builder()
            .token(self.token)
            .post_init(self._post_init)
            .build()
        )

        # 명령어 핸들러 등록
        self.app.add_handler(CommandHandler("start", self.cmd_start))
        self.app.add_handler(CommandHandler("status", self.cmd_status))
        self.app.add_handler(CommandHandler("check", self.cmd_check))
        self.app.add_handler(CommandHandler("help", self.cmd_help))

        # 봇 실행 (polling 모드)
        logger.info("봇이 실행 중입니다. Ctrl+C로 종료합니다.")
        self.app.run_polling(drop_pending_updates=True)


def _esc(text: str) -> str:
    """Telegram MarkdownV2 이스케이프"""
    for ch in r"_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text
