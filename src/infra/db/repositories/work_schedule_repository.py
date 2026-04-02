"""
/**
 * @file: work_schedule_repository.py
 * @description: Репозиторий рабочего графика для Supabase
 * @dependencies: infra.db.supabase_client, asyncio, datetime
 * @created: 2026-03-24
 */
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, time
from typing import List, Optional, Set

from src.infra.db.supabase_client import get_supabase_client


@dataclass(frozen=True)
class WorkScheduleModel:
    weekdays: Set[int]  # python weekday: Пн=0 ... Вс=6
    start_time: time
    end_time: time
    lunch_time: Optional[time] = None


class WorkScheduleRepository:
    async def get_latest(self) -> Optional[WorkScheduleModel]:
        def _op() -> Optional[dict]:
            client = get_supabase_client()
            # Новая схема хранит lunch_time, но для обратной совместимости
            # делаем fallback к старому select, если колонки еще не добавлены.
            try:
                res = (
                    client.table("work_schedule")
                    .select("weekdays,start_time,end_time,lunch_time,lunch_start,created_at")
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
            except Exception:
                res = (
                    client.table("work_schedule")
                    .select("weekdays,start_time,end_time,created_at")
                    .order("created_at", desc=True)
                    .limit(1)
                    .execute()
                )
            return res.data[0] if res.data else None

        row = await asyncio.to_thread(_op)
        if not row:
            return None

        weekdays_raw = row.get("weekdays") or []
        weekdays: Set[int] = {int(x) for x in weekdays_raw}

        start_time_raw = row.get("start_time")
        end_time_raw = row.get("end_time")
        lunch_raw = row.get("lunch_time")
        if lunch_raw is None:
            lunch_raw = row.get("lunch_start")

        # Supabase time может приходить как строка "HH:MM:SS" или "HH:MM".
        start_s = str(start_time_raw)[:5]
        end_s = str(end_time_raw)[:5]
        start_t = datetime.strptime(start_s, "%H:%M").time()
        end_t = datetime.strptime(end_s, "%H:%M").time()
        lunch_t = datetime.strptime(str(lunch_raw)[:5], "%H:%M").time() if lunch_raw is not None else None

        return WorkScheduleModel(
            weekdays=weekdays,
            start_time=start_t,
            end_time=end_t,
            lunch_time=lunch_t,
        )

    async def set_schedule(
        self,
        weekdays: List[int],
        start_time: time,
        end_time: time,
        lunch_time: Optional[time] = None,
    ) -> None:
        payload = {
            "weekdays": list(weekdays),
            "start_time": start_time.strftime("%H:%M:%S"),
            "end_time": end_time.strftime("%H:%M:%S"),
        }
        payload_with_lunch = dict(payload)
        payload_with_lunch["lunch_time"] = lunch_time.strftime("%H:%M:%S") if lunch_time else None

        def _op() -> None:
            client = get_supabase_client()
            # Supabase/PostgREST требует WHERE для DELETE.
            # Обновляем последнюю запись, если она есть; иначе вставляем новую.
            latest = (
                client.table("work_schedule")
                .select("id")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            row = (latest.data or [None])[0]
            if row and row.get("id") is not None:
                try:
                    client.table("work_schedule").update(payload_with_lunch).eq("id", int(row["id"])).execute()
                except Exception:
                    client.table("work_schedule").update(payload).eq("id", int(row["id"])).execute()
            else:
                try:
                    client.table("work_schedule").insert(payload_with_lunch).execute()
                except Exception:
                    client.table("work_schedule").insert(payload).execute()

        await asyncio.to_thread(_op)

