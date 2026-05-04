"""
/**
 * @file: booking_service.py
 * @description: Бизнес-логика записи/отмены для MVP через Supabase
 * @dependencies: app.services.schedule_service, infra.db.repositories
 * @created: 2026-03-23
 */
"""

from __future__ import annotations

import calendar
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
from src.infra.db.repositories.masters_repository import MastersRepository
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
        self._masters_repo = MastersRepository()
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

    async def list_user_appointments(self, user_id: int, limit: int = 20) -> list[AppointmentModel]:
        return await self._appointments_repo.list_for_user(user_id, limit=limit)

    async def get_appointment_by_id(self, appointment_id: int) -> Optional[AppointmentModel]:
        return await self._appointments_repo.get_by_id(appointment_id)

    @staticmethod
    def _intervals_overlap(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
        # Half-open intervals: [start, end)
        return a_start < b_end and a_end > b_start

    async def list_available_time_slots(
        self,
        target_date: date,
        service_id: int,
        master_key: Optional[str] = None,
    ) -> list[str]:
        service = await self._services_repo.get_by_id(service_id)
        if service is None:
            return []

        candidates = await self._schedule_service.get_candidate_slots_for_date(
            target_date,
            duration_minutes=service.duration_minutes,
        )
        try:
            occupied = await self._appointments_repo.list_confirmed_intervals(
                target_date,
                master_key=master_key,
            )
        except TypeError:
            # Совместимость с тестовыми даблами/старыми реализациями репозитория.
            occupied = await self._appointments_repo.list_confirmed_intervals(target_date)

        out: list[str] = []
        master = await self._masters_repo.get_by_key(master_key) if master_key else None
        for slot_hhmm in candidates:
            start_t = datetime.strptime(slot_hhmm, "%H:%M").time()
            end_t = (datetime.combine(target_date, start_t) + timedelta(minutes=service.duration_minutes)).time()

            if master is not None:
                if start_t < master.work_start or end_t > master.work_end:
                    continue

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

    async def list_available_slots_for_any_master(
        self,
        target_date: date,
        service_id: int,
        masters: list[tuple[str, str]],
    ) -> dict[str, tuple[str, str]]:
        """
        Возвращает карту slot -> (master_key, master_name) для режима "любой мастер".
        Если слот доступен у нескольких мастеров, выбирается первый по порядку в списке masters.
        """
        slot_map: dict[str, tuple[str, str]] = {}
        for master_key, master_name in masters:
            slots = await self.list_available_time_slots(
                target_date=target_date,
                service_id=service_id,
                master_key=master_key,
            )
            for slot in slots:
                if slot not in slot_map:
                    slot_map[slot] = (master_key, master_name)
        return slot_map

    async def dates_without_available_slots_in_month(
        self,
        year: int,
        month: int,
        service_id: int,
        master_key: Optional[str] = None,
    ) -> list[date]:
        """
        Дни месяца, в которые для выбранной услуги нет ни одного свободного слота
        (для подсветки календаря). Один запрос занятых интервалов на весь месяц.
        """
        settings = get_settings()
        tz = ZoneInfo(settings.app_timezone or "Europe/Minsk")
        now_local = datetime.now(tz)
        today = now_local.date()

        service = await self._services_repo.get_by_id(service_id)
        first_day = date(year, month, 1)
        last_day = date(year, month, calendar.monthrange(year, month)[1])
        try:
            intervals_by_date = await self._appointments_repo.list_confirmed_intervals_range(
                first_day,
                last_day,
                master_key=master_key,
            )
        except TypeError:
            # Совместимость с тестовыми даблами/старыми реализациями репозитория.
            intervals_by_date = await self._appointments_repo.list_confirmed_intervals_range(
                first_day,
                last_day,
            )

        out: list[date] = []
        cur = first_day
        while cur <= last_day:
            if cur < today:
                out.append(cur)
                cur += timedelta(days=1)
                continue
            day_schedule = await self._schedule_service.get_day_schedule_for_date(cur)
            if day_schedule.is_day_off:
                out.append(cur)
                cur += timedelta(days=1)
                continue
            if day_schedule.end_time is None:
                out.append(cur)
                cur += timedelta(days=1)
                continue
            if cur == today and now_local.time() >= day_schedule.end_time:
                out.append(cur)
                cur += timedelta(days=1)
                continue

            if service is None:
                candidates: list[str] = []
            else:
                candidates = self._schedule_service.candidate_slots_for_day_schedule_sync(
                    cur, service.duration_minutes, day_schedule
                )

            if cur == today:
                filtered: list[str] = []
                for slot_hhmm in candidates:
                    start_t = datetime.strptime(slot_hhmm, "%H:%M").time()
                    slot_dt_local = datetime.combine(cur, start_t, tzinfo=tz)
                    if slot_dt_local < now_local:
                        continue
                    filtered.append(slot_hhmm)
                candidates = filtered

            occupied = intervals_by_date.get(cur, [])
            has_slot = False
            if service is not None:
                for slot_hhmm in candidates:
                    start_t = datetime.strptime(slot_hhmm, "%H:%M").time()
                    end_t = (
                        datetime.combine(cur, start_t) + timedelta(minutes=service.duration_minutes)
                    ).time()
                    if any(
                        self._intervals_overlap(start_t, end_t, o_start, o_end)
                        for o_start, o_end in occupied
                    ):
                        continue
                    has_slot = True
                    break

            if not has_slot:
                out.append(cur)
            cur += timedelta(days=1)

        return out

    async def create_appointment(
        self,
        user_id: int,
        target_date: date,
        service_id: int,
        time_slot_hhmm: str,
        branch_id: Optional[int] = None,
        master_id: Optional[int] = None,
        branch_name: Optional[str] = None,
        master_name: Optional[str] = None,
        master_key: Optional[str] = None,
        comment: Optional[str] = None,
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
                branch_id=branch_id,
                master_id=master_id,
                branch_name=branch_name,
                master_name=master_name,
                master_key=master_key,
                comment=comment,
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

    async def reschedule_appointment(
        self,
        *,
        user_id: int,
        source_appointment_id: int,
        target_date: date,
        time_slot_hhmm: str,
    ) -> AppointmentModel:
        """
        Перенос записи через создание новой записи и отмену старой.
        Если новый слот в последний момент занят, пытаемся восстановить старую запись.
        """
        source = await self._appointments_repo.get_by_id(source_appointment_id)
        if source is None or source.user_id != user_id or source.status != "confirmed":
            raise RuntimeError("Активная запись для переноса не найдена")

        service = await self._services_repo.get_by_id(source.service_id)
        if service is None:
            raise RuntimeError("Не удалось найти услугу для переноса")

        new_start = datetime.strptime(time_slot_hhmm, "%H:%M").time()
        if source.date == target_date and source.start_time == new_start:
            raise RuntimeError("Вы выбрали то же время. Перенос не требуется.")

        source_date = source.date
        source_start = source.start_time
        source_end = source.end_time
        source_service_id = source.service_id

        cancelled = await self.cancel_appointment_by_id(source_appointment_id)
        if cancelled is None:
            raise RuntimeError("Не удалось перенести запись: исходная запись неактивна")

        try:
            return await self.create_appointment(
                user_id=user_id,
                target_date=target_date,
                service_id=service.id,
                time_slot_hhmm=time_slot_hhmm,
                branch_id=source.branch_id,
                master_id=source.master_id,
                branch_name=source.branch_name,
                master_name=source.master_name,
                master_key=source.master_key,
                comment=source.comment,
            )
        except Exception as create_error:
            # Best-effort rollback, чтобы не оставить клиента без записи из-за гонки.
            try:
                restored = await self._appointments_repo.create_confirmed(
                    user_id=user_id,
                    target_date=source_date,
                    service_id=source_service_id,
                    start_time=source_start,
                    end_time=source_end,
                    branch_id=source.branch_id,
                    master_id=source.master_id,
                    branch_name=source.branch_name,
                    master_name=source.master_name,
                    master_key=source.master_key,
                    comment=source.comment,
                )
                await self._reminder_service.schedule_reminders(restored)
            except Exception:
                pass
            raise create_error
