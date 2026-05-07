"""
Разрешение Telegram chat_id мастера для уведомлений: БД, затем env fallback.
"""

from __future__ import annotations

from src.infra.config.settings import Settings, get_settings
from src.infra.db.repositories.masters_repository import MastersRepository


def parse_master_notify_map(raw: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for chunk in (raw or "").split(","):
        pair = chunk.strip()
        if not pair:
            continue
        sep = ":" if ":" in pair else ("=" if "=" in pair else None)
        if sep is None:
            continue
        key_raw, user_id_raw = pair.split(sep, 1)
        key = key_raw.strip()
        if not key:
            continue
        try:
            out[key] = int(user_id_raw.strip())
        except ValueError:
            continue
    return out


async def resolve_master_notify_chat_id(
    master_key: str | None,
    *,
    masters_repo: MastersRepository,
    settings: Settings | None = None,
) -> int | None:
    key = (master_key or "").strip()
    if not key:
        return None
    master = await masters_repo.get_by_key(key)
    if master is not None and master.telegram_user_id is not None:
        return int(master.telegram_user_id)
    cfg = settings or get_settings()
    return parse_master_notify_map(cfg.master_telegram_map).get(key)
