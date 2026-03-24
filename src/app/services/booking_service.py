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
from datetime import date
from typing import Optional

from src.app.services.schedule_service import ScheduleService
from src.app.services.reminder_service import ReminderService
from src.infra.db.models import AppointmentModel, UserModel
from src.infra.db.repositories.appointments_repository import (
    AppointmentsRepository,
    SlotUnavailableError,
)
from src.infra.db.repositories.users_repository import UsersRepository


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
        self._reminder_service = ReminderService()

    async def get_user(self, user_id: int) -> Optional[UserModel]:
        return await self._users_repo.get_by_user_id(user_id)

    async def register_user(self, user_id: int, phone: str, name: str) -> UserModel:
        user = UserModel(
            user_id=user_id,
            phone=phone,
            name=name,
            created_at=None,  # создается на стороне БД/необязательно для MVP
        )
        await self._users_repo.upsert(user)
        return user

    async def get_active_appointment(self, user_id: int) -> Optional[AppointmentModel]:
        return await self._appointments_repo.get_active_for_user(user_id)

    async def list_available_time_slots(self, target_date: date) -> list[str]:
        candidate = self._schedule_service.get_candidate_slots_for_date(target_date)
        occupied = await self._appointments_repo.list_confirmed_time_slots(target_date)
        return [slot for slot in candidate if slot not in occupied]

    async def create_appointment(self, user_id: int, target_date: date, time_slot_hhmm: str) -> AppointmentModel:
        existing = await self.get_active_appointment(user_id)
        if existing is not None:
            raise BookingAlreadyExistsError(
                "У вас уже есть активная запись. Сначала отмените её."
            )

        try:
            appointment = await self._appointments_repo.create_confirmed(
                user_id=user_id,
                target_date=target_date,
                time_slot_hhmm=time_slot_hhmm,
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
