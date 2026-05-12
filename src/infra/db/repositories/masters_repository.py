"""
/**
 * @file: masters_repository.py
 * @description: Репозиторий мастеров и их графиков с fallback на settings
 * @dependencies: infra.db.models, infra.db.supabase_client, infra.config.settings
 * @created: 2026-05-04
 */
"""

from __future__ import annotations

import asyncio
from datetime import datetime, time
from typing import List, Optional

from src.infra.config.settings import get_settings
from src.infra.db.models import MasterModel
from src.infra.db.supabase_client import get_supabase_client


_SELECT = "id,master_key,name,work_start,work_end,lunch_time,is_active,telegram_user_id"


class MastersRepository:
    @staticmethod
    def _parse_time(raw: object, default_hhmm: str) -> time:
        s = str(raw)[:5] if raw is not None else default_hhmm
        return datetime.strptime(s, "%H:%M").time()

    @classmethod
    def _row_to_model(cls, row: dict) -> MasterModel:
        tid = row.get("telegram_user_id")
        telegram_user_id = int(tid) if tid is not None else None
        lunch_raw = row.get("lunch_time")
        lunch_t: time | None = None
        if lunch_raw is not None and str(lunch_raw).strip():
            lunch_t = cls._parse_time(lunch_raw, "12:00")
        return MasterModel(
            id=int(row["id"]),
            master_key=str(row.get("master_key") or f"m{int(row['id'])}"),
            name=str(row.get("name") or ""),
            work_start=cls._parse_time(row.get("work_start"), "10:00"),
            work_end=cls._parse_time(row.get("work_end"), "18:00"),
            lunch_time=lunch_t,
            is_active=bool(row.get("is_active", True)),
            telegram_user_id=telegram_user_id,
        )

    def _fallback(self) -> List[MasterModel]:
        settings = get_settings()
        names = [x.strip() for x in (settings.masters_csv or "").split(",") if x.strip()] or ["Илья"]
        out: List[MasterModel] = []
        for idx, name in enumerate(names):
            out.append(
                MasterModel(
                    id=idx + 1,
                    master_key=f"m{idx + 1}",
                    name=name,
                    work_start=time(10, 0),
                    work_end=time(18, 0),
                    lunch_time=None,
                    is_active=True,
                    telegram_user_id=None,
                )
            )
        return out

    async def get_by_key(self, master_key: str) -> Optional[MasterModel]:
        key = (master_key or "").strip()
        if not key:
            return None

        def _op() -> Optional[dict]:
            client = get_supabase_client()
            try:
                res = (
                    client.table("masters")
                    .select(_SELECT)
                    .eq("master_key", key)
                    .limit(1)
                    .execute()
                )
            except Exception:
                return None
            return res.data[0] if res.data else None

        row = await asyncio.to_thread(_op)
        if not row:
            return None
        return self._row_to_model(row)

    async def get_by_telegram_user_id(self, telegram_user_id: int) -> Optional[MasterModel]:
        def _op() -> Optional[dict]:
            client = get_supabase_client()
            try:
                res = (
                    client.table("masters")
                    .select(_SELECT)
                    .eq("telegram_user_id", int(telegram_user_id))
                    .limit(1)
                    .execute()
                )
            except Exception:
                return None
            return res.data[0] if res.data else None

        row = await asyncio.to_thread(_op)
        if not row:
            return None
        return self._row_to_model(row)

    async def set_telegram_for_master_key(self, master_key: str, telegram_user_id: int | None) -> bool:
        key = (master_key or "").strip()
        if not key:
            return False

        def _op() -> bool:
            client = get_supabase_client()
            try:
                if telegram_user_id is not None:
                    client.table("masters").update({"telegram_user_id": None}).eq(
                        "telegram_user_id", int(telegram_user_id)
                    ).execute()
                res = (
                    client.table("masters")
                    .update({"telegram_user_id": telegram_user_id})
                    .eq("master_key", key)
                    .execute()
                )
                return bool(res.data)
            except Exception:
                return False

        return await asyncio.to_thread(_op)

    async def insert_master(
        self,
        *,
        master_key: str,
        name: str,
        telegram_user_id: int,
        work_start: time | None = None,
        work_end: time | None = None,
    ) -> Optional[MasterModel]:
        ws = work_start or time(10, 0)
        we = work_end or time(18, 0)
        key = (master_key or "").strip()
        if not key:
            return None

        def _op() -> Optional[dict]:
            client = get_supabase_client()
            try:
                client.table("masters").update({"telegram_user_id": None}).eq(
                    "telegram_user_id", int(telegram_user_id)
                ).execute()
                res = client.table("masters").insert(
                    {
                        "master_key": key,
                        "name": name.strip() or key,
                        "work_start": ws.strftime("%H:%M:%S"),
                        "work_end": we.strftime("%H:%M:%S"),
                        "is_active": True,
                        "telegram_user_id": int(telegram_user_id),
                    }
                ).execute()
                row = (res.data or [None])[0]
                return row
            except Exception:
                return None

        row = await asyncio.to_thread(_op)
        if not row:
            return None
        return self._row_to_model(row)

    async def list_active(self, branch_id: Optional[int] = None) -> List[MasterModel]:
        def _op() -> List[MasterModel]:
            client = get_supabase_client()
            try:
                query = (
                    client.table("masters")
                    .select(_SELECT)
                    .eq("is_active", True)
                )
                if branch_id is not None:
                    rel = (
                        client.table("master_branches")
                        .select("master_id")
                        .eq("branch_id", int(branch_id))
                        .execute()
                    )
                    ids = [int(r["master_id"]) for r in (rel.data or []) if r.get("master_id") is not None]
                    if not ids:
                        return []
                    query = query.in_("id", ids)

                res = query.order("id").execute()
            except Exception:
                return self._fallback()

            rows = list(res.data or [])
            if not rows:
                return self._fallback() if branch_id is None else []
            return [self._row_to_model(row) for row in rows]

        return await asyncio.to_thread(_op)

    async def list_all(self, branch_id: Optional[int] = None) -> List[MasterModel]:
        def _op() -> List[MasterModel]:
            client = get_supabase_client()
            try:
                query = client.table("masters").select(_SELECT)
                if branch_id is not None:
                    rel = (
                        client.table("master_branches")
                        .select("master_id")
                        .eq("branch_id", int(branch_id))
                        .execute()
                    )
                    ids = [int(r["master_id"]) for r in (rel.data or []) if r.get("master_id") is not None]
                    if not ids:
                        return []
                    query = query.in_("id", ids)
                res = query.order("id").execute()
            except Exception:
                return self._fallback()

            rows = list(res.data or [])
            if not rows:
                return self._fallback() if branch_id is None else []
            return [self._row_to_model(row) for row in rows]

        return await asyncio.to_thread(_op)

    async def set_active(self, master_key: str, is_active: bool) -> bool:
        def _op() -> bool:
            client = get_supabase_client()
            try:
                res = (
                    client.table("masters")
                    .update({"is_active": bool(is_active)})
                    .eq("master_key", master_key)
                    .execute()
                )
                return bool(res.data)
            except Exception:
                return False

        return await asyncio.to_thread(_op)

    async def set_work_hours(self, master_key: str, work_start: time, work_end: time) -> bool:
        """Обновить только окно работы; время обеда не меняет."""
        def _op() -> bool:
            client = get_supabase_client()
            payload = {
                "work_start": work_start.strftime("%H:%M:%S"),
                "work_end": work_end.strftime("%H:%M:%S"),
            }
            try:
                res = client.table("masters").update(payload).eq("master_key", master_key).execute()
                return bool(res.data)
            except Exception:
                return False

        return await asyncio.to_thread(_op)

    async def set_work_schedule(
        self,
        master_key: str,
        work_start: time,
        work_end: time,
        lunch_time: time | None,
    ) -> bool:
        """Окно работы и обед (60 мин от lunch_time); None — без обеда."""

        def _op() -> bool:
            client = get_supabase_client()
            payload = {
                "work_start": work_start.strftime("%H:%M:%S"),
                "work_end": work_end.strftime("%H:%M:%S"),
                "lunch_time": lunch_time.strftime("%H:%M:%S") if lunch_time is not None else None,
            }
            try:
                res = client.table("masters").update(payload).eq("master_key", master_key).execute()
                return bool(res.data)
            except Exception:
                return False

        return await asyncio.to_thread(_op)

    async def update_display_name(self, master_key: str, name: str) -> bool:
        key = (master_key or "").strip()
        label = (name or "").strip()
        if not key or not label:
            return False

        def _op() -> bool:
            client = get_supabase_client()
            try:
                res = (
                    client.table("masters")
                    .update({"name": label[:200]})
                    .eq("master_key", key)
                    .execute()
                )
                return bool(res.data)
            except Exception:
                return False

        return await asyncio.to_thread(_op)

    async def list_branch_ids_by_master_key(self, master_key: str) -> List[int]:
        def _op() -> List[int]:
            client = get_supabase_client()
            try:
                master = (
                    client.table("masters")
                    .select("id")
                    .eq("master_key", master_key)
                    .limit(1)
                    .execute()
                )
                row = (master.data or [None])[0]
                if not row or row.get("id") is None:
                    return []
                rel = (
                    client.table("master_branches")
                    .select("branch_id")
                    .eq("master_id", int(row["id"]))
                    .execute()
                )
            except Exception:
                return []
            out: List[int] = []
            for r in rel.data or []:
                if r.get("branch_id") is not None:
                    out.append(int(r["branch_id"]))
            return sorted(set(out))

        return await asyncio.to_thread(_op)

    async def set_branch_binding(self, master_key: str, branch_id: int, linked: bool) -> bool:
        def _op() -> bool:
            client = get_supabase_client()
            try:
                master = (
                    client.table("masters")
                    .select("id")
                    .eq("master_key", master_key)
                    .limit(1)
                    .execute()
                )
                row = (master.data or [None])[0]
                if not row or row.get("id") is None:
                    return False
                master_id = int(row["id"])
                if linked:
                    payload = {"master_id": master_id, "branch_id": int(branch_id)}
                    client.table("master_branches").upsert(payload, on_conflict="master_id,branch_id").execute()
                    return True
                client.table("master_branches").delete().eq("master_id", master_id).eq(
                    "branch_id", int(branch_id)
                ).execute()
                return True
            except Exception:
                return False

        return await asyncio.to_thread(_op)
