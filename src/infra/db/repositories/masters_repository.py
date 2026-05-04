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


class MastersRepository:
    @staticmethod
    def _parse_time(raw: object, default_hhmm: str) -> time:
        s = str(raw)[:5] if raw is not None else default_hhmm
        return datetime.strptime(s, "%H:%M").time()

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
                    is_active=True,
                )
            )
        return out

    async def list_active(self, branch_id: Optional[int] = None) -> List[MasterModel]:
        def _op() -> List[MasterModel]:
            client = get_supabase_client()
            try:
                query = (
                    client.table("masters")
                    .select("id,master_key,name,work_start,work_end,is_active")
                    .eq("is_active", True)
                )
                if branch_id is not None:
                    # Получаем id мастеров для филиала.
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
            out: List[MasterModel] = []
            for row in rows:
                out.append(
                    MasterModel(
                        id=int(row["id"]),
                        master_key=str(row.get("master_key") or f"m{int(row['id'])}"),
                        name=str(row.get("name") or ""),
                        work_start=self._parse_time(row.get("work_start"), "10:00"),
                        work_end=self._parse_time(row.get("work_end"), "18:00"),
                        is_active=bool(row.get("is_active", True)),
                    )
                )
            return out

        return await asyncio.to_thread(_op)

    async def list_all(self, branch_id: Optional[int] = None) -> List[MasterModel]:
        def _op() -> List[MasterModel]:
            client = get_supabase_client()
            try:
                query = client.table("masters").select(
                    "id,master_key,name,work_start,work_end,is_active"
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
            out: List[MasterModel] = []
            for row in rows:
                out.append(
                    MasterModel(
                        id=int(row["id"]),
                        master_key=str(row.get("master_key") or f"m{int(row['id'])}"),
                        name=str(row.get("name") or ""),
                        work_start=self._parse_time(row.get("work_start"), "10:00"),
                        work_end=self._parse_time(row.get("work_end"), "18:00"),
                        is_active=bool(row.get("is_active", True)),
                    )
                )
            return out

        return await asyncio.to_thread(_op)

    async def get_by_key(self, master_key: str) -> Optional[MasterModel]:
        all_items = await self.list_all()
        for item in all_items:
            if item.master_key == master_key:
                return item
        return None

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
