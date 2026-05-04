"""
/**
 * @file: branches_repository.py
 * @description: Репозиторий филиалов (branches) с fallback на settings
 * @dependencies: infra.db.models, infra.db.supabase_client, infra.config.settings
 * @created: 2026-05-04
 */
"""

from __future__ import annotations

import asyncio
from typing import List

from src.infra.config.settings import get_settings
from src.infra.db.models import BranchModel
from src.infra.db.supabase_client import get_supabase_client


class BranchesRepository:
    async def list_active(self) -> List[BranchModel]:
        def _fallback() -> List[BranchModel]:
            settings = get_settings()
            raw = [x.strip() for x in (settings.branches_csv or "").split(",") if x.strip()]
            names = raw or ["Основной филиал"]
            return [
                BranchModel(
                    id=idx + 1,
                    name=name,
                    address=name,
                    is_active=True,
                )
                for idx, name in enumerate(names)
            ]

        def _op() -> List[BranchModel]:
            client = get_supabase_client()
            try:
                res = (
                    client.table("branches")
                    .select("id,name,address,is_active")
                    .eq("is_active", True)
                    .order("id")
                    .execute()
                )
            except Exception:
                return _fallback()

            out: List[BranchModel] = []
            rows = list(res.data or [])
            if not rows:
                return _fallback()
            for row in rows:
                out.append(
                    BranchModel(
                        id=int(row["id"]),
                        name=str(row.get("name") or ""),
                        address=str(row.get("address") or row.get("name") or ""),
                        is_active=bool(row.get("is_active", True)),
                    )
                )
            return out

        return await asyncio.to_thread(_op)

    async def list_all(self) -> List[BranchModel]:
        def _fallback() -> List[BranchModel]:
            settings = get_settings()
            raw = [x.strip() for x in (settings.branches_csv or "").split(",") if x.strip()]
            names = raw or ["Основной филиал"]
            return [
                BranchModel(
                    id=idx + 1,
                    name=name,
                    address=name,
                    is_active=True,
                )
                for idx, name in enumerate(names)
            ]

        def _op() -> List[BranchModel]:
            client = get_supabase_client()
            try:
                res = client.table("branches").select("id,name,address,is_active").order("id").execute()
            except Exception:
                return _fallback()

            rows = list(res.data or [])
            if not rows:
                return _fallback()
            out: List[BranchModel] = []
            for row in rows:
                out.append(
                    BranchModel(
                        id=int(row["id"]),
                        name=str(row.get("name") or ""),
                        address=str(row.get("address") or row.get("name") or ""),
                        is_active=bool(row.get("is_active", True)),
                    )
                )
            return out

        return await asyncio.to_thread(_op)

    async def set_active(self, branch_id: int, is_active: bool) -> bool:
        def _op() -> bool:
            client = get_supabase_client()
            try:
                res = (
                    client.table("branches")
                    .update({"is_active": bool(is_active)})
                    .eq("id", int(branch_id))
                    .execute()
                )
                return bool(res.data)
            except Exception:
                return False

        return await asyncio.to_thread(_op)
