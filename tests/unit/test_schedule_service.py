"""
/**
 * @file: test_schedule_service.py
 * @description: Unit-тест генерации временных слотов
 * @dependencies: app.services.schedule_service, pytest
 * @created: 2026-03-23
 */
"""

import asyncio
from datetime import date, time, timedelta

from src.app.services.booking_service import BookingService
from src.app.services.schedule_service import ScheduleService
from src.infra.db.models import ServiceModel
from src.infra.db.repositories.work_schedule_repository import WorkScheduleModel


class _DummyWorkScheduleRepo:
    async def get_latest(self):
        return WorkScheduleModel(
            weekdays={0, 1, 2, 3, 4, 5},
            start_time=time(10, 0),
            end_time=time(20, 0),
        )


class _DummyServicesRepo:
    async def get_by_id(self, service_id: int):
        if service_id != 1:
            return None
        return ServiceModel(id=1, name="Test", price_byn=45, duration_minutes=60)


class _DummyAppointmentsRepo:
    async def list_confirmed_intervals(self, target_date: date):
        # Один подтвержденный интервал: [10:00, 11:00)
        return [(time(10, 0), time(11, 0))]

class _DummyCustomLunchScheduleRepo:
    async def get_latest(self):
        return WorkScheduleModel(
            weekdays={0, 1, 2, 3, 4, 5},
            start_time=time(8, 0),
            end_time=time(20, 0),
            lunch_time=time(18, 30),
        )


def test_schedule_service_step_and_last_start_duration_60() -> None:
    service = ScheduleService()
    service._repo = _DummyWorkScheduleRepo()

    # 2026-03-30 — понедельник
    target_date = date(2026, 3, 30)

    slots = asyncio.run(service.get_candidate_slots_for_date(target_date, duration_minutes=60))

    # step=duration => старт 10:00...19:00, последний старт для 60 минут = 19:00
    assert slots[0] == "10:00"
    assert "19:00" in slots
    assert "19:30" not in slots


def test_schedule_service_lunch_blocking() -> None:
    service = ScheduleService()
    service._repo = _DummyWorkScheduleRepo()

    target_date = date(2026, 3, 30)
    slots = asyncio.run(service.get_candidate_slots_for_date(target_date, duration_minutes=60))

    # Обед 14:00–15:00 блокирует интервалы, которые пересекаются с ним
    assert "14:00" not in slots
    assert "13:00" in slots
    assert "15:00" in slots


def test_booking_service_overlap_filter_half_open_interval() -> None:
    bs = BookingService()
    bs._schedule_service._repo = _DummyWorkScheduleRepo()
    bs._services_repo = _DummyServicesRepo()
    bs._appointments_repo = _DummyAppointmentsRepo()

    target_date = date(2026, 3, 30)
    slots = asyncio.run(bs.list_available_time_slots(target_date, service_id=1))

    # Существующая запись [10:00, 11:00):
    assert "10:00" not in slots
    assert "10:30" not in slots

    # Граница end_time == start_time считается допустимой:
    assert "11:00" in slots


def test_schedule_service_step_changes_with_duration_90() -> None:
    service = ScheduleService()
    service._repo = _DummyWorkScheduleRepo()

    target_date = date(2026, 3, 30)
    slots = asyncio.run(service.get_candidate_slots_for_date(target_date, duration_minutes=90))

    # step=90 => стартовые времена идут каждые 90 минут:
    # 10:00, 11:30, 13:00, 14:30, 16:00, 17:30
    # 13:00 и 14:30 пересекаются с обедом 14:00–15:00, поэтому их не должно быть.
    assert "10:00" in slots
    assert "11:30" in slots
    assert "13:00" not in slots
    assert "14:30" not in slots
    assert "16:00" in slots
    assert "17:30" in slots


def test_schedule_service_sunday_allowed_when_in_schedule_weekdays() -> None:
    class _RepoWithSunday:
        async def get_latest(self):
            return WorkScheduleModel(
                weekdays={0, 1, 2, 3, 4, 5, 6},
                start_time=time(10, 0),
                end_time=time(20, 0),
            )

    service = ScheduleService()
    service._repo = _RepoWithSunday()
    # 2026-04-05 — воскресенье
    target_date = date(2026, 4, 5)
    slots = asyncio.run(service.get_candidate_slots_for_date(target_date, duration_minutes=60))
    assert "10:00" in slots


def test_schedule_service_uses_lunch_time_from_schedule() -> None:
    service = ScheduleService()
    service._repo = _DummyCustomLunchScheduleRepo()

    target_date = date(2026, 3, 30)
    slots = asyncio.run(service.get_candidate_slots_for_date(target_date, duration_minutes=30))

    # lunch_time=18:30 => блок 18:30–19:30
    assert "18:30" not in slots
    assert "19:00" not in slots
    assert "19:30" in slots
