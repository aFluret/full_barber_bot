"""
Принятие одноразового приглашения мастера: новая строка в masters + role=master.
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import Optional, Tuple

from src.infra.auth.roles import ROLE_MASTER
from src.infra.db.models import MasterModel
from src.infra.db.repositories.master_invites_repository import MasterInvitesRepository
from src.infra.db.repositories.masters_repository import MastersRepository
from src.infra.db.repositories.users_repository import UsersRepository


class MasterInviteService:
    def __init__(self) -> None:
        self._invites = MasterInvitesRepository()
        self._masters = MastersRepository()
        self._users = UsersRepository()

    @staticmethod
    def generate_token() -> str:
        return secrets.token_hex(16)

    @staticmethod
    def deep_link_payload(token: str) -> str:
        return f"mi_{token}"

    @staticmethod
    def parse_payload(args: str | None) -> str | None:
        raw = (args or "").strip()
        if raw.startswith("mi_") and len(raw) > 3:
            return raw[3:].strip()
        return None

    async def create_invite_link(
        self,
        *,
        admin_user_id: int,
        bot_username: str,
        hint_name: str | None = None,
        ttl_minutes: int = 15,
    ) -> tuple[str, str]:
        token = self.generate_token()
        await self._invites.create_invite(
            token=token,
            created_by_user_id=admin_user_id,
            hint_name=hint_name,
            ttl_minutes=ttl_minutes,
        )
        payload = self.deep_link_payload(token)
        user = bot_username.strip().lstrip("@")
        url = f"https://t.me/{user}?start={payload}"
        return token, url

    @staticmethod
    def _master_display_name(user_name: str, user_id: int) -> str:
        base = (user_name or "").strip() or "Мастер"
        return f"{base} · tg{user_id}"

    @staticmethod
    def _master_key_from_user(user_name: str, user_id: int, token: str) -> str:
        slug = re.sub(r"[^a-z0-9]+", "_", (user_name or "master").lower()).strip("_")[:20] or "master"
        suffix = token[:8]
        return f"{slug}_{user_id}_{suffix}"[:64]

    def _invite_still_valid(self, row: dict) -> bool:
        if row.get("used_at"):
            return False
        raw_exp = row.get("expires_at")
        if raw_exp is None:
            return False
        if isinstance(raw_exp, str):
            exp = datetime.fromisoformat(raw_exp.replace("Z", "+00:00"))
        else:
            exp = raw_exp
        return exp > datetime.now(timezone.utc)

    async def is_token_valid(self, token: str) -> bool:
        row = await self._invites.get_by_token(token)
        if row is None:
            return False
        return self._invite_still_valid(row)

    async def redeem(self, token: str, user_id: int) -> Tuple[bool, str, Optional[MasterModel]]:
        row = await self._invites.get_by_token(token)
        if row is None:
            return False, "not_found", None
        if not self._invite_still_valid(row):
            return False, "expired_or_used", None

        user = await self._users.get_by_user_id(user_id)
        if user is None:
            return False, "not_registered", None

        existing_master = await self._masters.get_by_telegram_user_id(user_id)
        if existing_master is not None:
            return False, "already_master", None

        invite_id = int(row["id"])
        key = self._master_key_from_user(user.name, user_id, token)
        display = self._master_display_name(user.name, user_id)
        master = await self._masters.insert_master(
            master_key=key,
            name=display,
            telegram_user_id=user_id,
        )
        if master is None:
            return False, "db_error", None

        await self._users.set_role(user_id, ROLE_MASTER)
        await self._invites.mark_used(invite_id, user_id, master.id)
        return True, "ok", master
