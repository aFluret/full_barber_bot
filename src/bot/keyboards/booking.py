"""
/**
 * @file: booking.py
 * @description: Inline-клавиатуры сценария записи (дата/время/подтверждение)
 * @dependencies: aiogram.types
 * @created: 2026-03-23
 */
"""

from __future__ import annotations

from datetime import date, timedelta

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.infra.db.models import ServiceModel


RU_WEEKDAY_ABBR = {
    0: "Пн",
    1: "Вт",
    2: "Ср",
    3: "Чт",
    4: "Пт",
    5: "Сб",
    6: "Вс",
}

RU_MONTHS_GEN = {
    1: "января",
    2: "февраля",
    3: "марта",
    4: "апреля",
    5: "мая",
    6: "июня",
    7: "июля",
    8: "августа",
    9: "сентября",
    10: "октября",
    11: "ноября",
    12: "декабря",
}


def _format_date_button_text(d: date, today: date | None = None) -> str:
    today = today or date.today()
    if d == today:
        return "Сегодня"
    if d == today + timedelta(days=1):
        return "Завтра"
    return f"{RU_WEEKDAY_ABBR[d.weekday()]}, {d.day} {RU_MONTHS_GEN[d.month]}"


def _format_duration(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} мин"
    hours, rest = divmod(minutes, 60)
    if rest == 0:
        return f"{hours} ч"
    return f"{hours} ч {rest} мин"


def date_picker_keyboard_with_back(dates: list[date], back_callback_data: str | None) -> InlineKeyboardMarkup:
    today = date.today()
    # Показываем по 3 кнопки в ряду, чтобы уменьшить количество скролла.
    buttons: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(dates), 3):
        row = dates[i : i + 3]
        buttons.append(
            [
                InlineKeyboardButton(
                    text=_format_date_button_text(d, today=today),
                    callback_data=f"bk_date:{d.isoformat()}",
                )
                for d in row
            ]
        )

    if back_callback_data:
        buttons.append([InlineKeyboardButton(text="← Назад", callback_data=back_callback_data)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def services_picker_keyboard(
    services: list[ServiceModel],
    back_callback_data: str | None = None,
) -> InlineKeyboardMarkup:
    """
    Инлайн-выбор услуги с отображением цены и длительности.
    callback_data формата: `bk_service:{service_id}`
    """

    buttons: list[list[InlineKeyboardButton]] = []
    # Вертикальный список: по одной кнопке в ряд.
    for s in services:
        price_label = f"{s.price_byn} BYN" if s.price_byn else "цена по запросу"
        buttons.append(
            [
                InlineKeyboardButton(
                    text=f"{s.name} • {_format_duration(s.duration_minutes)} • {price_label}",
                    callback_data=f"bk_service:{s.id}",
                )
            ]
        )

    if back_callback_data:
        buttons.append([InlineKeyboardButton(text="← Назад", callback_data=back_callback_data)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def time_picker_keyboard(slots: list[str], back_callback_data: str | None = None) -> InlineKeyboardMarkup:
    # Показываем по 3 кнопки в ряду.
    buttons: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(slots), 3):
        row = slots[i : i + 3]
        buttons.append(
            [
                InlineKeyboardButton(
                    text=slot,
                    callback_data=f"bk_time:{slot}",
                )
                for slot in row
            ]
        )

    if back_callback_data:
        buttons.append([InlineKeyboardButton(text="← Назад", callback_data=back_callback_data)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def confirm_booking_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить", callback_data="bk_confirm:1")],
            [InlineKeyboardButton(text="← Назад", callback_data="bk_confirm:0")],
        ]
    )


def comment_choice_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✍️ Добавить комментарий", callback_data="bk_comment:add")],
            [InlineKeyboardButton(text="➡️ Без комментария", callback_data="bk_comment:skip")],
            [InlineKeyboardButton(text="← Назад ко времени", callback_data="bk_comment:back_time")],
        ]
    )


def categories_picker_keyboard(
    categories: list[tuple[str, str]],
    back_callback_data: str = "bk_back:menu",
) -> InlineKeyboardMarkup:
    """
    categories: list of (key, label)
    """
    buttons: list[list[InlineKeyboardButton]] = []
    # Вертикальный список: по одной кнопке в ряд.
    for key, label in categories:
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"bk_cat:{key}")])

    buttons.append([InlineKeyboardButton(text="← Назад", callback_data=back_callback_data)])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def date_picker_keyboard(dates: list[date], back_callback_data: str | None = None) -> InlineKeyboardMarkup:
    return date_picker_keyboard_with_back(dates, back_callback_data=back_callback_data)


def branches_picker_keyboard(branches: list[str]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for idx, label in enumerate(branches):
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"bk_branch:{idx}")])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="bk_back:menu")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def masters_picker_keyboard(masters: list[str], include_any: bool = True) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    if include_any:
        buttons.append([InlineKeyboardButton(text="Любой мастер", callback_data="bk_master:any")])
    for idx, label in enumerate(masters):
        buttons.append([InlineKeyboardButton(text=label, callback_data=f"bk_master:{idx}")])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="bk_back:branch")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
