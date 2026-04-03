"""
/**
 * @file: booking_service.py
 * @description: Бизнес-логика записи/отмены для MVP через Supabase
 * @dependencies: app.services.schedule_service, infra.db.repositories
 * @created: 2026-03-23
 */
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from src.app.services.schedule_service import ScheduleService
from src.app.services.reminder_service import ReminderService
from src.infra.db.models import AppointmentModel, UserModel
from src.infra.db.repositories.appointments_repository import (
    AppointmentsRepository,
    SlotUnavailableError,
)
from src.infra.db.repositories.services_repository import ServicesRepository
from src.infra.db.repositories.users_repository import UsersRepository
from src.infra.config.settings import get_settings


class BookingAlreadyExistsError(Exception):
    pass


@dataclass(frozen=True)
class BookingResult:
    appointment: AppointmentModel


class BookingService:
    def __init__(self) -> None:
        self._schedule_service = ScheduleService()
        self._users_repo = UsersRepository()
        self._appointments_repo = AppointmentsRepository()
        self._services_repo = ServicesRepository()
        self._reminder_service = ReminderService()

    async def get_user(self, user_id: int) -> Optional[UserModel]:
        return await self._users_repo.get_by_user_id(user_id)

    async def register_user(self, user_id: int, phone: str, name: str) -> UserModel:
        user = UserModel(
            user_id=user_id,
            phone=phone,
            name=name,
            role="client",
            created_at=None,  # создается на стороне БД/необязательно для MVP
        )
        await self._users_repo.upsert(user)
        return user

    async def get_active_appointment(self, user_id: int) -> Optional[AppointmentModel]:
        appt = await self._appointments_repo.get_active_for_user(user_id)
        if appt is None:
            return None

        settings = get_settings()
        tz = ZoneInfo(settings.app_timezone or "Europe/Minsk")
        now_local = datetime.now(tz)

        # Даже если в БД status ещё 'confirmed', считаем запись неактивной,
        # когда она уже закончилась по времени.
        end_dt_local = datetime.combine(appt.date, appt.end_time, tzinfo=tz)
        if appt.date < now_local.date() or end_dt_local <= now_local:
            return None

        return appt

    @staticmethod
    def _intervals_overlap(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
        # Half-open intervals: [start, end)
        return a_start < b_end and a_end > b_start

    async def list_available_time_slots(self, target_date: date, service_id: int) -> list[str]:
        service = await self._services_repo.get_by_id(service_id)
        if service is None:
            return []

        candidates = await self._schedule_service.get_candidate_slots_for_date(
            target_date,
            duration_minutes=service.duration_minutes,
        )
        occupied = await self._appointments_repo.list_confirmed_intervals(target_date)

        out: list[str] = []
        for slot_hhmm in candidates:
            start_t = datetime.strptime(slot_hhmm, "%H:%M").time()
            end_t = (datetime.combine(target_date, start_t) + timedelta(minutes=service.duration_minutes)).time()

            # Если выбираем "сегодня", скрываем уже начавшиеся слоты.
            if target_date == date.today():
                settings = get_settings()
                tz = ZoneInfo(settings.app_timezone or "Europe/Minsk")
                now_local = datetime.now(tz)
                slot_dt_local = datetime.combine(target_date, start_t, tzinfo=tz)
                if slot_dt_local < now_local:
                    continue

            if any(self._intervals_overlap(start_t, end_t, o_start, o_end) for o_start, o_end in occupied):
                continue
            out.append(slot_hhmm)

        return out

    async def create_appointment(
        self,
        user_id: int,
        target_date: date,
        service_id: int,
        time_slot_hhmm: str,
    ) -> AppointmentModel:
        existing = await self.get_active_appointment(user_id)
        if existing is not None:
            raise BookingAlreadyExistsError(
                "У вас уже есть активная запись. Сначала отмените её."
            )

        try:
            service = await self._services_repo.get_by_id(service_id)
            if service is None:
                raise RuntimeError("Указанная услуга не найдена")

            start_t = datetime.strptime(time_slot_hhmm, "%H:%M").time()
            end_t = (datetime.combine(target_date, start_t) + timedelta(minutes=service.duration_minutes)).time()

            # Защита: не создаем запись в прошлом (особенно если кнопка устарела).
            if target_date == date.today():
                settings = get_settings()
                tz = ZoneInfo(settings.app_timezone or "Europe/Minsk")
                now_local = datetime.now(tz)
                start_dt_local = datetime.combine(target_date, start_t, tzinfo=tz)
                if start_dt_local < now_local:
                    raise SlotUnavailableError("Слот уже прошел. Выберите другое время.")

            appointment = await self._appointments_repo.create_confirmed(
                user_id=user_id,
                target_date=target_date,
                service_id=service_id,
                start_time=start_t,
                end_time=end_t,
            )
            await self._reminder_service.schedule_reminders(appointment)
            return appointment
        except SlotUnavailableError:
            # Пробрасываем исходный смысл исключения наверх.
            raise

    async def cancel_active_appointment(self, user_id: int) -> Optional[AppointmentModel]:
        cancelled = await self._appointments_repo.cancel_active_for_user(user_id)
        if cancelled is not None:
            await self._reminder_service.cancel_future_reminders_for_appointment(cancelled.id)
        return cancelled

    async def cancel_appointment_by_id(self, appointment_id: int) -> Optional[AppointmentModel]:
        cancelled = await self._appointments_repo.cancel_confirmed_by_id(appointment_id)
        if cancelled is not None:
            await self._reminder_service.cancel_future_reminders_for_appointment(cancelled.id)
        return cancelled
