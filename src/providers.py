"""Provider routing and provider-safe showtime identity for movie booking notifier v5."""
from __future__ import annotations

from datetime import date
from typing import Any

from .cgv import CGVClient
from .megabox import MegaboxClient


def showtime_key(item: dict[str, Any]) -> str:
    """Stable cross-provider schedule identity.

    Megabox exposes a stable ``playSchdlNo``.  CGV currently does not expose a
    similarly dependable ID in the fields used by this notifier, so CGV falls
    back to the historical date/movie/hall/start identity.  Provider is always
    part of v5-generated keys to prevent cross-chain collisions.

    Items produced by older tests/migrations without ``provider`` deliberately
    retain the old v4 key format for backward compatibility.
    """
    provider = str(item.get("provider") or "").strip().lower()
    schedule_id = str(item.get("schedule_id") or "").strip()
    if provider and schedule_id:
        return f"{provider}|{item['date_raw']}|{schedule_id}"
    legacy = f"{item['date_raw']}|{item['movie']}|{item['hall']}|{item['start']}"
    return f"{provider}|{legacy}" if provider else legacy


class ProviderRouter:
    """Dispatch a watcher to its cinema-chain client while sharing monitor logic."""

    def __init__(
        self,
        config: dict[str, Any],
        *,
        cgv: CGVClient | None = None,
        megabox: MegaboxClient | None = None,
    ) -> None:
        self.cgv = cgv or CGVClient(config)
        self.megabox = megabox or MegaboxClient(config)

    def client_for(self, watcher: dict[str, Any]):
        provider = str(watcher.get("provider", "cgv")).lower()
        if provider == "cgv":
            return self.cgv
        if provider == "megabox":
            return self.megabox
        raise ValueError(f"지원하지 않는 provider: {provider}")

    def start_cycle(self) -> None:
        self.cgv.start_cycle()
        self.megabox.start_cycle()

    def close(self) -> None:
        self.cgv.close()
        self.megabox.close()

    def active_dates(self, watcher: dict[str, Any], today: date | None = None) -> list[str]:
        return self.client_for(watcher).active_dates(watcher, today=today)

    def scan_watcher(self, watcher: dict[str, Any], today: date | None = None) -> dict[str, Any]:
        return self.client_for(watcher).scan_watcher(watcher, today=today)
