"""
/**
 * @file: calendar.py
 * @description: Генерация inline-календаря для записи
 * @dependencies: aiogram.types, infra.config.settings, work_schedule_repository
 * @created: 2026-04-02
 */
"""

from __future__ import annotations

import calendar
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.infra.config.settings import get_settings
from src.infra.db.repositories.work_schedule_repository import WorkScheduleRepository

RU_MONTHS_NOM = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}

WEEKDAY_LABELS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

STATUS_AVAILABLE = "Available"
STATUS_EMPTY = "Empty"


def _local_tz() -> ZoneInfo:
    settings = get_settings()
    return ZoneInfo(settings.app_timezone or "Europe/Minsk")


def _month_delta(year: int, month: int, delta: int) -> tuple[int, int]:
    base = year * 12 + (month - 1) + delta
    return base // 12, (base % 12) + 1


def generate_calendar(year: int, month: int) -> list[list[dict]]:
    """
    Возвращает календарную сетку месяца (Пн..Вс) как list[list[dict]].
    """
    cal = calendar.Calendar(firstweekday=0)
    weeks: list[list[dict]] = []
    for week in cal.monthdatescalendar(year, month):
        row: list[dict] = []
        for d in week:
            row.append(
                {
                    "date": d,
                    "day": d.day,
                    "in_month": d.month == month,
                    "status": STATUS_EMPTY if d.month != month else STATUS_AVAILABLE,
                }
            )
        weeks.append(row)
    return weeks


async def is_date_available(dt: datetime) -> bool:
    """
    Базовая проверка доступности даты (без учета занятости слотов по услуге).
    """
    tz = _local_tz()
    local_date = dt.astimezone(tz).date()
    now_local = datetime.now(tz)

    if local_date < now_local.date():
        return False

    repo = WorkScheduleRepository()
    schedule = await repo.get_latest()
    if schedule is None:
        weekdays = {0, 1, 2, 3, 4, 5}
        end_time = datetime.strptime("20:00", "%H:%M").time()
    else:
        weekdays = set(schedule.weekdays)
        end_time = schedule.end_time

    if local_date.weekday() not in weekdays:
        return False

    if local_date == now_local.date() and now_local.time() >= end_time:
        return False
    return True


def build_calendar_keyboard(
    year: int,
    month: int,
    booked_dates: list[date],
) -> InlineKeyboardMarkup:
    """
    Строит inline-календарь с навигацией по месяцам (текущий..+3 месяца).

    Доступные дни — число без скобок, `bk_cal:YYYY-MM-DD`.
    Недоступные (прошлое, нет слотов, не рабочий день) — «(15)», callback `bk_cal_dis`
    (выбор даты не выполняется; в Telegram кнопки нельзя отключить полностью).
    """
    tz = _local_tz()
    today = datetime.now(tz).date()
    max_year, max_month = _month_delta(today.year, today.month, 3)

    weeks = generate_calendar(year, month)
    booked_set = set(booked_dates)

    rows: list[list[InlineKeyboardButton]] = []
    rows.append(
        [
            InlineKeyboardButton(text=f"{RU_MONTHS_NOM[month]} {year}", callback_data="bk_cal_noop"),
        ]
    )
    rows.append([InlineKeyboardButton(text=w, callback_data="bk_cal_noop") for w in WEEKDAY_LABELS])

    for week in weeks:
        row: list[InlineKeyboardButton] = []
        for cell in week:
            d: date = cell["date"]
            in_month = bool(cell["in_month"])
            if not in_month:
                row.append(InlineKeyboardButton(text=" ", callback_data="bk_cal_noop"))
                continue

            inactive = d < today or d in booked_set
            if inactive:
                day_label = f"({d.day})"
                cb = "bk_cal_dis"
            else:
                day_label = str(d.day)
                cb = f"bk_cal:{d.isoformat()}"

            row.append(InlineKeyboardButton(text=day_label, callback_data=cb))
        rows.append(row)

    prev_allowed = (year, month) > (today.year, today.month)
    next_allowed = (year, month) < (max_year, max_month)
    prev_year, prev_month = _month_delta(year, month, -1)
    next_year, next_month = _month_delta(year, month, 1)
    rows.append(
        [
            InlineKeyboardButton(
                text="◀",
                callback_data=f"bk_cal_nav:{prev_year:04d}-{prev_month:02d}" if prev_allowed else "bk_cal_noop",
            ),
            InlineKeyboardButton(
                text="▶",
                callback_data=f"bk_cal_nav:{next_year:04d}-{next_month:02d}" if next_allowed else "bk_cal_noop",
            ),
        ]
    )
    rows.append([InlineKeyboardButton(text="← Назад", callback_data="bk_back:category")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
