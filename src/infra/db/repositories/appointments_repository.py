"""
/**
 * @file: appointments_repository.py
 * @description: Репозиторий записей для Supabase
 * @dependencies: infra.db.models, infra.db.supabase_client, asyncio
 * @created: 2026-03-23
 */
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, time
from typing import List, Optional, Set

from src.infra.db.models import AppointmentModel
from src.infra.db.supabase_client import get_supabase_client


class SlotUnavailableError(Exception):
    pass


class AppointmentsRepository:
    @staticmethod
    def _normalize_time_slot(time_slot_hhmm: str) -> str:
        # PostgreSQL `time` часто принимает формат HH:MM:SS.
        hhmm = time_slot_hhmm.strip()
        if len(hhmm) == 5 and ":" in hhmm:
            return f"{hhmm}:00"
        return hhmm

    async def get_active_for_user(self, user_id: int) -> Optional[AppointmentModel]:
        def _op() -> Optional[dict]:
            client = get_supabase_client()
            res = (
                client.table("appointments")
                .select("id,user_id,date,time_slot,status,created_at")
                .eq("user_id", user_id)
                .eq("status", "confirmed")
                .limit(1)
                .execute()
            )
            return res.data[0] if res.data else None

        row = await asyncio.to_thread(_op)
        if not row:
            return None

        created_at = row.get("created_at")
        if isinstance(created_at, str):
            created_at_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            created_at_dt = created_at

        # Supabase обычно возвращает time как строку.
        slot = str(row["time_slot"])
        if len(slot) >= 5:
            slot_time = time.fromisoformat(slot[:5])
        else:
            slot_time = time.fromisoformat(slot)

        return AppointmentModel(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            date=date.fromisoformat(str(row["date"])),
            time_slot=slot_time,
            status=str(row["status"]),
            created_at=created_at_dt,
        )

    async def cancel_active_for_user(self, user_id: int) -> Optional[AppointmentModel]:
        # Изменение статуса делает слот "свободным" для логики выборки занятых.
        # В supabase-py 2.x chain `.update(...).select(...)` недоступен, поэтому:
        # 1) заранее забираем активную запись через get_active_for_user()
        # 2) обновляем статус без select
        # 3) возвращаем исходные данные активной записи
        existing = await self.get_active_for_user(user_id)
        if existing is None:
            return None

        def _op() -> None:
            client = get_supabase_client()
            client.table("appointments").update({"status": "cancelled"}).eq(
                "user_id", user_id
            ).eq("status", "confirmed").execute()

        await asyncio.to_thread(_op)
        return existing

    async def list_confirmed_time_slots(self, target_date: date) -> Set[str]:
        def _op() -> Set[str]:
            client = get_supabase_client()
            res = (
                client.table("appointments")
                .select("time_slot")
                .eq("date", target_date.isoformat())
                .eq("status", "confirmed")
                .execute()
            )
            slots: Set[str] = set()
            for row in res.data or []:
                slot = str(row["time_slot"])
                slots.add(slot[:5])
            return slots

        return await asyncio.to_thread(_op)

    async def create_confirmed(self, user_id: int, target_date: date, time_slot_hhmm: str) -> AppointmentModel:
        normalized_time = self._normalize_time_slot(time_slot_hhmm)

        # Важно: для защиты от гонок в проде нужен уникальный индекс/constraint в БД.
        # Здесь логика сделана "проверил-далее-вставил" для MVP.
        occupied = await self.list_confirmed_time_slots(target_date)
        if time_slot_hhmm[:5] in occupied:
            raise SlotUnavailableError(f"Слот {time_slot_hhmm} уже занят")

        def _op() -> dict:
            client = get_supabase_client()
            payload = {
                "user_id": user_id,
                "date": target_date.isoformat(),
                "time_slot": normalized_time,
                "status": "confirmed",
            }

            # В supabase-py 2.x нельзя использовать `.select()` после `.insert()`.
            # Поэтому: вставляем, затем отдельным запросом забираем запись того же слота.
            client.table("appointments").insert(payload).execute()

            res2 = (
                client.table("appointments")
                .select("id,user_id,date,time_slot,status,created_at")
                .eq("user_id", user_id)
                .eq("date", target_date.isoformat())
                .eq("time_slot", normalized_time)
                .eq("status", "confirmed")
                .order("created_at", desc=True)
                .limit(1)
                .execute()
            )
            if not res2.data:
                raise RuntimeError("Не удалось прочитать созданную запись appointments")
            return res2.data[0]

        try:
            row = await asyncio.to_thread(_op)
        except Exception as e:
            # В Supabase/PG при конкурентной записи уникальность `appointments_confirmed_slot_unique`
            # может вызвать exception (unique_violation / duplicate key).
            msg = str(e).lower()
            if (
                "appointments_confirmed_slot_unique" in msg
                or "duplicate key" in msg
                or "unique constraint" in msg
                or "23505" in msg  # postgres unique_violation
            ):
                raise SlotUnavailableError(f"Слот {time_slot_hhmm} уже занят") from e
            raise

        created_at = row.get("created_at")
        if isinstance(created_at, str):
            created_at_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            created_at_dt = created_at

        slot = str(row["time_slot"])
        if len(slot) >= 5:
            slot_time = time.fromisoformat(slot[:5])
        else:
            slot_time = time.fromisoformat(slot)

        return AppointmentModel(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            date=date.fromisoformat(str(row["date"])),
            time_slot=slot_time,
            status=str(row["status"]),
            created_at=created_at_dt,
        )

    async def list_by_date_from_today(self, target_date: date) -> List[AppointmentModel]:
        # used for /today and /tomorrow: exact date filter
        def _op() -> List[dict]:
            client = get_supabase_client()
            res = (
                client.table("appointments")
                .select("id,user_id,date,time_slot,status,created_at")
                .eq("date", target_date.isoformat())
                .eq("status", "confirmed")
                .order("time_slot")
                .execute()
            )
            return list(res.data or [])

        rows = await asyncio.to_thread(_op)
        out: List[AppointmentModel] = []
        for row in rows:
            created_at = row.get("created_at")
            if isinstance(created_at, str):
                created_at_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            else:
                created_at_dt = created_at

            slot = str(row["time_slot"])
            slot_time = time.fromisoformat(slot[:5]) if len(slot) >= 5 else time.fromisoformat(slot)

            out.append(
                AppointmentModel(
                    id=int(row["id"]),
                    user_id=int(row["user_id"]),
                    date=date.fromisoformat(str(row["date"])),
                    time_slot=slot_time,
                    status=str(row["status"]),
                    created_at=created_at_dt,
                )
            )
        return out

    async def list_confirmed_from_date(self, target_date: date) -> List[AppointmentModel]:
        # используется для команды /all (все будущие записи)
        def _op() -> List[dict]:
            client = get_supabase_client()
            res = (
                client.table("appointments")
                .select("id,user_id,date,time_slot,status,created_at")
                .gte("date", target_date.isoformat())
                .eq("status", "confirmed")
                .order("date")
                .order("time_slot")
                .execute()
            )
            return list(res.data or [])

        rows = await asyncio.to_thread(_op)
        out: List[AppointmentModel] = []
        for row in rows:
            created_at = row.get("created_at")
            if isinstance(created_at, str):
                created_at_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            else:
                created_at_dt = created_at

            slot = str(row["time_slot"])
            slot_time = time.fromisoformat(slot[:5]) if len(slot) >= 5 else time.fromisoformat(slot)

            out.append(
                AppointmentModel(
                    id=int(row["id"]),
                    user_id=int(row["user_id"]),
                    date=date.fromisoformat(str(row["date"])),
                    time_slot=slot_time,
                    status=str(row["status"]),
                    created_at=created_at_dt,
                )
            )
        return out

    async def get_by_id(self, appointment_id: int) -> Optional[AppointmentModel]:
        def _op() -> Optional[dict]:
            client = get_supabase_client()
            res = (
                client.table("appointments")
                .select("id,user_id,date,time_slot,status,created_at")
                .eq("id", appointment_id)
                .limit(1)
                .execute()
            )
            return res.data[0] if res.data else None

        row = await asyncio.to_thread(_op)
        if not row:
            return None

        created_at = row.get("created_at")
        if isinstance(created_at, str):
            created_at_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            created_at_dt = created_at

        slot = str(row["time_slot"])
        slot_time = time.fromisoformat(slot[:5]) if len(slot) >= 5 else time.fromisoformat(slot)

        return AppointmentModel(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            date=date.fromisoformat(str(row["date"])),
            time_slot=slot_time,
            status=str(row["status"]),
            created_at=created_at_dt,
        )

