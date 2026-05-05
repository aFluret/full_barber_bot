"""
/**
 * @file: reminder_service.py
 * @description: Заготовка сервиса напоминаний
 * @dependencies: infra.scheduler.jobs
 * @created: 2026-03-23
 */
"""

from __future__ import annotations

import re
import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from aiogram import Bot
from src.infra.config.settings import get_settings

if TYPE_CHECKING:
    from src.infra.db.models import AppointmentModel


class ReminderService:
    @staticmethod
    def _safe_format(template: str, values: dict[str, str]) -> str:
        text = (template or "").replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
        for key, value in values.items():
            text = text.replace("{" + key + "}", str(value))
        return text

    @staticmethod
    def _resolve_timezone(raw_tz: str):
        value = (raw_tz or "").strip()
        if not value:
            return ZoneInfo("Europe/Minsk")

        # Поддерживаем числовые форматы: "3", "+3", "-5", а также "UTC+3", "GMT+3".
        match = re.fullmatch(r"(?:(?:UTC|GMT)\s*)?([+-]?\d{1,2})", value, flags=re.IGNORECASE)
        if match:
            hours = int(match.group(1))
            if -23 <= hours <= 23:
                return timezone(timedelta(hours=hours))

        # Иначе пытаемся интерпретировать как IANA timezone, например Europe/Minsk.
        try:
            return ZoneInfo(value)
        except Exception:
            # Безопасный fallback для проекта.
            return ZoneInfo("Europe/Minsk")

    def __init__(self) -> None:
        from src.infra.db.repositories.appointments_repository import AppointmentsRepository
        from src.infra.db.repositories.reminder_jobs_repository import ReminderJobsRepository

        self._appointments_repo = AppointmentsRepository()
        self._jobs_repo = ReminderJobsRepository()
        settings = get_settings()
        self._app_timezone = self._resolve_timezone(settings.app_timezone)
        self._offset_24h_minutes = settings.reminder_24h_offset_minutes
        self._offset_2h_minutes = settings.reminder_2h_offset_minutes

    def _appointment_dt_local(self, appointment: "AppointmentModel") -> datetime:
        # Время записи интерпретируется как локальное время барбера.
        return datetime.combine(
            appointment.date,
            appointment.start_time,
            tzinfo=self._app_timezone,
        )

    def _remind_times_from_appointment_dt(self, appt_dt_utc: datetime) -> list[tuple[str, datetime]]:
        return [
            ("24h", appt_dt_utc - timedelta(minutes=self._offset_24h_minutes)),
            ("2h", appt_dt_utc - timedelta(minutes=self._offset_2h_minutes)),
        ]

    async def schedule_reminders(self, appointment: "AppointmentModel") -> None:
        now = datetime.now(timezone.utc)
        appt_dt_local = self._appointment_dt_local(appointment)
        reminds = self._remind_times_from_appointment_dt(appt_dt_local)

        # Планируем только будущие напоминания, чтобы не спамить сразу после создания.
        for remind_type, remind_at in reminds:
            remind_at_utc = remind_at.astimezone(timezone.utc)
            if remind_at_utc <= now:
                continue
            await self._jobs_repo.insert_for_appointment(
                appointment_id=appointment.id,
                user_id=appointment.user_id,
                remind_type=remind_type,
                remind_at=remind_at_utc,
            )

    async def send_due_reminders(self, bot: Bot) -> None:
        now = datetime.now(timezone.utc)

        # MVP: перед отправкой напоминаний автозавершаем все ended confirmed-записи,
        # чтобы они перестали блокировать слоты и не спамили напоминаниями.
        now_local = datetime.now(self._app_timezone)
        ended_ids = await self._appointments_repo.complete_ended_confirmed_appointments(now_local)
        if ended_ids:
            await asyncio.gather(
                *[self.cancel_future_reminders_for_appointment(aid) for aid in ended_ids],
                return_exceptions=True,
            )

        due = await self._jobs_repo.fetch_due_unsent(now)
        if not due:
            return

        for job in due:
            reminder_job_id = int(job["id"])
            appointment_id = int(job["appointment_id"])
            user_id = int(job["user_id"])
            remind_type = str(job.get("remind_type") or "")

            appointment = await self._appointments_repo.get_by_id(appointment_id)
            if appointment is None or appointment.status != "confirmed":
                # Если запись отменена/удалена, закрываем job, чтобы не крутить ее бесконечно.
                await self._jobs_repo.mark_sent(reminder_job_id)
                continue

            settings = get_settings()
            template = (
                settings.reminder_text_24h
                if remind_type == "24h"
                else settings.reminder_text_2h
            )
            text = self._safe_format(
                template,
                {
                    "date": appointment.date.strftime("%d.%m.%Y"),
                    "time": appointment.start_time.strftime("%H:%M"),
                    "branch": appointment.branch_name or "—",
                    "master": appointment.master_name or "—",
                },
            )

            try:
                await bot.send_message(user_id, text)
                await self._jobs_repo.mark_sent(reminder_job_id)
            except Exception:
                # Не отмечаем sent_at, чтобы можно было повторить на следующем цикле.
                continue

    async def cancel_future_reminders_for_appointment(self, appointment_id: int) -> None:
        await self._jobs_repo.mark_all_unsent_for_appointment_as_sent(appointment_id)
