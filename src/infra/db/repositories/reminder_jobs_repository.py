"""
/**
 * @file: reminder_jobs_repository.py
 * @description: Репозиторий reminder_jobs для Supabase
 * @dependencies: infra.db.supabase_client, asyncio, datetime
 * @created: 2026-03-23
 */
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.infra.db.supabase_client import get_supabase_client


class ReminderJobsRepository:
    async def insert_for_appointment(
        self,
        appointment_id: int,
        user_id: int,
        remind_type: str,
        remind_at: datetime,
    ) -> None:
        # remind_at должен быть timezone-aware (timestamptz)
        remind_at_utc = remind_at.astimezone(timezone.utc)
        payload = {
            "appointment_id": appointment_id,
            "user_id": user_id,
            "remind_type": remind_type,
            "remind_at": remind_at_utc.isoformat(),
        }

        def _op() -> None:
            client = get_supabase_client()
            client.table("reminder_jobs").insert(payload).execute()

        try:
            await asyncio.to_thread(_op)
        except Exception as e:
            # Повторное планирование при повторном вызове не должно ломать UX.
            # Если уникальный индекс уже сработал — считаем это "уже запланировано".
            msg = str(e).lower()
            if "reminder_jobs_unique_per_appointment_type" in msg or "duplicate key" in msg or "23505" in msg:
                return
            raise

    async def fetch_due_unsent(
        self,
        now_utc: datetime,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        now_iso = now_utc.astimezone(timezone.utc).isoformat()

        def _op() -> List[Dict[str, Any]]:
            client = get_supabase_client()
            res = (
                client.table("reminder_jobs")
                .select("id,appointment_id,user_id,remind_type,remind_at,sent_at")
                .lte("remind_at", now_iso)
                .order("remind_at")
                .limit(limit)
                .execute()
            )
            return list(res.data or [])

        rows = await asyncio.to_thread(_op)
        # sent_at=0 нельзя фильтровать надежно без is_/null-предикатов,
        # поэтому фильтруем здесь.
        out: List[Dict[str, Any]] = []
        for r in rows:
            if r.get("sent_at") is None:
                out.append(r)
        return out

    async def mark_sent(self, reminder_job_id: int, sent_at: Optional[datetime] = None) -> None:
        sent_at_utc = (sent_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        payload = {"sent_at": sent_at_utc.isoformat()}

        def _op() -> None:
            client = get_supabase_client()
            client.table("reminder_jobs").update(payload).eq("id", reminder_job_id).execute()

        await asyncio.to_thread(_op)

    async def mark_all_unsent_for_appointment_as_sent(
        self,
        appointment_id: int,
        sent_at: Optional[datetime] = None,
    ) -> None:
        sent_at_utc = (sent_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        payload = {"sent_at": sent_at_utc.isoformat()}

        def _op() -> None:
            client = get_supabase_client()
            # Обновляем только unsent, чтобы не трогать уже отправленные jobs.
            rows = (
                client.table("reminder_jobs")
                .select("id,sent_at")
                .eq("appointment_id", appointment_id)
                .execute()
            ).data or []
            for row in rows:
                if row.get("sent_at") is None:
                    client.table("reminder_jobs").update(payload).eq("id", row["id"]).execute()

        await asyncio.to_thread(_op)

