"""
/**
 * @file: schedule_service.py
 * @description: Сервис генерации временных слотов (пока на дефолтном графике)
 * @dependencies: datetime, typing
 * @created: 2026-03-23
 */
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, time
from src.infra.db.repositories.work_schedule_repository import WorkScheduleModel, WorkScheduleRepository


class ScheduleService:
    DEFAULT_START = "10:00"
    DEFAULT_END = "20:00"
    # Шаг стартового времени — 30 минут (как в TZ_MARK).
    DEFAULT_STEP_MINUTES = 30
    WORKING_WEEKDAYS = {0, 1, 2, 3, 4, 5}  # Пн..Сб (0=Пн)

    LUNCH_START = time(14, 0)
    LUNCH_END = time(15, 0)
    LUNCH_DURATION_MINUTES = 60

    def __init__(self) -> None:
        self._repo = WorkScheduleRepository()

    @staticmethod
    def _intervals_overlap(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
        # Half-open intervals: [start, end). Границы не считаем пересечением.
        return a_start < b_end and a_end > b_start

    async def _get_schedule_or_default(self) -> WorkScheduleModel:
        schedule = await self._repo.get_latest()
        if schedule is None:
            return WorkScheduleModel(
                weekdays=set(self.WORKING_WEEKDAYS),
                start_time=datetime.strptime(self.DEFAULT_START, "%H:%M").time(),
                end_time=datetime.strptime(self.DEFAULT_END, "%H:%M").time(),
                lunch_time=self.LUNCH_START,
            )
        if schedule.lunch_time is None:
            return WorkScheduleModel(
                weekdays=set(schedule.weekdays),
                start_time=schedule.start_time,
                end_time=schedule.end_time,
                lunch_time=self.LUNCH_START,
            )
        return schedule

    async def next_working_dates(self, count: int) -> list[date]:
        today = date.today()
        schedule = await self._get_schedule_or_default()
        out: list[date] = []
        d = today
        while len(out) < count:
            # TZ_MARK: в календаре выбора даты воскресенье не показываем.
            if d.weekday() == 6:
                d += timedelta(days=1)
                continue

            if d.weekday() in schedule.weekdays:
                out.append(d)
            d += timedelta(days=1)
        return out

    async def get_candidate_slots_for_date(self, target_date: date, duration_minutes: int) -> list[str]:
        schedule = await self._get_schedule_or_default()
        if target_date.weekday() == 6:
            return []
        if target_date.weekday() not in schedule.weekdays:
            return []

        work_start_dt = datetime.combine(target_date, schedule.start_time)
        work_end_dt = datetime.combine(target_date, schedule.end_time)
        # По ТЗ: шаг стартового времени зависит от длительности услуги.
        # Например, для 60 минут шаг = 60, для 90 минут шаг = 90 и т.д.
        step = timedelta(minutes=duration_minutes)

        # Последний старт, при котором запись успевает закончиться до конца рабочего дня.
        last_start_dt = work_end_dt - timedelta(minutes=duration_minutes)
        if last_start_dt < work_start_dt:
            return []

        out: list[str] = []
        current = work_start_dt
        while current <= last_start_dt:
            start_t = current.time()
            end_t = (current + timedelta(minutes=duration_minutes)).time()

            # TZ_MARK: слот не должен пересекаться с обедом.
            if schedule.lunch_time is not None:
                lunch_start = schedule.lunch_time
                lunch_end = (
                    datetime.combine(target_date, lunch_start) + timedelta(minutes=self.LUNCH_DURATION_MINUTES)
                ).time()
                if self._intervals_overlap(start_t, end_t, lunch_start, lunch_end):
                    current += step
                    continue

            out.append(start_t.strftime("%H:%M"))
            current += step

        return out
