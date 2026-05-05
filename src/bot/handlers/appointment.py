"""
/**
 * @file: appointment.py
 * @description: Просмотр, перенос и отмена записей пользователя
 * @dependencies: app.services.booking_service, repositories, FSM
 * @created: 2026-03-23
 */
"""

from __future__ import annotations

import asyncio
import html
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.app.services.booking_service import BookingService
from src.bot.callback_safe import safe_callback_answer
from src.bot.handlers.states import RescheduleStates
from src.bot.keyboards.calendar import RU_MONTHS_NOM, WEEKDAY_LABELS, generate_calendar
from src.bot.keyboards.main_menu import menu_keyboard_for_role
from src.infra.config.settings import get_settings
from src.infra.db.repositories.services_repository import ServicesRepository
from src.infra.db.repositories.users_repository import UsersRepository

router = Router()
booking_service = BookingService()
services_repo = ServicesRepository()
users_repo = UsersRepository()
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


def _render_template(template: str, values: dict[str, str]) -> str:
    text = (template or "").replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")
    for key, value in values.items():
        text = text.replace("{" + key + "}", html.escape(str(value)))
    return text


def _parse_master_notify_map(raw: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for chunk in (raw or "").split(","):
        pair = chunk.strip()
        if not pair:
            continue
        sep = ":" if ":" in pair else ("=" if "=" in pair else None)
        if sep is None:
            continue
        key_raw, user_id_raw = pair.split(sep, 1)
        key = key_raw.strip()
        if not key:
            continue
        try:
            out[key] = int(user_id_raw.strip())
        except ValueError:
            continue
    return out


def _resolve_master_notify_chat_id(master_key: str | None) -> int | None:
    key = (master_key or "").strip()
    if not key:
        return None
    return _parse_master_notify_map(get_settings().master_telegram_map).get(key)


def _parse_admin_user_ids(raw: str) -> list[int]:
    out: list[int] = []
    for chunk in (raw or "").split(","):
        value = chunk.strip()
        if not value:
            continue
        try:
            out.append(int(value))
        except ValueError:
            continue
    return out


async def _admin_recipient_ids() -> list[int]:
    ids = set(_parse_admin_user_ids(get_settings().admin_user_ids))
    admins = await users_repo.list_admins()
    for admin in admins:
        ids.add(int(admin.user_id))
    return sorted(ids)


async def _notify_admins(bot, text: str) -> None:
    recipient_ids = await _admin_recipient_ids()
    if not recipient_ids:
        return
    await asyncio.gather(
        *[bot.send_message(chat_id=user_id, text=text) for user_id in recipient_ids],
        return_exceptions=True,
    )


def _human_booking_date(d: date) -> str:
    today = date.today()
    if d == today:
        suffix = "сегодня"
    elif d == today + timedelta(days=1):
        suffix = "завтра"
    else:
        suffix = RU_WEEKDAY_FULL[d.weekday()]
    return f"{d.day} {RU_MONTHS_GEN[d.month]} ({suffix})"


def _status_label(status: str, target_date: date, end_time) -> str:
    settings = get_settings()
    tz = ZoneInfo(settings.app_timezone or "Europe/Minsk")
    now_local = datetime.now(tz)
    if status == "cancelled":
        return "отменена"
    if status == "completed":
        return "завершена"
    if status == "no_show":
        return "no-show"
    if status == "confirmed":
        end_dt_local = datetime.combine(target_date, end_time, tzinfo=tz)
        if end_dt_local <= now_local:
            return "завершена"
        return "активна"
    return status


def _is_active(status: str, target_date: date, end_time) -> bool:
    settings = get_settings()
    tz = ZoneInfo(settings.app_timezone or "Europe/Minsk")
    now_local = datetime.now(tz)
    if status != "confirmed":
        return False
    end_dt_local = datetime.combine(target_date, end_time, tzinfo=tz)
    return end_dt_local > now_local


@router.message(F.text.in_({"📚 Мои записи", "📋 Моя запись"}))
async def my_appointments(message: Message) -> None:
    user_id = message.from_user.id
    user = await booking_service.get_user(user_id)
    user_name = user.name if user and user.name else "Клиент"
    user_role = user.role if user else "client"

    items = await booking_service.list_user_appointments(user_id, limit=10)
    if not items:
        await message.answer(
            f"{user_name}, у тебя пока нет записей.",
            reply_markup=menu_keyboard_for_role(user_role),
        )
        return

    service_ids = {a.service_id for a in items}
    services = {}
    for sid in service_ids:
        svc = await services_repo.get_by_id(sid)
        if svc is not None:
            services[sid] = svc

    active = [a for a in items if _is_active(a.status, a.date, a.end_time)]
    history = [a for a in items if not _is_active(a.status, a.date, a.end_time)]

    lines: list[str] = [f"{user_name}, вот твои записи:\n"]
    if active:
        lines.append("✅ Активные:")
        for idx, appt in enumerate(active, start=1):
            svc = services.get(appt.service_id)
            service_name = svc.name if svc else f"Услуга #{appt.service_id}"
            branch_name = appt.branch_name or "—"
            master_name = appt.master_name or "—"
            lines.append(
                f"{idx}. {_human_booking_date(appt.date)}, {appt.start_time.strftime('%H:%M')}–{appt.end_time.strftime('%H:%M')}\n"
                f"   Филиал: {branch_name}\n"
                f"   Мастер: {master_name}\n"
                f"   Услуга: {service_name}\n"
                f"   Статус: {_status_label(appt.status, appt.date, appt.end_time)}"
            )
    else:
        lines.append("✅ Активных записей нет.")

    if history:
        lines.append("\n📚 История:")
        for idx, appt in enumerate(history[:5], start=1):
            svc = services.get(appt.service_id)
            service_name = svc.name if svc else f"Услуга #{appt.service_id}"
            branch_name = appt.branch_name or "—"
            master_name = appt.master_name or "—"
            lines.append(
                f"{idx}. {appt.date.strftime('%d.%m.%Y')} {appt.start_time.strftime('%H:%M')}\n"
                f"   Филиал: {branch_name}\n"
                f"   Мастер: {master_name}\n"
                f"   Услуга: {service_name}\n"
                f"   Статус: {_status_label(appt.status, appt.date, appt.end_time)}"
            )

    actions: list[list[InlineKeyboardButton]] = []
    if active:
        first_active = active[0]
        actions.append(
            [
                InlineKeyboardButton(text="🔄 Перенести активную", callback_data=f"ap_rs_start:{first_active.id}"),
                InlineKeyboardButton(text="❌ Отменить активную", callback_data=f"ap_cancel_prompt:{first_active.id}"),
            ]
        )
    actions.append([InlineKeyboardButton(text="🔁 Повторить прошлую запись", callback_data="bk_repeat_last")])
    actions.append([InlineKeyboardButton(text="📅 Записаться снова", callback_data="bk_restart_service")])

    await message.answer("\n".join(lines), reply_markup=menu_keyboard_for_role(user_role))
    await message.answer(
        "Быстрые действия:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=actions),
    )


@router.message(F.text == "❌ Отменить запись")
async def cancel_appointment(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    user = await booking_service.get_user(user_id)
    user_name = user.name if user and user.name else "Клиент"
    user_role = user.role if user else "client"
    await state.clear()

    appt = await booking_service.get_active_appointment(user_id)
    if appt is None:
        await message.answer(f"{user_name}, активной записи не найдено.", reply_markup=menu_keyboard_for_role(user_role))
        return

    await message.answer("Подтверди отмену записи:", reply_markup=_cancel_confirm_keyboard(appt.id))


def _cancel_confirm_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Да, отменить", callback_data=f"ap_cancel_yes:{appointment_id}"),
                InlineKeyboardButton(text="↩️ Нет", callback_data="ap_cancel_no"),
            ]
        ]
    )


@router.callback_query(F.data.startswith("ap_cancel_prompt:"))
async def cancel_prompt_from_list(callback: CallbackQuery) -> None:
    payload = callback.data.split(":", 1)[1]
    try:
        appointment_id = int(payload)
    except ValueError:
        await safe_callback_answer(callback, "Некорректная запись", show_alert=True)
        return
    appt = await booking_service.get_appointment_by_id(appointment_id)
    if appt is None or appt.user_id != callback.from_user.id or not _is_active(appt.status, appt.date, appt.end_time):
        await safe_callback_answer(callback, "Запись уже неактивна", show_alert=True)
        return
    await safe_callback_answer(callback)
    await _safe_edit(callback, "Подтверди отмену записи:", reply_markup=_cancel_confirm_keyboard(appointment_id))


@router.callback_query(F.data == "ap_cancel_no")
async def cancel_appointment_abort(callback: CallbackQuery) -> None:
    await safe_callback_answer(callback)
    try:
        await callback.message.edit_text("Отмена не выполнена. Запись сохранена.")
    except TelegramBadRequest:
        await callback.message.answer("Отмена не выполнена. Запись сохранена.")


@router.callback_query(F.data.startswith("ap_cancel_yes:"))
async def cancel_appointment_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    payload = callback.data.split(":", 1)[1]
    try:
        appointment_id = int(payload)
    except ValueError:
        await safe_callback_answer(callback, "Некорректная запись", show_alert=True)
        return

    source = await booking_service.get_appointment_by_id(appointment_id)
    if source is None or source.user_id != callback.from_user.id or source.status != "confirmed":
        await safe_callback_answer(callback, "Запись уже неактивна", show_alert=True)
        return

    await state.clear()
    appt = await booking_service.cancel_appointment_by_id(appointment_id)
    if appt is None:
        await safe_callback_answer(callback, "Запись уже неактивна", show_alert=True)
        return

    user = await booking_service.get_user(callback.from_user.id)
    role = user.role if user else "client"
    service = await services_repo.get_by_id(appt.service_id)
    service_name = service.name if service else f"Услуга #{appt.service_id}"
    admin_text = _render_template(
        get_settings().notify_admin_cancelled_text,
        {
            "client": user.name if user else "Клиент",
            "branch": appt.branch_name or "—",
            "master": appt.master_name or "—",
            "service": service_name,
            "date": _human_booking_date(appt.date),
            "time": f"{appt.start_time.strftime('%H:%M')}–{appt.end_time.strftime('%H:%M')}",
        },
    )
    await _notify_admins(callback.bot, admin_text)
    master_chat_id = _resolve_master_notify_chat_id(appt.master_key)
    if master_chat_id:
        master_text = _render_template(
            get_settings().notify_master_cancelled_text,
            {
                "client": user.name if user else "Клиент",
                "service": service_name,
                "date": _human_booking_date(appt.date),
                "time": f"{appt.start_time.strftime('%H:%M')}–{appt.end_time.strftime('%H:%M')}",
            },
        )
        try:
            await callback.bot.send_message(chat_id=master_chat_id, text=master_text)
        except Exception:
            pass

    await safe_callback_answer(callback)
    try:
        await callback.message.edit_text(_render_template(get_settings().notify_client_cancelled_text, {}))
    except TelegramBadRequest:
        await callback.message.answer(_render_template(get_settings().notify_client_cancelled_text, {}))
    await callback.message.answer(
        "Выбери действие в меню ниже.",
        reply_markup=menu_keyboard_for_role(role),
    )


async def _safe_edit(message: CallbackQuery, text: str, reply_markup=None) -> None:
    try:
        await message.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        await message.message.answer(text, reply_markup=reply_markup)


async def _build_booked_days_for_month(
    year: int,
    month: int,
    service_id: int,
    master_key: str | None = None,
) -> list[date]:
    return await booking_service.dates_without_available_slots_in_month(
        year=year,
        month=month,
        service_id=service_id,
        master_key=master_key,
    )


def _month_delta(year: int, month: int, delta: int) -> tuple[int, int]:
    base = year * 12 + (month - 1) + delta
    return base // 12, (base % 12) + 1


def _reschedule_calendar_keyboard(year: int, month: int, booked_dates: list[date]) -> InlineKeyboardMarkup:
    today = date.today()
    max_year, max_month = _month_delta(today.year, today.month, 3)
    booked_set = set(booked_dates)
    weeks = generate_calendar(year, month)

    rows: list[list[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton(text=f"{RU_MONTHS_NOM[month]} {year}", callback_data="rs_cal_noop")])
    rows.append([InlineKeyboardButton(text=w, callback_data="rs_cal_noop") for w in WEEKDAY_LABELS])

    for week in weeks:
        row: list[InlineKeyboardButton] = []
        for cell in week:
            d: date = cell["date"]
            in_month = bool(cell["in_month"])
            if not in_month:
                row.append(InlineKeyboardButton(text=" ", callback_data="rs_cal_noop"))
                continue
            inactive = d < today or d in booked_set
            if inactive:
                row.append(InlineKeyboardButton(text=f"({d.day})", callback_data="rs_cal_dis"))
            else:
                row.append(InlineKeyboardButton(text=str(d.day), callback_data=f"rs_cal:{d.isoformat()}"))
        rows.append(row)

    prev_year, prev_month = _month_delta(year, month, -1)
    next_year, next_month = _month_delta(year, month, 1)
    prev_cb = f"rs_cal_nav:{prev_year:04d}-{prev_month:02d}" if (year, month) > (today.year, today.month) else "rs_cal_noop"
    next_cb = f"rs_cal_nav:{next_year:04d}-{next_month:02d}" if (year, month) < (max_year, max_month) else "rs_cal_noop"
    rows.append(
        [
            InlineKeyboardButton(text="◀", callback_data=prev_cb),
            InlineKeyboardButton(text="▶", callback_data=next_cb),
        ]
    )
    rows.append([InlineKeyboardButton(text="← В меню", callback_data="rs_exit")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _reschedule_time_keyboard(slots: list[str]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(slots), 3):
        row = slots[i : i + 3]
        buttons.append([InlineKeyboardButton(text=slot, callback_data=f"rs_time:{slot}") for slot in row])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data="rs_back:date")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _reschedule_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Подтвердить перенос", callback_data="rs_confirm:1")],
            [InlineKeyboardButton(text="← Назад", callback_data="rs_confirm:0")],
            [InlineKeyboardButton(text="↩️ В меню", callback_data="rs_exit")],
        ]
    )


async def _render_reschedule_calendar(
    callback: CallbackQuery,
    state: FSMContext,
    year: int,
    month: int,
    title: str = "Выбери новую дату:",
) -> None:
    data = await state.get_data()
    service_id = data.get("reschedule_service_id")
    master_key = str(data.get("reschedule_master_key") or "") or None
    if not service_id:
        await _safe_edit(callback, "Не удалось определить услугу для переноса.")
        return
    booked = await _build_booked_days_for_month(year, month, int(service_id), master_key=master_key)
    await state.update_data(calendar_year=year, calendar_month=month)
    await _safe_edit(
        callback,
        title,
        reply_markup=_reschedule_calendar_keyboard(year, month, booked),
    )


@router.message(F.text == "🔄 Перенести запись")
async def start_reschedule(message: Message, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id
    active = await booking_service.get_active_appointment(user_id)
    user = await booking_service.get_user(user_id)
    user_role = user.role if user else "client"
    if active is None:
        await message.answer(
            "Активной записи для переноса не найдено.",
            reply_markup=menu_keyboard_for_role(user_role),
        )
        return

    await state.set_state(RescheduleStates.waiting_date)
    await state.update_data(
        reschedule_appointment_id=active.id,
        reschedule_service_id=active.service_id,
        reschedule_master_key=active.master_key,
    )
    today = date.today()
    booked = await _build_booked_days_for_month(
        today.year,
        today.month,
        active.service_id,
        master_key=active.master_key,
    )
    await message.answer(
        "Выбери новую дату:",
        reply_markup=_reschedule_calendar_keyboard(today.year, today.month, booked),
    )


@router.callback_query(F.data.startswith("ap_rs_start:"))
async def start_reschedule_from_list(callback: CallbackQuery, state: FSMContext) -> None:
    payload = callback.data.split(":", 1)[1]
    try:
        appointment_id = int(payload)
    except ValueError:
        await safe_callback_answer(callback, "Некорректная запись", show_alert=True)
        return
    appt = await booking_service.get_appointment_by_id(appointment_id)
    if appt is None or appt.user_id != callback.from_user.id or not _is_active(appt.status, appt.date, appt.end_time):
        await safe_callback_answer(callback, "Запись уже неактивна", show_alert=True)
        return

    await state.clear()
    await state.set_state(RescheduleStates.waiting_date)
    await state.update_data(
        reschedule_appointment_id=appt.id,
        reschedule_service_id=appt.service_id,
        reschedule_master_key=appt.master_key,
    )
    today = date.today()
    booked = await _build_booked_days_for_month(
        today.year,
        today.month,
        appt.service_id,
        master_key=appt.master_key,
    )
    await safe_callback_answer(callback)
    await _safe_edit(
        callback,
        "Выбери новую дату:",
        reply_markup=_reschedule_calendar_keyboard(today.year, today.month, booked),
    )


@router.callback_query(F.data.startswith("rs_cal"))
async def reschedule_pick_date(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != RescheduleStates.waiting_date.state:
        await safe_callback_answer(
            callback,
            "Сценарий переноса устарел. Открой «🔄 Перенести запись» заново.",
            show_alert=True,
        )
        return

    data = callback.data
    if data in {"rs_cal_noop", "rs_cal_dis", "rs_cal_unavailable"}:
        await safe_callback_answer(callback)
        return

    if data.startswith("rs_cal_nav:"):
        payload = data.split(":", 1)[1]
        try:
            y_s, m_s = payload.split("-", 1)
            year, month = int(y_s), int(m_s)
        except ValueError:
            await safe_callback_answer(callback, "Некорректная навигация", show_alert=True)
            return
        today = date.today()
        max_year, max_month = _month_delta(today.year, today.month, 3)
        if (year, month) < (today.year, today.month) or (year, month) > (max_year, max_month):
            await safe_callback_answer(callback)
            return
        await safe_callback_answer(callback)
        await _render_reschedule_calendar(callback, state, year, month)
        return

    if not data.startswith("rs_cal:"):
        return

    payload = data.split(":", 1)[1]
    try:
        target_date = date.fromisoformat(payload)
    except ValueError:
        await safe_callback_answer(callback, "Некорректная дата", show_alert=True)
        return

    state_data = await state.get_data()
    service_id = state_data.get("reschedule_service_id")
    master_key = str(state_data.get("reschedule_master_key") or "") or None
    if not service_id:
        await safe_callback_answer(callback, "Не удалось определить услугу", show_alert=True)
        return

    slots = await booking_service.list_available_time_slots(
        target_date,
        int(service_id),
        master_key=master_key,
    )
    if not slots:
        await safe_callback_answer(callback)
        await _render_reschedule_calendar(
            callback,
            state,
            target_date.year,
            target_date.month,
            title="На эту дату нет свободного времени. Выбери другую дату:",
        )
        return

    await state.update_data(reschedule_date=target_date.isoformat())
    await state.set_state(RescheduleStates.waiting_time)
    await safe_callback_answer(callback)
    await _safe_edit(
        callback,
        f"Новая дата: {target_date.strftime('%d.%m.%Y')}\nВыбери новое время:",
        reply_markup=_reschedule_time_keyboard(slots),
    )


@router.callback_query(F.data == "rs_back:date")
async def reschedule_back_to_date(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != RescheduleStates.waiting_time.state:
        await safe_callback_answer(callback)
        return
    data = await state.get_data()
    iso = data.get("reschedule_date")
    if isinstance(iso, str):
        d = date.fromisoformat(iso)
        year, month = d.year, d.month
    else:
        today = date.today()
        year, month = today.year, today.month
    await state.set_state(RescheduleStates.waiting_date)
    await safe_callback_answer(callback)
    await _render_reschedule_calendar(callback, state, year, month)


@router.callback_query(F.data == "rs_exit")
async def reschedule_exit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user = await booking_service.get_user(callback.from_user.id)
    role = user.role if user else "client"
    await safe_callback_answer(callback)
    try:
        await callback.message.delete()
    except Exception:
        pass
    await callback.message.answer(
        "Перенос отменён. Возвращаю в меню.",
        reply_markup=menu_keyboard_for_role(role),
    )


@router.callback_query(F.data.startswith("rs_time:"))
async def reschedule_pick_time(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != RescheduleStates.waiting_time.state:
        await safe_callback_answer(
            callback,
            "Сначала выберите дату для переноса.",
            show_alert=True,
        )
        return

    time_slot = callback.data.split(":", 1)[1].strip()
    if not time_slot:
        await safe_callback_answer(callback, "Некорректное время", show_alert=True)
        return

    data = await state.get_data()
    iso = data.get("reschedule_date")
    if not iso:
        await safe_callback_answer(callback, "Сначала выбери дату", show_alert=True)
        return

    target_date = date.fromisoformat(str(iso))
    await state.update_data(reschedule_time=time_slot)
    await state.set_state(RescheduleStates.waiting_confirm)
    await safe_callback_answer(callback)
    await _safe_edit(
        callback,
        f"Подтверди перенос:\n{_human_booking_date(target_date)} в {time_slot}",
        reply_markup=_reschedule_confirm_keyboard(),
    )


@router.callback_query(F.data.startswith("rs_confirm:"))
async def reschedule_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != RescheduleStates.waiting_confirm.state:
        await safe_callback_answer(
            callback,
            "Кнопка подтверждения устарела. Начните перенос заново.",
            show_alert=True,
        )
        return

    action = callback.data.split(":", 1)[1]
    data = await state.get_data()
    iso = data.get("reschedule_date")
    hhmm = data.get("reschedule_time")
    source_id = data.get("reschedule_appointment_id")
    service_id = data.get("reschedule_service_id")
    master_key = str(data.get("reschedule_master_key") or "") or None
    source = await booking_service.get_appointment_by_id(int(source_id)) if source_id else None
    if not iso or not source_id or not service_id:
        await safe_callback_answer(callback, "Недостаточно данных для переноса", show_alert=True)
        return

    target_date = date.fromisoformat(str(iso))
    if action == "0":
        await state.set_state(RescheduleStates.waiting_time)
        slots = await booking_service.list_available_time_slots(
            target_date,
            int(service_id),
            master_key=master_key,
        )
        await safe_callback_answer(callback)
        await _safe_edit(
            callback,
            f"Новая дата: {target_date.strftime('%d.%m.%Y')}\nВыбери новое время:",
            reply_markup=_reschedule_time_keyboard(slots),
        )
        return
    if action != "1":
        await safe_callback_answer(callback)
        return
    if not hhmm:
        await safe_callback_answer(callback, "Сначала выбери время", show_alert=True)
        return

    try:
        new_appointment = await booking_service.reschedule_appointment(
            user_id=callback.from_user.id,
            source_appointment_id=int(source_id),
            target_date=target_date,
            time_slot_hhmm=str(hhmm),
        )
    except Exception as exc:
        await safe_callback_answer(callback)
        await _safe_edit(
            callback,
            f"Не удалось перенести запись: {str(exc)}\nПопробуй выбрать другую дату или время.",
        )
        await state.set_state(RescheduleStates.waiting_date)
        await _render_reschedule_calendar(callback, state, target_date.year, target_date.month)
        return

    await state.clear()
    user = await booking_service.get_user(callback.from_user.id)
    role = user.role if user else "client"
    service = await services_repo.get_by_id(new_appointment.service_id)
    service_name = service.name if service else f"Услуга #{new_appointment.service_id}"
    old_date_text = _human_booking_date(source.date) if source is not None else "—"
    old_time_text = (
        f"{source.start_time.strftime('%H:%M')}–{source.end_time.strftime('%H:%M')}"
        if source is not None
        else "—"
    )
    new_date_text = _human_booking_date(new_appointment.date)
    new_time_text = f"{new_appointment.start_time.strftime('%H:%M')}–{new_appointment.end_time.strftime('%H:%M')}"
    admin_text = _render_template(
        get_settings().notify_admin_rescheduled_text,
        {
            "client": user.name if user else "Клиент",
            "branch": new_appointment.branch_name or source.branch_name if source else "—",
            "master": new_appointment.master_name or source.master_name if source else "—",
            "service": service_name,
            "old_date": old_date_text,
            "old_time": old_time_text,
            "new_date": new_date_text,
            "new_time": new_time_text,
        },
    )
    await _notify_admins(callback.bot, admin_text)
    master_chat_id = _resolve_master_notify_chat_id(new_appointment.master_key or master_key)
    if master_chat_id:
        master_text = _render_template(
            get_settings().notify_master_rescheduled_text,
            {
                "client": user.name if user else "Клиент",
                "service": service_name,
                "old_date": old_date_text,
                "old_time": old_time_text,
                "new_date": new_date_text,
                "new_time": new_time_text,
            },
        )
        try:
            await callback.bot.send_message(chat_id=master_chat_id, text=master_text)
        except Exception:
            pass

    await safe_callback_answer(callback)
    try:
        await callback.message.delete()
    except Exception:
        pass
    client_text = _render_template(
        get_settings().notify_client_rescheduled_text,
        {
            "service": service_name,
            "new_date": new_date_text,
            "new_time": new_time_text,
        },
    )
    await callback.message.answer(client_text, reply_markup=menu_keyboard_for_role(role))


@router.message(Command("no_show"))
async def mark_no_show(message: Message) -> None:
    user = await users_repo.get_by_user_id(message.from_user.id)
    if user is None or user.role != "admin":
        await message.answer("Недостаточно прав.")
        return
    parts = (message.text or "").strip().split()
    if len(parts) != 2:
        await message.answer("Использование: /no_show &lt;appointment_id&gt;")
        return
    try:
        appointment_id = int(parts[1])
    except ValueError:
        await message.answer("appointment_id должен быть числом.")
        return
    appt = await booking_service.mark_no_show_by_id(appointment_id)
    if appt is None:
        await message.answer("Не удалось установить no-show: запись не найдена или уже не active.")
        return

    client = await booking_service.get_user(appt.user_id)
    service = await services_repo.get_by_id(appt.service_id)
    service_name = service.name if service else f"Услуга #{appt.service_id}"
    date_text = _human_booking_date(appt.date)
    time_text = f"{appt.start_time.strftime('%H:%M')}–{appt.end_time.strftime('%H:%M')}"
    values = {
        "client": client.name if client else "Клиент",
        "branch": appt.branch_name or "—",
        "master": appt.master_name or "—",
        "service": service_name,
        "date": date_text,
        "time": time_text,
    }
    await _notify_admins(message.bot, _render_template(get_settings().notify_admin_no_show_text, values))
    if client is not None:
        try:
            await message.bot.send_message(
                chat_id=client.user_id,
                text=_render_template(get_settings().notify_client_no_show_text, values),
            )
        except Exception:
            pass
    master_chat_id = _resolve_master_notify_chat_id(appt.master_key)
    if master_chat_id:
        try:
            await message.bot.send_message(
                chat_id=master_chat_id,
                text=_render_template(get_settings().notify_master_no_show_text, values),
            )
        except Exception:
            pass
    await message.answer(f"No-show установлен ✅ для записи #{appointment_id}")
