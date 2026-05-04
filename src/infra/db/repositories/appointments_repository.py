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
from collections import defaultdict
from datetime import date, datetime, time
from typing import Dict, List, Optional, Tuple

from src.infra.db.models import AppointmentModel
from src.infra.db.supabase_client import get_supabase_client


class SlotUnavailableError(Exception):
    pass


class AppointmentsRepository:
    @staticmethod
    def _parse_supabase_time(raw: object) -> time:
        """
        Supabase time обычно возвращает строку вроде 'HH:MM:SS' или 'HH:MM'.
        """
        if raw is None:
            raise ValueError("Supabase time is null")
        s = str(raw).strip()
        # Нам нужны первые HH:MM (timezone/секунды не важны для логики слотов).
        if len(s) >= 5:
            s = s[:5]
        return time.fromisoformat(s)

    @staticmethod
    def _time_to_supabase(raw: time) -> str:
        # Supabase ожидает format 'HH:MM:SS' для полей типа time.
        return raw.strftime("%H:%M:%S")

    @staticmethod
    def _intervals_overlap(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
        # Half-open intervals: [start, end). Границы не считаем пересечением.
        return a_start < b_end and a_end > b_start

    async def get_active_for_user(self, user_id: int) -> Optional[AppointmentModel]:
        def _op() -> Optional[dict]:
            client = get_supabase_client()
            res = (
                client.table("appointments")
                .select("id,user_id,date,service_id,start_time,end_time,status,created_at")
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

        start_t = self._parse_supabase_time(row["start_time"])
        end_t = self._parse_supabase_time(row["end_time"])

        return AppointmentModel(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            date=date.fromisoformat(str(row["date"])),
            service_id=int(row["service_id"]),
            start_time=start_t,
            end_time=end_t,
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

    async def cancel_confirmed_by_id(self, appointment_id: int) -> Optional[AppointmentModel]:
        """
        Админская отмена конкретной записи по appointment_id.
        """
        existing = await self.get_by_id(appointment_id)
        if existing is None or existing.status != "confirmed":
            return None

        def _op() -> None:
            client = get_supabase_client()
            client.table("appointments").update({"status": "cancelled"}).eq("id", appointment_id).eq(
                "status", "confirmed"
            ).execute()

        await asyncio.to_thread(_op)
        return existing

    async def list_confirmed_intervals(self, target_date: date) -> List[Tuple[time, time]]:
        def _op() -> List[Tuple[time, time]]:
            client = get_supabase_client()
            res = (
                client.table("appointments")
                .select("start_time,end_time")
                .eq("date", target_date.isoformat())
                .eq("status", "confirmed")
                .execute()
            )
            out: List[Tuple[time, time]] = []
            for row in res.data or []:
                out.append(
                    (
                        self._parse_supabase_time(row["start_time"]),
                        self._parse_supabase_time(row["end_time"]),
                    )
                )
            return out

        return await asyncio.to_thread(_op)

    async def list_confirmed_intervals_range(
        self,
        start_date: date,
        end_date: date,
    ) -> Dict[date, List[Tuple[time, time]]]:
        """
        Все подтверждённые интервалы за диапазон дат [start_date, end_date] одним запросом.
        """

        def _op() -> Dict[date, List[Tuple[time, time]]]:
            client = get_supabase_client()
            res = (
                client.table("appointments")
                .select("date,start_time,end_time")
                .eq("status", "confirmed")
                .gte("date", start_date.isoformat())
                .lte("date", end_date.isoformat())
                .execute()
            )
            out: Dict[date, List[Tuple[time, time]]] = defaultdict(list)
            for row in res.data or []:
                raw_d = row.get("date")
                if raw_d is None:
                    continue
                d = date.fromisoformat(str(raw_d))
                out[d].append(
                    (
                        self._parse_supabase_time(row["start_time"]),
                        self._parse_supabase_time(row["end_time"]),
                    )
                )
            return dict(out)

        return await asyncio.to_thread(_op)

    async def create_confirmed(
        self,
        user_id: int,
        target_date: date,
        service_id: int,
        start_time: time,
        end_time: time,
    ) -> AppointmentModel:
        # Приложение делает fast-check по confirmed interval'ам,
        # а БД обеспечивает основную защиту от гонок.
        occupied = await self.list_confirmed_intervals(target_date)
        for o_start, o_end in occupied:
            if self._intervals_overlap(start_time, end_time, o_start, o_end):
                raise SlotUnavailableError("Интервал пересекается с существующей записью")

        def _op() -> dict:
            client = get_supabase_client()
            payload = {
                "user_id": user_id,
                "date": target_date.isoformat(),
                "service_id": service_id,
                "start_time": self._time_to_supabase(start_time),
                "end_time": self._time_to_supabase(end_time),
                "status": "confirmed",
            }

            # В supabase-py 2.x нельзя использовать `.select()` после `.insert()`.
            # Поэтому: вставляем, затем отдельным запросом забираем запись того же слота.
            client.table("appointments").insert(payload).execute()

            res2 = (
                client.table("appointments")
                .select("id,user_id,date,service_id,start_time,end_time,status,created_at")
                .eq("user_id", user_id)
                .eq("date", target_date.isoformat())
                .eq("service_id", service_id)
                .eq("start_time", self._time_to_supabase(start_time))
                .eq("end_time", self._time_to_supabase(end_time))
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
            # В Supabase/PG exclusion constraint при конкурентной записи может вызвать exception.
            msg = str(e).lower()
            if (
                "exclusion" in msg
                or "duplicate key" in msg
                or "unique constraint" in msg
                or "23505" in msg
            ):
                raise SlotUnavailableError("Интервал пересекается с существующей записью") from e
            raise

        created_at = row.get("created_at")
        if isinstance(created_at, str):
            created_at_dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        else:
            created_at_dt = created_at

        start_t = self._parse_supabase_time(row["start_time"])
        end_t = self._parse_supabase_time(row["end_time"])

        return AppointmentModel(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            date=date.fromisoformat(str(row["date"])),
            service_id=int(row["service_id"]),
            start_time=start_t,
            end_time=end_t,
            status=str(row["status"]),
            created_at=created_at_dt,
        )

    async def list_by_date_from_today(self, target_date: date) -> List[AppointmentModel]:
        # used for /today and /tomorrow: exact date filter
        def _op() -> List[dict]:
            client = get_supabase_client()
            res = (
                client.table("appointments")
                .select("id,user_id,date,service_id,start_time,end_time,status,created_at")
                .eq("date", target_date.isoformat())
                .eq("status", "confirmed")
                .order("start_time")
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

            start_t = self._parse_supabase_time(row["start_time"])
            end_t = self._parse_supabase_time(row["end_time"])

            out.append(
                AppointmentModel(
                    id=int(row["id"]),
                    user_id=int(row["user_id"]),
                    date=date.fromisoformat(str(row["date"])),
                    service_id=int(row["service_id"]),
                    start_time=start_t,
                    end_time=end_t,
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
                .select("id,user_id,date,service_id,start_time,end_time,status,created_at")
                .gte("date", target_date.isoformat())
                .eq("status", "confirmed")
                .order("date")
                .order("start_time")
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

            start_t = self._parse_supabase_time(row["start_time"])
            end_t = self._parse_supabase_time(row["end_time"])

            out.append(
                AppointmentModel(
                    id=int(row["id"]),
                    user_id=int(row["user_id"]),
                    date=date.fromisoformat(str(row["date"])),
                    service_id=int(row["service_id"]),
                    start_time=start_t,
                    end_time=end_t,
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
                .select("id,user_id,date,service_id,start_time,end_time,status,created_at")
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

        start_t = self._parse_supabase_time(row["start_time"])
        end_t = self._parse_supabase_time(row["end_time"])

        return AppointmentModel(
            id=int(row["id"]),
            user_id=int(row["user_id"]),
            date=date.fromisoformat(str(row["date"])),
            service_id=int(row["service_id"]),
            start_time=start_t,
            end_time=end_t,
            status=str(row["status"]),
            created_at=created_at_dt,
        )

    async def list_for_user(self, user_id: int, limit: int = 20) -> List[AppointmentModel]:
        def _op() -> List[dict]:
            client = get_supabase_client()
            res = (
                client.table("appointments")
                .select("id,user_id,date,service_id,start_time,end_time,status,created_at")
                .eq("user_id", user_id)
                .order("date", desc=True)
                .order("start_time", desc=True)
                .limit(limit)
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
            start_t = self._parse_supabase_time(row["start_time"])
            end_t = self._parse_supabase_time(row["end_time"])
            out.append(
                AppointmentModel(
                    id=int(row["id"]),
                    user_id=int(row["user_id"]),
                    date=date.fromisoformat(str(row["date"])),
                    service_id=int(row["service_id"]),
                    start_time=start_t,
                    end_time=end_t,
                    status=str(row["status"]),
                    created_at=created_at_dt,
                )
            )
        return out

    async def complete_ended_confirmed_appointments(self, now_local: datetime) -> List[int]:
        """
        Автозавершение: если confirmed-запись уже закончилась, ставим status='completed'.
        Возвращает список appointment_id, для которых нужно отменить будущие reminder_jobs.
        """

        today = now_local.date()
        now_time = now_local.time()
        today_iso = today.isoformat()
        now_time_supabase = self._time_to_supabase(now_time)

        def _op() -> List[int]:
            client = get_supabase_client()
            ids: set[int] = set()

            # Записи за прошлые дни (точно закончились).
            rows_prev = (
                client.table("appointments")
                .select("id")
                .eq("status", "confirmed")
                .lt("date", today_iso)
                .execute()
            )
            for r in rows_prev.data or []:
                ids.add(int(r["id"]))

            # Записи на сегодня, которые уже закончились.
            rows_today = (
                client.table("appointments")
                .select("id")
                .eq("status", "confirmed")
                .eq("date", today_iso)
                .lte("end_time", now_time_supabase)
                .execute()
            )
            for r in rows_today.data or []:
                ids.add(int(r["id"]))

            if not ids:
                return []

            # Обновляем status по каждому id (простота/надежность для MVP).
            for appointment_id in ids:
                client.table("appointments").update({"status": "completed"}).eq("id", appointment_id).execute()

            return sorted(ids)

        return await asyncio.to_thread(_op)

