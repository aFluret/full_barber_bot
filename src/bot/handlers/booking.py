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
from src.infra.db.repositories.branches_repository import BranchesRepository
from src.infra.db.repositories.masters_repository import MastersRepository
from src.bot.handlers.states import BookingStates
from src.bot.keyboards.booking import (
    branches_picker_keyboard,
    categories_picker_keyboard,
    comment_choice_keyboard,
    confirm_booking_keyboard,
    masters_picker_keyboard,
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
branches_repo = BranchesRepository()
masters_repo = MastersRepository()

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


def _normalize_comment(raw: str | None) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    return value[:300]


async def _build_booking_confirm_text(
    *,
    booking_date: date,
    booking_time: str,
    service_id: int,
    comment: str,
    branch_name: str,
    master_name: str,
) -> str:
    service = await services_repo.get_by_id(service_id)
    service_name = service.name if service is not None else f"Услуга #{service_id}"
    service_price = f"{service.price_byn} BYN" if service is not None else "уточняется"
    comment_text = comment if comment else "без комментария"
    return (
        "Подтверди запись:\n"
        f"Филиал: {branch_name}\n"
        f"Мастер: {master_name}\n"
        f"Дата: {_human_booking_date(booking_date)}\n"
        f"Время: {booking_time}\n"
        f"Услуга: {service_name}\n"
        f"Стоимость: {service_price}\n"
        f"Комментарий: {comment_text}"
    )


def _parse_csv_items(raw: str) -> list[str]:
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def _mode_is_barbershop() -> bool:
    return (get_settings().booking_mode or "").strip().lower() == "barbershop"


async def _branch_records() -> list[tuple[int, str]]:
    rows = await branches_repo.list_active()
    if not rows:
        options = _parse_csv_items(get_settings().branches_csv) or ["Основной филиал"]
        return [(idx + 1, name) for idx, name in enumerate(options)]
    return [(row.id, row.name) for row in rows]


async def _master_records(branch_id: int | None = None) -> list[tuple[int, str, str]]:
    rows = await masters_repo.list_active(branch_id=branch_id)
    if not rows:
        options = _parse_csv_items(get_settings().masters_csv) or ["Илья"]
        return [(idx + 1, f"m{idx + 1}", name) for idx, name in enumerate(options)]
    return [(row.id, row.master_key, row.name) for row in rows]


def _category_back_callback(data: dict) -> str:
    if data.get("booking_has_master_step"):
        return "bk_back:master"
    if data.get("booking_has_branch_step"):
        return "bk_back:branch"
    return "bk_back:menu"


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
    master_key: str | None = None,
) -> list[date]:
    cache_key = (int(service_id), int(year), int(month), str(master_key or "all"))
    now_mono = time.monotonic()
    _cleanup_calendar_cache(now_mono)
    cached = _calendar_month_cache.get(cache_key)
    if cached is not None:
        cached_at, cached_days = cached
        if now_mono - cached_at <= CALENDAR_MONTH_CACHE_TTL_SECONDS:
            return list(cached_days)

    out = await booking_service.dates_without_available_slots_in_month(
        year=year,
        month=month,
        service_id=service_id,
        master_key=master_key if master_key and master_key != "any" else None,
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
    master_key = str(data.get("booking_master_key") or "")
    if not service_id:
        await _safe_edit_booking_message(callback, "Сначала выбери услугу.")
        return
    if master_key == "any":
        unavailable_sets: list[set[date]] = []
        for _, mk, _ in await _master_records():
            days = await _build_booked_days_for_month(
                year=year,
                month=month,
                service_id=int(service_id),
                master_key=mk,
            )
            unavailable_sets.append(set(days))
        booked_days = list(set.intersection(*unavailable_sets)) if unavailable_sets else []
    else:
        booked_days = await _build_booked_days_for_month(
            year=year,
            month=month,
            service_id=int(service_id),
            master_key=master_key if master_key else None,
        )
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


async def _show_category_step_message(message: Message, state: FSMContext) -> None:
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

    data = await state.get_data()
    await state.set_state(BookingStates.waiting_category)
    prompt = await message.answer(
        "Выбери категорию ✂️",
        reply_markup=categories_picker_keyboard(categories, back_callback_data=_category_back_callback(data)),
    )
    await state.update_data(booking_prompt_message_id=prompt.message_id)


async def _show_category_step_callback(callback: CallbackQuery, state: FSMContext) -> None:
    services = await services_repo.list_all()
    categories = _build_categories_present(services)
    if not categories:
        await _safe_edit_booking_message(callback, "Список услуг недоступен.")
        return
    data = await state.get_data()
    await state.set_state(BookingStates.waiting_category)
    await _safe_edit_booking_message(
        callback,
        "Выбери категорию ✂️",
        reply_markup=categories_picker_keyboard(categories, back_callback_data=_category_back_callback(data)),
    )


async def _start_booking_flow_message(message: Message, state: FSMContext) -> None:
    branch_records = await _branch_records()
    master_records = await _master_records()
    is_barbershop = _mode_is_barbershop()

    has_branch_step = is_barbershop and len(branch_records) > 1
    has_master_step = is_barbershop and len(master_records) > 1

    selected_branch_id, selected_branch = branch_records[0]
    selected_master_id, selected_master_key, selected_master = master_records[0]
    if has_master_step and get_settings().enable_any_master_option:
        selected_master_id = 0
        selected_master_key = "any"
        selected_master = "Любой мастер"

    await state.update_data(
        booking_has_branch_step=has_branch_step,
        booking_has_master_step=has_master_step,
        booking_branch_id=selected_branch_id,
        booking_branch=selected_branch,
        booking_master_id=selected_master_id if selected_master_id else None,
        booking_master_key=selected_master_key,
        booking_master=selected_master,
    )

    if has_branch_step:
        await state.set_state(BookingStates.waiting_branch)
        await message.answer(
            "Выбери филиал:",
            reply_markup=branches_picker_keyboard([name for _, name in branch_records]),
        )
        return

    if has_master_step and len(master_records) > 1:
        await state.set_state(BookingStates.waiting_master)
        await message.answer(
            "Выбери мастера:",
            reply_markup=masters_picker_keyboard(
                [name for _, name in master_records],
                include_any=get_settings().enable_any_master_option,
            ),
        )
        return

    await _show_category_step_message(message, state)


@router.message(F.text == "📅 Записаться")
async def start_booking(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id
    existing = await booking_service.get_user(user_id)
    if existing is None:
        await message.answer("Сначала пройдите регистрацию: нажмите /start.")
        return

    await _start_booking_flow_message(message, state)


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


@router.callback_query(F.data.startswith("bk_branch:"))
async def choose_branch(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != BookingStates.waiting_branch.state:
        await safe_callback_answer(callback, "Сначала начни запись заново.", show_alert=True)
        return

    payload = callback.data.split(":", 1)[1].strip()
    try:
        idx = int(payload)
    except ValueError:
        await safe_callback_answer(callback, "Некорректный филиал.", show_alert=True)
        return

    branch_records = await _branch_records()
    if idx < 0 or idx >= len(branch_records):
        await safe_callback_answer(callback, "Некорректный филиал.", show_alert=True)
        return

    selected_branch_id, selected_branch_name = branch_records[idx]
    await state.update_data(booking_branch_id=selected_branch_id, booking_branch=selected_branch_name)
    data = await state.get_data()
    has_master_step = bool(data.get("booking_has_master_step"))
    master_records = await _master_records(branch_id=selected_branch_id)
    if has_master_step and len(master_records) > 1:
        await state.set_state(BookingStates.waiting_master)
        await _safe_edit_booking_message(
            callback,
            "Выбери мастера:",
            reply_markup=masters_picker_keyboard(
                [name for _, _, name in master_records],
                include_any=get_settings().enable_any_master_option,
            ),
        )
    else:
        default_id, default_key, default_master = (
            master_records[0] if master_records else (0, "any", "Любой мастер")
        )
        if has_master_step and get_settings().enable_any_master_option:
            default_id = 0
            default_key = "any"
            default_master = "Любой мастер"
        await state.update_data(
            booking_master_id=default_id if default_id else None,
            booking_master_key=default_key,
            booking_master=default_master,
        )
        await _show_category_step_callback(callback, state)
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("bk_master:"))
async def choose_master(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != BookingStates.waiting_master.state:
        await safe_callback_answer(callback, "Сначала начни запись заново.", show_alert=True)
        return

    payload = callback.data.split(":", 1)[1].strip()
    if payload == "any":
        await state.update_data(booking_master_key="any", booking_master="Любой мастер")
        await _show_category_step_callback(callback, state)
        await safe_callback_answer(callback)
        return

    try:
        idx = int(payload)
    except ValueError:
        await safe_callback_answer(callback, "Некорректный мастер.", show_alert=True)
        return

    data = await state.get_data()
    branch_id_raw = data.get("booking_branch_id")
    branch_id = int(branch_id_raw) if branch_id_raw is not None else None
    master_records = await _master_records(branch_id=branch_id)
    if idx < 0 or idx >= len(master_records):
        await safe_callback_answer(callback, "Некорректный мастер.", show_alert=True)
        return

    master_id, master_key, master_name = master_records[idx]
    await state.update_data(
        booking_master_id=master_id,
        booking_master_key=master_key,
        booking_master=master_name,
    )
    await _show_category_step_callback(callback, state)
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

    await _start_booking_flow_message(callback.message, state)
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
        reply_markup=categories_picker_keyboard(categories, back_callback_data=_category_back_callback(await state.get_data())),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "bk_back:branch")
async def back_to_branch(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() not in {BookingStates.waiting_master.state, BookingStates.waiting_category.state}:
        await safe_callback_answer(callback)
        return
    branch_records = await _branch_records()
    data = await state.get_data()
    if not data.get("booking_has_branch_step"):
        await safe_callback_answer(callback)
        return
    await state.set_state(BookingStates.waiting_branch)
    await _safe_edit_booking_message(
        callback,
        "Выбери филиал:",
        reply_markup=branches_picker_keyboard([name for _, name in branch_records]),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "bk_back:master")
async def back_to_master(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() not in {BookingStates.waiting_category.state, BookingStates.waiting_service.state}:
        await safe_callback_answer(callback)
        return
    data = await state.get_data()
    if not data.get("booking_has_master_step"):
        await safe_callback_answer(callback)
        return
    branch_id_raw = data.get("booking_branch_id")
    branch_id = int(branch_id_raw) if branch_id_raw is not None else None
    master_records = await _master_records(branch_id=branch_id)
    await state.set_state(BookingStates.waiting_master)
    await _safe_edit_booking_message(
        callback,
        "Выбери мастера:",
        reply_markup=masters_picker_keyboard(
            [name for _, _, name in master_records],
            include_any=get_settings().enable_any_master_option,
        ),
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

    master_key = str(data.get("booking_master_key") or "")
    if master_key == "any":
        branch_id_raw = data.get("booking_branch_id")
        branch_id = int(branch_id_raw) if branch_id_raw is not None else None
        masters = [(mk, name) for _, mk, name in await _master_records(branch_id=branch_id)]
        slot_map = await booking_service.list_available_slots_for_any_master(
            target_date=target_date,
            service_id=int(service_id),
            masters=masters,
        )
        slots = sorted(slot_map.keys())
        await state.update_data(
            booking_any_master_slot_map=slot_map,
            booking_master_resolved_id=None,
            booking_master_resolved_key=None,
            booking_master_resolved=None,
        )
    else:
        slots = await booking_service.list_available_time_slots(
            target_date,
            service_id=int(service_id),
            master_key=master_key or None,
        )
        await state.update_data(booking_any_master_slot_map={})
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

    master_key = str(data.get("booking_master_key") or "")
    if master_key == "any":
        slot_map_raw = data.get("booking_any_master_slot_map") or {}
        slot_map: dict[str, list[str] | tuple[str, str]] = dict(slot_map_raw)
        resolved = slot_map.get(time_slot)
        if not resolved:
            await safe_callback_answer(
                callback,
                "Это время уже неактуально для выбранных мастеров. Выбери другое.",
                show_alert=True,
            )
            return
        resolved_key, resolved_name = str(resolved[0]), str(resolved[1])
        resolved_master = await masters_repo.get_by_key(resolved_key)
        resolved_id = resolved_master.id if resolved_master is not None else None
        await state.update_data(
            booking_master_resolved_id=resolved_id,
            booking_master_resolved_key=resolved_key,
            booking_master_resolved=resolved_name,
        )
    else:
        resolved_key = master_key
        resolved_name = str(data.get("booking_master") or "—")
        resolved_id_raw = data.get("booking_master_id")
        resolved_id = int(resolved_id_raw) if resolved_id_raw is not None else None
        await state.update_data(
            booking_master_resolved_id=resolved_id,
            booking_master_resolved_key=resolved_key,
            booking_master_resolved=resolved_name,
        )

    await state.update_data(booking_time=time_slot)
    await state.set_state(BookingStates.waiting_comment)
    await safe_callback_answer(callback)
    await _safe_edit_booking_message(
        callback,
        "Хочешь добавить комментарий к записи?\n"
        "Например: «Опоздаю на 10 минут» или «Нужна стрижка + борода».",
        reply_markup=comment_choice_keyboard(),
    )


@router.callback_query(F.data.startswith("bk_comment:"))
async def choose_comment_mode(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != BookingStates.waiting_comment.state:
        await safe_callback_answer(callback, "Сначала выбери время.", show_alert=True)
        return

    action = callback.data.split(":", 1)[1].strip()
    data = await state.get_data()
    booking_date_iso = data.get("booking_date")
    booking_time = data.get("booking_time")
    service_id = data.get("booking_service_id")
    if not booking_date_iso or not booking_time or not service_id:
        await safe_callback_answer(callback, "Недостаточно данных. Начни запись заново.", show_alert=True)
        return
    booking_date = date.fromisoformat(str(booking_date_iso))

    if action == "back_time":
        await state.set_state(BookingStates.waiting_time)
        master_key = str(data.get("booking_master_key") or "")
        if master_key == "any":
            branch_id_raw = data.get("booking_branch_id")
            branch_id = int(branch_id_raw) if branch_id_raw is not None else None
            masters = [(mk, name) for _, mk, name in await _master_records(branch_id=branch_id)]
            slot_map = await booking_service.list_available_slots_for_any_master(
                target_date=booking_date,
                service_id=int(service_id),
                masters=masters,
            )
            slots = sorted(slot_map.keys())
            await state.update_data(
                booking_any_master_slot_map=slot_map,
                booking_master_resolved_id=None,
                booking_master_resolved_key=None,
                booking_master_resolved=None,
            )
        else:
            slots = await booking_service.list_available_time_slots(
                booking_date,
                service_id=int(service_id),
                master_key=master_key or None,
            )
        await safe_callback_answer(callback)
        await _safe_edit_booking_message(
            callback,
            f"Дата: {booking_date.strftime('%d.%m.%Y')}\nВыбери время для записи:",
            reply_markup=time_picker_keyboard(slots, back_callback_data="bk_back:date"),
        )
        return

    if action == "add":
        await safe_callback_answer(callback)
        await _safe_edit_booking_message(
            callback,
            "Напиши комментарий одним сообщением.\n"
            "Если комментарий не нужен, нажми кнопку «Без комментария».",
            reply_markup=comment_choice_keyboard(),
        )
        return

    if action != "skip":
        await safe_callback_answer(callback)
        return

    await state.update_data(booking_comment="")
    await state.set_state(BookingStates.waiting_confirm)
    branch_name = str(data.get("booking_branch") or "—")
    master_name = str(data.get("booking_master_resolved") or data.get("booking_master") or "—")
    confirm_text = await _build_booking_confirm_text(
        booking_date=booking_date,
        booking_time=str(booking_time),
        service_id=int(service_id),
        comment="",
        branch_name=branch_name,
        master_name=master_name,
    )
    await safe_callback_answer(callback)
    await _safe_edit_booking_message(
        callback,
        confirm_text,
        reply_markup=confirm_booking_keyboard(),
    )


@router.message(BookingStates.waiting_comment)
async def handle_booking_comment(message: Message, state: FSMContext) -> None:
    raw_comment = _normalize_comment(message.text)
    if not raw_comment:
        await message.answer("Комментарий пустой. Напиши текст или нажми «Без комментария».")
        return

    data = await state.get_data()
    booking_date_iso = data.get("booking_date")
    booking_time = data.get("booking_time")
    service_id = data.get("booking_service_id")
    if not booking_date_iso or not booking_time or not service_id:
        await message.answer("Недостаточно данных. Начни запись заново через «📅 Записаться».")
        await state.clear()
        return

    booking_date = date.fromisoformat(str(booking_date_iso))
    await state.update_data(booking_comment=raw_comment)
    await state.set_state(BookingStates.waiting_confirm)
    branch_name = str(data.get("booking_branch") or "—")
    master_name = str(data.get("booking_master_resolved") or data.get("booking_master") or "—")
    confirm_text = await _build_booking_confirm_text(
        booking_date=booking_date,
        booking_time=str(booking_time),
        service_id=int(service_id),
        comment=raw_comment,
        branch_name=branch_name,
        master_name=master_name,
    )
    await message.answer(confirm_text, reply_markup=confirm_booking_keyboard())


@router.callback_query(F.data.startswith("bk_confirm:"))
async def confirm_or_back(callback: CallbackQuery, state: FSMContext) -> None:
    action = callback.data.split(":", 1)[1]
    data = await state.get_data()

    booking_date_iso = data.get("booking_date")
    booking_time = data.get("booking_time")
    service_id = data.get("booking_service_id")
    booking_comment = _normalize_comment(data.get("booking_comment"))
    booking_branch_id_raw = data.get("booking_branch_id")
    booking_branch_id = int(booking_branch_id_raw) if booking_branch_id_raw is not None else None
    booking_branch = str(data.get("booking_branch") or "—")
    booking_master_id_raw = data.get("booking_master_resolved_id") or data.get("booking_master_id")
    booking_master_id = int(booking_master_id_raw) if booking_master_id_raw is not None else None
    booking_master = str(data.get("booking_master_resolved") or data.get("booking_master") or "—")
    booking_master_key = str(data.get("booking_master_resolved_key") or data.get("booking_master_key") or "")
    if not booking_date_iso:
        await safe_callback_answer(callback, "Сначала выбери дату.", show_alert=True)
        return

    booking_date = date.fromisoformat(str(booking_date_iso))

    if action == "0":
        # Назад к шагу комментария (а оттуда уже можно вернуться к времени).
        await state.set_state(BookingStates.waiting_comment)
        await _safe_edit_booking_message(
            callback,
            "Хочешь добавить комментарий к записи?\n"
            "Например: «Опоздаю на 10 минут» или «Нужна стрижка + борода».",
            reply_markup=comment_choice_keyboard(),
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
            branch_id=booking_branch_id,
            master_id=booking_master_id,
            branch_name=booking_branch,
            master_name=booking_master,
            master_key=(booking_master_key if booking_master_key and booking_master_key != "any" else None),
            comment=booking_comment or None,
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
        if booking_master_key == "any":
            branch_id_raw = data.get("booking_branch_id")
            branch_id = int(branch_id_raw) if branch_id_raw is not None else None
            masters = [(mk, name) for _, mk, name in await _master_records(branch_id=branch_id)]
            slot_map = await booking_service.list_available_slots_for_any_master(
                target_date=booking_date,
                service_id=int(service_id),
                masters=masters,
            )
            slots = sorted(slot_map.keys())
            await state.update_data(
                booking_any_master_slot_map=slot_map,
                booking_master_resolved_id=None,
                booking_master_resolved_key=None,
                booking_master_resolved=None,
            )
        else:
            slots = await booking_service.list_available_time_slots(
                booking_date,
                service_id=int(service_id),
                master_key=booking_master_key or None,
            )
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
        comment_text = booking_comment if booking_comment else "без комментария"
        notify_text = (
            "🔥 Новая запись\n\n"
            f"Клиент: {user.name}\n"
            f"Филиал: {booking_branch}\n"
            f"Мастер: {booking_master}\n"
            f"Время: {appointment.start_time.strftime('%H:%M')}–{appointment.end_time.strftime('%H:%M')}\n"
            f"Услуга: {service_text}\n"
            f"Номер телефона: {user.phone}\n"
            f"Комментарий: {comment_text}"
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
        f"Филиал: {booking_branch}\n"
        f"Мастер: {booking_master}\n"
        f"Услуга: {service_name}\n"
        f"Дата: {_human_booking_date(appointment.date)}\n"
        f"Время: {appointment.start_time.strftime('%H:%M')}\n"
        f"Стоимость: {service_price} BYN\n\n"
        f"Комментарий: {booking_comment if booking_comment else 'без комментария'}\n\n"
        "Если планы изменятся — напиши заранее\n"
        "До встречи! ✂️",
        reply_markup=menu_keyboard_for_role(user.role if user else "client"),
    )
    await safe_callback_answer(callback)

