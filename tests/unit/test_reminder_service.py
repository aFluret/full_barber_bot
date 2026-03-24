"""
/**
 * @file: test_reminder_service.py
 * @description: Unit-тест вычисления времени напоминаний (без Supabase)
 * @dependencies: app.services.reminder_service, infra.db.models, datetime
 * @created: 2026-03-23
 */
"""

from datetime import date, datetime, time, timedelta, timezone

from src.app.services.reminder_service import ReminderService
from src.infra.config.settings import get_settings
from src.infra.db.models import AppointmentModel


def test_remind_times_from_appointment_dt_local(monkeypatch) -> None:
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Minsk")
    monkeypatch.setenv("REMINDER_24H_OFFSET_MINUTES", "1440")
    monkeypatch.setenv("REMINDER_2H_OFFSET_MINUTES", "120")
    get_settings.cache_clear()
    service = ReminderService()

    appt = AppointmentModel(
        id=1,
        user_id=10,
        date=date(2026, 3, 28),
        time_slot=time(1, 0),
        status="confirmed",
        created_at=datetime.now(timezone.utc),
    )

    appt_dt = service._appointment_dt_local(appt)
    assert appt_dt.year == 2026
    assert appt_dt.month == 3
    assert appt_dt.day == 28
    assert appt_dt.hour == 1
    assert appt_dt.minute == 0
    assert appt_dt.utcoffset() == timedelta(hours=3)

    times = service._remind_times_from_appointment_dt(appt_dt)
    assert times[0][0] == "24h"
    assert times[0][1] == appt_dt - timedelta(hours=24)
    assert times[1][0] == "2h"
    assert times[1][1] == appt_dt - timedelta(hours=2)


def test_remind_times_can_be_overridden(monkeypatch) -> None:
    monkeypatch.setenv("APP_TIMEZONE", "Europe/Minsk")
    monkeypatch.setenv("REMINDER_24H_OFFSET_MINUTES", "2")
    monkeypatch.setenv("REMINDER_2H_OFFSET_MINUTES", "1")
    get_settings.cache_clear()

    service = ReminderService()
    appt_dt = datetime(2026, 3, 28, 1, 0, tzinfo=timezone(timedelta(hours=3)))
    times = service._remind_times_from_appointment_dt(appt_dt)
    assert times[0][1] == appt_dt - timedelta(minutes=2)
    assert times[1][1] == appt_dt - timedelta(minutes=1)


def test_timezone_can_be_numeric_offset(monkeypatch) -> None:
    monkeypatch.setenv("APP_TIMEZONE", "3")
    monkeypatch.setenv("REMINDER_24H_OFFSET_MINUTES", "1440")
    monkeypatch.setenv("REMINDER_2H_OFFSET_MINUTES", "120")
    get_settings.cache_clear()

    service = ReminderService()
    appt = AppointmentModel(
        id=2,
        user_id=22,
        date=date(2026, 3, 28),
        time_slot=time(16, 0),
        status="confirmed",
        created_at=datetime.now(timezone.utc),
    )
    appt_dt = service._appointment_dt_local(appt)
    assert appt_dt.utcoffset() == timedelta(hours=3)

