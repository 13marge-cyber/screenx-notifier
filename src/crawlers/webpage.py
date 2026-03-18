"""
범용 웹페이지 변경 감지 크롤러

어떤 웹사이트든 CSS 선택자로 특정 영역을 지정하여
변경 사항을 감지하고 알림을 보냅니다.

사용 예:
  - 무주산골영화제 공지사항 새 글 감지
  - 영화제 예매 페이지 변경 감지
  - 기타 게시판/공지 페이지 모니터링
"""

import re
import logging
from typing import Optional
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
}


class WebpageCrawler:
    """범용 웹페이지 변경 감지 크롤러"""

    def __init__(self, settings: dict):
        self.url = settings.get("url", "")
        self.selector = settings.get("selector", "body")
        self.encoding = settings.get("encoding", "utf-8")
        self.keywords = [kw.upper() for kw in settings.get("keywords", [])]
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def check(self) -> dict:
        """
        웹페이지를 조회하여 결과를 반환합니다.

        Returns:
            {
                "items": [...],      # 감지된 항목 목록
                "raw_data": str,     # 변경 감지용 원본 데이터
            }
        """
        try:
            resp = self.session.get(self.url, timeout=15)
            resp.encoding = self.encoding
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.error(f"웹페이지 조회 실패 ({self.url}): {e}")
            return {"items": [], "raw_data": ""}

        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        # CSS 선택자로 대상 요소 추출
        elements = soup.select(self.selector)
        if not elements:
            logger.warning(
                f"선택자 '{self.selector}'에 해당하는 요소가 없습니다: {self.url}"
            )
            return {"items": [], "raw_data": ""}

        items = []
        raw_parts = []

        for el in elements:
            text = el.get_text(strip=True)
            if not text:
                continue

            raw_parts.append(text)

            # 키워드 필터링
            if self.keywords:
                if not any(kw in text.upper() for kw in self.keywords):
                    continue

            # 링크 추출 (게시판 제목인 경우)
            link_el = el.find("a") if el.name != "a" else el
            link = ""
            if link_el and link_el.get("href"):
                href = link_el["href"]
                # JavaScript 링크 처리
                js_match = re.search(r"OnReadArticle\('(\d+)'\)", href)
                if js_match:
                    seq = js_match.group(1)
                    # 무주산골영화제 게시글 URL 패턴
                    board_match = re.search(r"strBoardID=([^&]+)", self.url)
                    board_id = board_match.group(1) if board_match else ""
                    link = (
                        f"https://mjff.or.kr/kor/artyboard/mboard.asp"
                        f"?Action=view&strBoardID={board_id}&intSeq={seq}"
                    )
                elif href.startswith("http"):
                    link = href
                elif href.startswith("/"):
                    link = urljoin(self.url, href)

            items.append({
                "title": text[:200],
                "link": link,
            })

        return {
            "items": items,
            "raw_data": "\n".join(raw_parts),
        }

    def format_message(self, watcher_name: str, items: list[dict]) -> str:
        """알림 메시지를 포맷팅합니다 (Telegram MarkdownV2)."""
        if not items:
            return ""

        lines = []
        lines.append(f"📢 *{_esc(watcher_name)} 업데이트\\!*\n")
        lines.append(f"새로운 항목이 감지되었습니다\\.\n")

        for item in items[:10]:  # 최대 10개까지 표시
            title = _esc(item["title"][:100])
            link = item.get("link", "")
            if link:
                lines.append(f"  • [{title}]({_esc(link)})")
            else:
                lines.append(f"  • {title}")

        if len(items) > 10:
            lines.append(f"\n\\.\\.\\. 외 {len(items) - 10}건")

        lines.append(f"\n[🔗 페이지 바로가기]({_esc(self.url)})")
        return "\n".join(lines)


def _esc(text: str) -> str:
    """Telegram MarkdownV2 이스케이프"""
    for ch in r"_*[]()~`>#+-=|{}.!":
        text = text.replace(ch, f"\\{ch}")
    return text
