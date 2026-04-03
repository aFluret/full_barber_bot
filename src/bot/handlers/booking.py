"""
/**
 * @file: booking.py
 * @description: FSM-логика сценария записи клиента (дата/время/подтверждение)
 * @dependencies: app.services.booking_service, bot.keyboards.booking, bot.handlers.states
 * @created: 2026-03-23
 */
"""

from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from src.app.services.booking_service import (
    BookingAlreadyExistsError,
    BookingService,
)
from src.infra.db.repositories.appointments_repository import SlotUnavailableError
from src.infra.db.repositories.users_repository import UsersRepository
from src.infra.db.repositories.services_repository import ServicesRepository
from src.bot.handlers.states import BookingStates
from src.bot.keyboards.booking import (
    categories_picker_keyboard,
    confirm_booking_keyboard,
    services_picker_keyboard,
    time_picker_keyboard,
)
from src.bot.callback_safe import safe_callback_answer
from src.bot.keyboards.calendar import build_calendar_keyboard
from src.bot.keyboards.main_menu import menu_keyboard_for_role
from src.infra.config.settings import get_settings

router = Router()
booking_service = BookingService()
users_repo = UsersRepository()
services_repo = ServicesRepository()

SERVICE_CATEGORIES: dict[str, list[str]] = {
    "cuts": [
        "Мужская стрижка",
        "Мужская удлинённая",
        "Детская стрижка",
        "Отец + Сын",
    ],
    "beard": [
        "Оформление бороды и усов",
        "Тонировка бороды и усов",
    ],
    "combo": [
        "Комплекс",
        "Удаление волос воском (3 зоны)",
        "Укладка волос (без стрижки)",
    ],
}

CATEGORY_LABELS: dict[str, str] = {
    "cuts": "Стрижки",
    "beard": "Борода и усы",
    "combo": "Комплексные услуги",
}

OREDR_CATEGORY_KEYS: list[str] = ["cuts", "beard", "combo"]
RU_WEEKDAY_FULL = {
    0: "понедельник",
    1: "вторник",
    2: "среда",
    3: "четверг",
    4: "пятница",
    5: "суббота",
    6: "воскресенье",
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

CALENDAR_MONTH_CACHE_TTL_SECONDS = 20.0
_calendar_month_cache: dict[tuple[int, int, int], tuple[float, list[date]]] = {}


def _cleanup_calendar_cache(now_mono: float) -> None:
    if len(_calendar_month_cache) < 128:
        return
    expired = [
        key
        for key, (cached_at, _) in _calendar_month_cache.items()
        if now_mono - cached_at > CALENDAR_MONTH_CACHE_TTL_SECONDS
    ]
    for key in expired:
        _calendar_month_cache.pop(key, None)


def _category_services(services: list, category_key: str) -> list:
    names = set(SERVICE_CATEGORIES.get(category_key) or [])
    return [s for s in services if getattr(s, "name", None) in names]


def _build_categories_present(services: list) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for key in OREDR_CATEGORY_KEYS:
        if _category_services(services, key):
            out.append((key, CATEGORY_LABELS[key]))
    return out


def _human_booking_date(d: date) -> str:
    today = date.today()
    if d == today:
        suffix = "сегодня"
    elif d == today + timedelta(days=1):
        suffix = "завтра"
    else:
        suffix = RU_WEEKDAY_FULL[d.weekday()]
    return f"{d.day} {RU_MONTHS_GEN[d.month]} ({suffix})"


def _local_today() -> date:
    settings = get_settings()
    tz = ZoneInfo(settings.app_timezone or "Europe/Minsk")
    return datetime.now(tz).date()


def _month_delta(year: int, month: int, delta: int) -> tuple[int, int]:
    base = year * 12 + (month - 1) + delta
    return base // 12, (base % 12) + 1


async def _build_booked_days_for_month(
    *,
    year: int,
    month: int,
    service_id: int,
) -> list[date]:
    cache_key = (int(service_id), int(year), int(month))
    now_mono = time.monotonic()
    _cleanup_calendar_cache(now_mono)
    cached = _calendar_month_cache.get(cache_key)
    if cached is not None:
        cached_at, cached_days = cached
        if now_mono - cached_at <= CALENDAR_MONTH_CACHE_TTL_SECONDS:
            return list(cached_days)

    out = await booking_service.dates_without_available_slots_in_month(
        year=year, month=month, service_id=service_id
    )
    _calendar_month_cache[cache_key] = (now_mono, list(out))
    return out


async def _render_calendar(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    year: int,
    month: int,
    title: str = "Выбери дату для записи:",
) -> None:
    data = await state.get_data()
    service_id = data.get("booking_service_id")
    if not service_id:
        await _safe_edit_booking_message(callback, "Сначала выбери услугу.")
        return
    booked_days = await _build_booked_days_for_month(year=year, month=month, service_id=int(service_id))
    await state.update_data(calendar_year=year, calendar_month=month)
    await _safe_edit_booking_message(
        callback,
        title,
        reply_markup=build_calendar_keyboard(year, month, booked_days),
    )


async def process_calendar_callback(callback: CallbackQuery, data: str):
    if data in {"bk_cal_noop", "bk_cal_dis", "bk_cal_unavailable"}:
        await safe_callback_answer(callback)
        return {"kind": "unavailable"}
    if data.startswith("bk_cal_nav:"):
        payload = data.split(":", 1)[1]
        try:
            y_str, m_str = payload.split("-", 1)
            y = int(y_str)
            m = int(m_str)
        except Exception:
            await safe_callback_answer(callback, "Некорректная навигация", show_alert=True)
            return {"kind": "invalid"}
        return {"kind": "nav", "year": y, "month": m}
    if data.startswith("bk_cal:"):
        payload = data.split(":", 1)[1]
        try:
            target = date.fromisoformat(payload)
        except ValueError:
            await safe_callback_answer(callback, "Некорректная дата", show_alert=True)
            return {"kind": "invalid"}
        return {"kind": "date", "date": target}
    return {"kind": "ignore"}


async def _safe_edit_booking_message(
    callback: CallbackQuery,
    text: str,
    reply_markup=None,
) -> None:
    # Переиспользуем одно сообщение, чтобы не засорять чат.
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        # Частый кейс: "message is not modified" или сообщение уже недоступно.
        if "message is not modified" in str(e).lower():
            return
        await callback.message.answer(text, reply_markup=reply_markup)


@router.message(F.text == "📅 Записаться")
async def start_booking(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id
    existing = await booking_service.get_user(user_id)
    if existing is None:
        await message.answer("Сначала пройдите регистрацию: нажмите /start.")
        return

    await state.set_state(BookingStates.waiting_category)

    services = await services_repo.list_all()
    if not services:
        await message.answer(
            "Сейчас запись недоступна: администратор еще не добавил услуги.\n"
            "Напишите администратору и попробуйте позже."
        )
        return

    categories = _build_categories_present(services)
    if not categories:
        await message.answer("Список услуг недоступен.")
        return

    prompt = await message.answer(
        "Выбери категорию ✂️",
        reply_markup=categories_picker_keyboard(categories),
    )
    await state.update_data(booking_prompt_message_id=prompt.message_id)


@router.callback_query(F.data.startswith("bk_cat:"))
async def choose_category(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != BookingStates.waiting_category.state:
        await safe_callback_answer(callback, "Сначала выберите категорию.", show_alert=True)
        return

    payload = callback.data.split(":", 1)[1].strip()
    if payload not in SERVICE_CATEGORIES:
        await safe_callback_answer(callback, "Некорректная категория.", show_alert=True)
        return

    await state.update_data(booking_category_key=payload)
    await state.set_state(BookingStates.waiting_service)

    services = await services_repo.list_all()
    cat_services = _category_services(services, payload)
    if not cat_services:
        await _safe_edit_booking_message(callback, "В этой категории услуги недоступны.")
        await safe_callback_answer(callback)
        return

    await _safe_edit_booking_message(
        callback,
        "Выбери услугу ✂️",
        reply_markup=services_picker_keyboard(cat_services, back_callback_data="bk_back:category"),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "bk_back:menu")
async def back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.bot.send_message(
        chat_id=callback.message.chat.id,
        text="Выберите действие в меню ниже.",
        reply_markup=menu_keyboard_for_role("client"),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "bk_restart_service")
async def restart_booking_from_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user_id = callback.from_user.id
    existing = await booking_service.get_user(user_id)
    if existing is None:
        await callback.message.answer("Сначала пройдите регистрацию: нажмите /start.")
        await safe_callback_answer(callback)
        return

    services = await services_repo.list_all()
    if not services:
        await callback.message.answer(
            "Сейчас запись недоступна: администратор еще не добавил услуги.\n"
            "Напишите администратору и попробуйте позже."
        )
        await safe_callback_answer(callback)
        return

    categories = _build_categories_present(services)
    if not categories:
        await callback.message.answer("Список услуг недоступен.")
        await safe_callback_answer(callback)
        return

    await state.set_state(BookingStates.waiting_category)
    await callback.message.answer(
        "Выбери категорию ✂️",
        reply_markup=categories_picker_keyboard(categories),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "bk_back:category")
async def back_to_category(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() not in {BookingStates.waiting_service.state, BookingStates.waiting_date.state}:
        await safe_callback_answer(callback, "Сначала выберите категорию.", show_alert=True)
        return

    await state.set_state(BookingStates.waiting_category)
    services = await services_repo.list_all()
    categories = _build_categories_present(services)

    await _safe_edit_booking_message(
        callback,
        "Выбери категорию ✂️",
        reply_markup=categories_picker_keyboard(categories),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "bk_back:date")
async def back_to_date(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != BookingStates.waiting_time.state:
        await safe_callback_answer(callback, "Сначала выбери время.", show_alert=True)
        return

    data = await state.get_data()
    service_id = data.get("booking_service_id")
    if not service_id:
        await safe_callback_answer(callback, "Сначала выбери услугу.", show_alert=True)
        return

    await state.set_state(BookingStates.waiting_date)
    await safe_callback_answer(callback)
    data = await state.get_data()
    booking_date_iso = data.get("booking_date")
    if isinstance(booking_date_iso, str):
        selected = date.fromisoformat(booking_date_iso)
        cal_year, cal_month = selected.year, selected.month
    else:
        today = _local_today()
        cal_year, cal_month = today.year, today.month
    await _render_calendar(callback, state, year=cal_year, month=cal_month)


@router.callback_query(F.data.startswith("bk_service:"))
async def choose_service(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != BookingStates.waiting_service.state:
        await safe_callback_answer(callback, "Сначала выберите категорию.", show_alert=True)
        return

    payload = callback.data.split(":", 1)[1].strip()
    try:
        service_id = int(payload)
    except ValueError:
        await safe_callback_answer(callback, "Некорректная услуга.", show_alert=True)
        return

    await state.update_data(booking_service_id=service_id)
    await state.set_state(BookingStates.waiting_date)
    await safe_callback_answer(callback)
    today = _local_today()
    await _render_calendar(callback, state, year=today.year, month=today.month)


@router.callback_query(F.data.startswith("bk_cal"))
async def choose_date(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != BookingStates.waiting_date.state:
        await safe_callback_answer(callback, "Сначала выбери дату.", show_alert=True)
        return

    parsed = await process_calendar_callback(callback, callback.data)
    kind = parsed.get("kind")
    if kind in ("unavailable", "invalid"):
        return
    # Снимаем "часики" сразу, дальше может идти тяжелый рендер календаря.
    await safe_callback_answer(callback)
    if kind == "nav":
        nav_year = int(parsed["year"])
        nav_month = int(parsed["month"])
        today = _local_today()
        max_year, max_month = _month_delta(today.year, today.month, 3)
        if (nav_year, nav_month) < (today.year, today.month) or (nav_year, nav_month) > (max_year, max_month):
            return
        await _render_calendar(callback, state, year=nav_year, month=nav_month)
        return
    if kind != "date":
        return
    target_date = parsed["date"]

    now_date = _local_today()
    max_year, max_month = _month_delta(now_date.year, now_date.month, 3)
    if target_date < now_date or (target_date.year, target_date.month) > (max_year, max_month):
        return

    await state.update_data(booking_date=target_date.isoformat())
    await state.set_state(BookingStates.waiting_time)

    data = await state.get_data()
    service_id = data.get("booking_service_id")
    if not service_id:
        await _safe_edit_booking_message(callback, "Сначала выбери услугу.")
        return

    slots = await booking_service.list_available_time_slots(target_date, service_id=int(service_id))
    if not slots:
        await state.set_state(BookingStates.waiting_date)
        await _render_calendar(
            callback,
            state,
            year=target_date.year,
            month=target_date.month,
            title="На выбранную дату свободных мест нет. Выбери другую дату:",
        )
        return

    await _safe_edit_booking_message(
        callback,
        f"Дата: {target_date.strftime('%d.%m.%Y')}\nВыбери время для записи:",
        reply_markup=time_picker_keyboard(slots, back_callback_data="bk_back:date"),
    )


@router.callback_query(F.data.startswith("bk_time:"))
async def choose_time(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != BookingStates.waiting_time.state:
        await safe_callback_answer(callback, "Сначала выбери время.", show_alert=True)
        return

    time_slot = callback.data.split(":", 1)[1].strip()
    if not time_slot:
        await safe_callback_answer(callback, "Некорректное время.", show_alert=True)
        return

    data = await state.get_data()
    booking_date_iso = data.get("booking_date")
    if not booking_date_iso:
        await safe_callback_answer(callback, "Сначала выбери дату.", show_alert=True)
        return

    await state.update_data(booking_time=time_slot)
    await state.set_state(BookingStates.waiting_confirm)

    booking_date = date.fromisoformat(str(booking_date_iso))
    await _safe_edit_booking_message(
        callback,
        f"Подтверди запись:\n{_human_booking_date(booking_date)} в {time_slot}",
        reply_markup=confirm_booking_keyboard(),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("bk_confirm:"))
async def confirm_or_back(callback: CallbackQuery, state: FSMContext) -> None:
    action = callback.data.split(":", 1)[1]
    data = await state.get_data()

    booking_date_iso = data.get("booking_date")
    booking_time = data.get("booking_time")
    service_id = data.get("booking_service_id")
    if not booking_date_iso:
        await safe_callback_answer(callback, "Сначала выбери дату.", show_alert=True)
        return

    booking_date = date.fromisoformat(str(booking_date_iso))

    if action == "0":
        # Назад к выбору времени.
        await state.set_state(BookingStates.waiting_time)
        if not service_id:
            await safe_callback_answer(callback, "Сначала выбери услугу.", show_alert=True)
            return
        slots = await booking_service.list_available_time_slots(booking_date, service_id=int(service_id))
        if not slots:
            await state.set_state(BookingStates.waiting_date)
            await _render_calendar(
                callback,
                state,
                year=booking_date.year,
                month=booking_date.month,
                title="Свободных мест больше нет. Выбери другую дату:",
            )
        else:
            await _safe_edit_booking_message(
                callback,
                f"Дата: {booking_date.strftime('%d.%m.%Y')}\nВыбери время для записи:",
                reply_markup=time_picker_keyboard(slots, back_callback_data="bk_back:date"),
            )
        await safe_callback_answer(callback)
        return

    if action != "1":
        await safe_callback_answer(callback)
        return

    if not booking_time:
        await safe_callback_answer(callback, "Сначала выбери время.", show_alert=True)
        return

    if not service_id:
        await safe_callback_answer(callback, "Сначала выбери услугу.", show_alert=True)
        return

    try:
        appointment = await booking_service.create_appointment(
            user_id=callback.from_user.id,
            target_date=booking_date,
            service_id=int(service_id),
            time_slot_hhmm=str(booking_time),
        )
    except BookingAlreadyExistsError as e:
        await _safe_edit_booking_message(callback, str(e))
        await safe_callback_answer(callback)
        return
    except SlotUnavailableError:
        # Слот мог стать занятым между отображением и подтверждением.
        if not service_id:
            await safe_callback_answer(callback, "Сначала выбери услугу.", show_alert=True)
            return
        slots = await booking_service.list_available_time_slots(booking_date, service_id=int(service_id))
        if slots:
            await _safe_edit_booking_message(
                callback,
                "Место уже занято. Выбери другое время:",
                reply_markup=time_picker_keyboard(slots, back_callback_data="bk_back:date"),
            )
        else:
            await state.set_state(BookingStates.waiting_date)
            await _render_calendar(
                callback,
                state,
                year=booking_date.year,
                month=booking_date.month,
                title="Место уже занято, а свободных мест на эту дату больше нет. Выбери другую дату:",
            )
        await safe_callback_answer(callback)
        return
    except Exception:
        # Чтобы пользователь не видел "тишину" при внутренних сбоях.
        await _safe_edit_booking_message(
            callback,
            "Произошла ошибка при создании записи. Попробуй ещё раз.",
        )
        await safe_callback_answer(callback)
        return

    await state.clear()
    user = await booking_service.get_user(callback.from_user.id)
    user_name = user.name if user and user.name else "Клиент"
    try:
        await callback.message.delete()
    except Exception:
        pass

    service = await services_repo.get_by_id(appointment.service_id)
    service_name = service.name if service is not None else f"Услуга #{appointment.service_id}"
    service_price = service.price_byn if service is not None else 0

    # Уведомляем администраторов сразу после успешного подтверждения записи.
    admins = await users_repo.list_admins()
    if admins and user is not None:
        service_text = (
            f"{service.name} — {service.price_byn} BYN" if service is not None else f"Услуга #{appointment.service_id}"
        )
        notify_text = (
            "🔥 Новая запись\n\n"
            f"Клиент: {user.name}\n"
            f"Время: {appointment.start_time.strftime('%H:%M')}–{appointment.end_time.strftime('%H:%M')}\n"
            f"Услуга: {service_text}\n"
            f"Номер телефона: {user.phone}"
        )
        await asyncio.gather(
            *[
                callback.bot.send_message(chat_id=admin.user_id, text=notify_text)
                for admin in admins
            ],
            return_exceptions=True,
        )

    await callback.message.answer(
        "Готово! Ты записан ✅\n\n"
        f"Услуга: {service_name}\n"
        f"Дата: {_human_booking_date(appointment.date)}\n"
        f"Время: {appointment.start_time.strftime('%H:%M')}\n"
        f"Стоимость: {service_price} BYN\n\n"
        "Если планы изменятся — напиши заранее\n"
        "До встречи! ✂️",
        reply_markup=menu_keyboard_for_role(user.role if user else "client"),
    )
    await safe_callback_answer(callback)

