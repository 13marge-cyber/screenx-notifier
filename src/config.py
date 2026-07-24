"""
설정 파일 로더
YAML 설정 파일을 읽고 검증하는 모듈
"""

# str | None 같은 타입 힌트를 Python 3.9에서도 쓰기 위함
# (Oracle Linux 9의 기본 파이썬이 3.9입니다)
from __future__ import annotations

import os
import sys
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config.yaml"
EXAMPLE_CONFIG_PATH = Path(__file__).parent.parent / "config.example.yaml"


def load_config(path: str | Path | None = None) -> dict[str, Any]:
    """
    YAML 설정 파일을 로드합니다.
    환경변수 CONFIG_PATH 또는 인자로 경로를 지정할 수 있습니다.
    """
    config_path = Path(
        path or os.environ.get("CONFIG_PATH", str(DEFAULT_CONFIG_PATH))
    )

    if not config_path.exists():
        logger.error(
            f"설정 파일을 찾을 수 없습니다: {config_path}\n"
            f"  config.example.yaml을 config.yaml로 복사한 뒤 수정하세요:\n"
            f"  cp config.example.yaml config.yaml"
        )
        sys.exit(1)

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # 환경변수를 먼저 반영해야 합니다. 토큰을 환경변수로만 주입하는
    # 환경(GitHub Actions 등)에서 검증이 먼저 돌면 오탐으로 종료됩니다.
    _apply_env_overrides(config)
    _validate_config(config)

    return config


def _validate_config(config: dict) -> None:
    """필수 설정값 검증"""
    # 텔레그램 설정 검증
    tg = config.get("telegram", {})
    token = tg.get("bot_token", "")
    if not token or token == "YOUR_BOT_TOKEN_HERE":
        logger.error(
            "텔레그램 봇 토큰이 설정되지 않았습니다. "
            "config.yaml의 telegram.bot_token을 확인하세요."
        )
        sys.exit(1)

    chat_ids = tg.get("chat_ids", [])
    if not chat_ids or chat_ids == ["YOUR_CHAT_ID_HERE"]:
        logger.warning(
            "텔레그램 채팅 ID가 설정되지 않았습니다. "
            "/start 명령어로 봇에 메시지를 보내면 자동 등록됩니다."
        )

    # watcher 설정 검증
    watchers = config.get("watchers", [])
    if not watchers:
        logger.warning("모니터링 대상(watchers)이 설정되지 않았습니다.")

    for i, w in enumerate(watchers):
        if "name" not in w:
            logger.error(f"watcher[{i}]에 name이 없습니다.")
            sys.exit(1)
        if "type" not in w:
            logger.error(f"watcher[{i}] '{w['name']}'에 type이 없습니다.")
            sys.exit(1)
        if w["type"] not in ("cgv", "webpage"):
            logger.error(
                f"watcher[{i}] '{w['name']}'의 type '{w['type']}'은 "
                f"지원되지 않습니다. (cgv, webpage 중 선택)"
            )
            sys.exit(1)


def _apply_env_overrides(config: dict) -> None:
    """환경변수로 설정값을 오버라이드합니다. (Docker/CI 환경 지원)"""
    telegram = config.setdefault("telegram", {})

    env_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if env_token:
        telegram["bot_token"] = env_token

    env_chat_ids = os.environ.get("TELEGRAM_CHAT_IDS")
    if env_chat_ids:
        telegram["chat_ids"] = [
            cid.strip() for cid in env_chat_ids.split(",")
        ]

    env_log_level = os.environ.get("LOG_LEVEL")
    if env_log_level:
        config.setdefault("advanced", {})["log_level"] = env_log_level


def get_enabled_watchers(config: dict) -> list[dict]:
    """활성화된 watcher 목록만 반환"""
    return [w for w in config.get("watchers", []) if w.get("enabled", True)]
