"""Version-neutral, atomic notifier state with one validated backup and legacy migration."""
from __future__ import annotations

import json
import logging
import os
import tempfile
from copy import deepcopy
from datetime import date
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 4


class StateError(RuntimeError):
    pass


def _default_health() -> dict[str, Any]:
    return {
        "consecutive_failures": 0,
        "down_notified": False,
        "last_success_at": None,
        "last_error": None,
    }


def _default_state() -> dict[str, Any]:
    return {
        "schema": SCHEMA_VERSION,
        "watchers": {},
        "meta": {
            "last_heartbeat_date": None,
            "migration_sources": [],
        },
    }


def _legacy_safe_name(name: str) -> str:
    return "".join(char if char.isalnum() or char in "-_" else "_" for char in name)


def _normalized_health(raw: Any) -> dict[str, Any]:
    if raw is None:
        return _default_health()
    if not isinstance(raw, dict):
        raise StateError("health 구조가 손상되었습니다.")
    result = _default_health()
    failures = raw.get("consecutive_failures", 0)
    if isinstance(failures, bool) or not isinstance(failures, int) or failures < 0:
        raise StateError("health.consecutive_failures가 손상되었습니다.")
    down = raw.get("down_notified", False)
    if not isinstance(down, bool):
        raise StateError("health.down_notified가 손상되었습니다.")
    result["consecutive_failures"] = failures
    result["down_notified"] = down
    for field in ("last_success_at", "last_error"):
        value = raw.get(field)
        if value is not None and not isinstance(value, str):
            raise StateError(f"health.{field}가 손상되었습니다.")
        result[field] = value
    return result


def _validate_state_data(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or data.get("schema") != SCHEMA_VERSION:
        raise StateError(
            f"지원하지 않는 상태 스키마입니다: {data.get('schema') if isinstance(data, dict) else 'invalid'}"
        )
    watchers = data.get("watchers")
    meta = data.get("meta")
    if not isinstance(watchers, dict) or not isinstance(meta, dict):
        raise StateError("상태 파일 구조가 손상되었습니다.")

    normalized = _default_state()
    for watcher_id, raw_item in watchers.items():
        if not isinstance(watcher_id, str) or not watcher_id:
            raise StateError("watcher id 상태 키가 손상되었습니다.")
        if not isinstance(raw_item, dict):
            raise StateError(f"{watcher_id} watcher 상태 구조가 손상되었습니다.")
        keys = raw_item.get("known_keys", [])
        if not isinstance(keys, list) or not all(isinstance(key, str) for key in keys):
            raise StateError(f"{watcher_id} known_keys 구조가 손상되었습니다.")
        normalized["watchers"][watcher_id] = {
            "known_keys": sorted(set(keys)),
            "health": _normalized_health(raw_item.get("health")),
        }

    heartbeat = meta.get("last_heartbeat_date")
    if heartbeat is not None:
        if not isinstance(heartbeat, str):
            raise StateError("meta.last_heartbeat_date가 손상되었습니다.")
        try:
            date.fromisoformat(heartbeat)
        except ValueError as exc:
            raise StateError("meta.last_heartbeat_date가 손상되었습니다.") from exc
    migration_sources = meta.get("migration_sources", [])
    if not isinstance(migration_sources, list) or not all(
        isinstance(value, str) for value in migration_sources
    ):
        raise StateError("meta.migration_sources가 손상되었습니다.")
    normalized["meta"]["last_heartbeat_date"] = heartbeat
    normalized["meta"]["migration_sources"] = list(dict.fromkeys(migration_sources))
    return normalized


class StateManager:
    def __init__(
        self,
        state_dir: str | Path,
        legacy_state_dirs: Iterable[str | Path] | None = None,
    ):
        self.state_dir = Path(state_dir)
        self.path = self.state_dir / "state.json"
        self.backup_path = self.state_dir / "state.backup.json"
        self.legacy_state_dirs = [Path(value) for value in (legacy_state_dirs or [])]
        self.data: dict[str, Any] = _default_state()
        self._loaded = False

    @staticmethod
    def _read_json(path: Path) -> Any:
        try:
            with path.open("r", encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise StateError(f"상태 파일을 읽을 수 없습니다: {path}") from exc

    def _read_valid(self, path: Path) -> dict[str, Any]:
        return _validate_state_data(self._read_json(path))

    def _atomic_write_data(self, path: Path, data: dict[str, Any]) -> None:
        payload = _validate_state_data(data)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.state_dir,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temp_path = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            temp_path = None
            try:
                directory_fd = os.open(self.state_dir, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        except OSError as exc:
            raise StateError(f"상태 저장 실패: {path}") from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def _load_primary_or_backup(self) -> dict[str, Any] | None:
        if self.path.exists():
            try:
                return self._read_valid(self.path)
            except StateError as primary_error:
                if not self.backup_path.exists():
                    raise StateError("주 상태 파일이 손상되었고 정상 백업이 없습니다.") from primary_error
                try:
                    backup = self._read_valid(self.backup_path)
                except StateError as backup_error:
                    raise StateError("주 상태 파일과 백업 상태 파일이 모두 손상되었습니다.") from backup_error
                logger.error("주 상태 파일 손상 감지 - 검증된 백업으로 복구합니다.")
                self._atomic_write_data(self.path, backup)
                return backup
        if self.backup_path.exists():
            backup = self._read_valid(self.backup_path)
            logger.warning("주 상태 파일이 없어 검증된 백업으로 복구합니다.")
            self._atomic_write_data(self.path, backup)
            return backup
        return None

    @staticmethod
    def _health_from_legacy(raw: Any) -> dict[str, Any] | None:
        try:
            return _normalized_health(raw)
        except StateError:
            return None

    def _merge_known(self, watcher_id: str, keys: Any) -> bool:
        if not isinstance(keys, list):
            return False
        valid = {str(value) for value in keys if isinstance(value, (str, int, float))}
        if not valid:
            return False
        item = self.data["watchers"].setdefault(
            watcher_id, {"known_keys": [], "health": _default_health()}
        )
        before = set(item["known_keys"])
        item["known_keys"] = sorted(before | valid)
        return set(item["known_keys"]) != before

    def _migrate_r3_single(self, path: Path, watchers: list[dict[str, Any]]) -> bool:
        if not path.exists():
            return False
        try:
            raw = self._read_json(path)
        except StateError:
            logger.warning("손상된 legacy r3 state는 자동 이관하지 않습니다: %s", path)
            return False
        if not isinstance(raw, dict) or raw.get("schema") != 3 or not isinstance(raw.get("watchers"), dict):
            return False
        changed = False
        legacy_watchers = raw["watchers"]
        for watcher in watchers:
            old = legacy_watchers.get(watcher["id"])
            if not isinstance(old, dict):
                continue
            changed |= self._merge_known(watcher["id"], old.get("known_keys"))
            health = self._health_from_legacy(old.get("health"))
            if health is not None:
                self.data["watchers"].setdefault(
                    watcher["id"], {"known_keys": [], "health": _default_health()}
                )["health"] = health
                changed = True
        meta = raw.get("meta")
        if isinstance(meta, dict):
            heartbeat = meta.get("last_heartbeat_date")
            if isinstance(heartbeat, str):
                try:
                    date.fromisoformat(heartbeat)
                except ValueError:
                    pass
                else:
                    self.data["meta"]["last_heartbeat_date"] = heartbeat
                    changed = True
        return changed

    def _migrate_per_watcher(self, directory: Path, watchers: list[dict[str, Any]]) -> bool:
        changed = False
        for watcher in watchers:
            path = directory / f"v3_{watcher['id']}.json"
            if path.exists():
                try:
                    raw = self._read_json(path)
                except StateError:
                    logger.warning("손상된 legacy v3 watcher state는 이관하지 않습니다: %s", path)
                else:
                    if isinstance(raw, dict):
                        changed |= self._merge_known(watcher["id"], raw.get("known_keys"))
                        health = self._health_from_legacy(raw.get("health"))
                        if health is not None:
                            self.data["watchers"].setdefault(
                                watcher["id"], {"known_keys": [], "health": _default_health()}
                            )["health"] = health
                            changed = True

            # v2 used a file name derived from the human-readable watcher name.
            legacy_path = directory / f"{_legacy_safe_name(watcher['name'])}.json"
            if legacy_path.exists():
                try:
                    raw = self._read_json(legacy_path)
                except StateError:
                    logger.warning("손상된 legacy v2 state는 이관하지 않습니다: %s", legacy_path)
                else:
                    if isinstance(raw, dict):
                        changed |= self._merge_known(watcher["id"], raw.get("keys"))

        system_path = directory / "v3_system.json"
        if system_path.exists():
            try:
                raw_system = self._read_json(system_path)
            except StateError:
                logger.warning("손상된 legacy v3 system state는 이관하지 않습니다: %s", system_path)
            else:
                if isinstance(raw_system, dict):
                    heartbeat = raw_system.get("last_heartbeat_date")
                    if isinstance(heartbeat, str):
                        try:
                            date.fromisoformat(heartbeat)
                        except ValueError:
                            pass
                        else:
                            self.data["meta"]["last_heartbeat_date"] = heartbeat
                            changed = True
        return changed

    def _migrate_legacy(self, watchers: list[dict[str, Any]]) -> bool:
        sources: list[str] = []
        changed = False
        for directory in self.legacy_state_dirs:
            if not directory.exists() or directory.resolve() == self.state_dir.resolve():
                continue
            source_changed = False
            source_changed |= self._migrate_r3_single(directory / "state.json", watchers)
            source_changed |= self._migrate_per_watcher(directory, watchers)
            if source_changed:
                changed = True
                sources.append(str(directory))
        if changed:
            self.data["meta"]["migration_sources"] = list(dict.fromkeys(sources))
            logger.info("legacy 상태 자동 이관 완료: %s", ", ".join(sources))
        return changed

    def load(self, watchers: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        loaded = self._load_primary_or_backup()
        if loaded is not None:
            self.data = loaded
            self._loaded = True
            return self.data
        self.data = _default_state()
        self._loaded = True
        if watchers and self._migrate_legacy(watchers):
            self.save()
        return self.data

    def watcher(self, watcher_id: str) -> dict[str, Any]:
        if not self._loaded:
            self.load()
        item = self.data["watchers"].setdefault(
            watcher_id, {"known_keys": [], "health": _default_health()}
        )
        # Validate the created/existing item on every access so corruption in memory
        # cannot be silently persisted later.
        validated = _validate_state_data(self.data)
        self.data = validated
        return self.data["watchers"][watcher_id]

    def meta(self) -> dict[str, Any]:
        if not self._loaded:
            self.load()
        return self.data["meta"]

    def snapshot(self) -> dict[str, Any]:
        if not self._loaded:
            self.load()
        return deepcopy(_validate_state_data(self.data))

    def save(self) -> None:
        if not self._loaded:
            self.load()
        current = _validate_state_data(self.data)
        self.data = current

        if self.path.exists():
            try:
                previous = self._read_valid(self.path)
            except StateError:
                # Never overwrite a known-good backup with corrupt primary bytes.
                logger.error("저장 직전 주 state 손상 감지 - 기존 백업은 보존합니다.")
            else:
                self._atomic_write_data(self.backup_path, previous)
        self._atomic_write_data(self.path, current)

    def probe_writable(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        probe: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.state_dir,
                prefix=".probe-",
                delete=False,
            ) as handle:
                probe = Path(handle.name)
                handle.write("ok\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            raise StateError(f"상태 디렉터리에 쓸 수 없습니다: {self.state_dir}") from exc
        finally:
            if probe is not None:
                try:
                    probe.unlink(missing_ok=True)
                except OSError:
                    pass
