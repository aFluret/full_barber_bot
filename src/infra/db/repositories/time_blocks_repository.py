"""
/**
 * @file: time_blocks_repository.py
 * @description: Репозиторий ручных блокировок времени (time_blocks)
 * @dependencies: infra.db.supabase_client, asyncio, datetime
 * @created: 2026-05-05
 */
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime, time
from typing import Dict, List, Optional, Tuple

from src.infra.db.supabase_client import get_supabase_client


class TimeBlocksRepository:
    @staticmethod
    def _parse_supabase_time(raw: object) -> time:
        if raw is None:
            raise ValueError("Supabase time is null")
        s = str(raw).strip()
        if len(s) >= 5:
            s = s[:5]
        return time.fromisoformat(s)

    async def list_blocks_for_date(
        self,
        *,
        target_date: date,
        master_key: Optional[str] = None,
    ) -> List[Tuple[time, time]]:
        def _op() -> List[Tuple[time, time]]:
            client = get_supabase_client()
            try:
                query = (
                    client.table("time_blocks")
                    .select("start_time,end_time")
                    .eq("date", target_date.isoformat())
                )
                if master_key:
                    query = query.eq("master_key", master_key)
                try:
                    query = query.eq("is_active", True)
                except Exception:
                    pass
                res = query.execute()
            except Exception:
                return []

            out: List[Tuple[time, time]] = []
            for row in res.data or []:
                try:
                    out.append(
                        (
                            self._parse_supabase_time(row.get("start_time")),
                            self._parse_supabase_time(row.get("end_time")),
                        )
                    )
                except Exception:
                    continue
            return out

        return await asyncio.to_thread(_op)

    async def list_blocks_range(
        self,
        *,
        start_date: date,
        end_date: date,
        master_key: Optional[str] = None,
    ) -> Dict[date, List[Tuple[time, time]]]:
        def _op() -> Dict[date, List[Tuple[time, time]]]:
            client = get_supabase_client()
            try:
                query = (
                    client.table("time_blocks")
                    .select("date,start_time,end_time")
                    .gte("date", start_date.isoformat())
                    .lte("date", end_date.isoformat())
                )
                if master_key:
                    query = query.eq("master_key", master_key)
                try:
                    query = query.eq("is_active", True)
                except Exception:
                    pass
                res = query.execute()
            except Exception:
                return {}

            out: Dict[date, List[Tuple[time, time]]] = defaultdict(list)
            for row in res.data or []:
                try:
                    d = date.fromisoformat(str(row.get("date")))
                    out[d].append(
                        (
                            self._parse_supabase_time(row.get("start_time")),
                            self._parse_supabase_time(row.get("end_time")),
                        )
                    )
                except Exception:
                    continue
            return dict(out)

        return await asyncio.to_thread(_op)
