"""
Репозиторий одноразовых приглашений мастеров.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


from src.infra.db.supabase_client import get_supabase_client


class MasterInvitesRepository:
    async def create_invite(
        self,
        *,
        token: str,
        created_by_user_id: int,
        hint_name: str | None,
        ttl_minutes: int = 15,
    ) -> None:
        expires_at = datetime.now(timezone.utc) + timedelta(minutes=int(ttl_minutes))

        def _op() -> None:
            client = get_supabase_client()
            client.table("master_invites").insert(
                {
                    "token": token,
                    "hint_name": (hint_name or "").strip() or None,
                    "expires_at": expires_at.isoformat(),
                    "created_by_user_id": int(created_by_user_id),
                }
            ).execute()

        await asyncio.to_thread(_op)

    async def get_by_token(self, token: str) -> Optional[dict[str, Any]]:
        def _op() -> Optional[dict]:
            client = get_supabase_client()
            try:
                res = (
                    client.table("master_invites")
                    .select("id,token,hint_name,expires_at,used_at,used_by_user_id,master_id,created_by_user_id")
                    .eq("token", token.strip())
                    .limit(1)
                    .execute()
                )
            except Exception:
                return None
            if not res.data:
                return None
            return res.data[0]

        return await asyncio.to_thread(_op)

    async def mark_used(self, invite_id: int, used_by_user_id: int, master_id: int) -> None:
        def _op() -> None:
            client = get_supabase_client()
            now = datetime.now(timezone.utc).isoformat()
            client.table("master_invites").update(
                {
                    "used_at": now,
                    "used_by_user_id": int(used_by_user_id),
                    "master_id": int(master_id),
                }
            ).eq("id", int(invite_id)).execute()

        await asyncio.to_thread(_op)
