"""
상태 관리 모듈
각 watcher의 이전 상태를 저장/비교하여 변경을 감지합니다.
"""

import json
import hashlib
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class StateManager:
    """파일 기반 상태 관리자"""

    def __init__(self, state_dir: str = "./data/state"):
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _state_path(self, watcher_name: str) -> Path:
        safe_name = "".join(
            c if c.isalnum() or c in "-_" else "_" for c in watcher_name
        )
        return self.state_dir / f"{safe_name}.json"

    def get_state(self, watcher_name: str) -> dict[str, Any]:
        """저장된 상태를 읽어옵니다."""
        path = self._state_path(watcher_name)
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"상태 파일 읽기 실패 ({watcher_name}): {e}")
        return {}

    def save_state(self, watcher_name: str, state: dict[str, Any]) -> None:
        """상태를 파일에 저장합니다."""
        path = self._state_path(watcher_name)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
        except IOError as e:
            logger.error(f"상태 파일 저장 실패 ({watcher_name}): {e}")

    def has_changed(self, watcher_name: str, new_data: Any) -> bool:
        """
        새 데이터가 이전 상태와 다른지 확인합니다.
        """
        old_state = self.get_state(watcher_name)
        old_hash = old_state.get("hash", "")
        new_hash = self._compute_hash(new_data)
        return old_hash != new_hash

    def update_hash(self, watcher_name: str, data: Any) -> None:
        """데이터 해시를 업데이트합니다."""
        state = self.get_state(watcher_name)
        state["hash"] = self._compute_hash(data)
        self.save_state(watcher_name, state)

    @staticmethod
    def _compute_hash(data: Any) -> str:
        if isinstance(data, str):
            content = data
        else:
            content = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()
