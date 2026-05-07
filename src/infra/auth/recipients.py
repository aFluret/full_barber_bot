"""
Получатели служебных уведомлений (админы).
"""

from __future__ import annotations

from typing import Any


def parse_admin_user_ids(raw: str) -> list[int]:
    out: list[int] = []
    for chunk in (raw or "").split(","):
        value = chunk.strip()
        if not value:
            continue
        try:
            out.append(int(value))
        except ValueError:
            continue
    return out


async def gather_admin_recipient_ids(users_repo: Any, admin_user_ids_raw: str) -> list[int]:
    ids = set(parse_admin_user_ids(admin_user_ids_raw))
    admins = await users_repo.list_admins()
    for admin in admins:
        ids.add(int(admin.user_id))
    return sorted(ids)
