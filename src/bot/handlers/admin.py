"""
/**
 * @file: admin.py
 * @description: Админ-команды просмотра записей (MVP)
 * @dependencies: infra.db.repositories, infra.config.settings
 * @created: 2026-03-23
 */
"""

from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta
from typing import Any

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.infra.config.settings import get_settings
from src.infra.db.repositories.appointments_repository import AppointmentsRepository
from src.infra.db.repositories.users_repository import UsersRepository
from src.infra.db.repositories.services_repository import ServicesRepository
from src.infra.db.repositories.work_schedule_repository import WorkScheduleRepository
from src.infra.db.repositories.masters_repository import MastersRepository
from src.infra.db.repositories.branches_repository import BranchesRepository
from src.app.services.schedule_service import ScheduleService
from src.bot.callback_safe import safe_callback_answer
from src.bot.handlers.states import AdminPanelStates, AdminScheduleStates
from src.bot.keyboards.main_menu import admin_menu_keyboard, main_menu_keyboard

router = Router()
appointments_repo = AppointmentsRepository()
users_repo = UsersRepository()
services_repo = ServicesRepository()
work_schedule_repo = WorkScheduleRepository()
masters_repo = MastersRepository()
branches_repo = BranchesRepository()
schedule_service = ScheduleService()
ADMIN_INLINE_MESSAGE_ID_KEY = "admin_inline_message_id"
MONTHLY_PREFIX = "admin_monthly"

async def _safe_edit_admin_panel(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        # Частый кейс: "message is not modified" или сообщение недоступно.
        if "message is not modified" in str(e).lower():
            return
        await callback.message.answer(text, reply_markup=reply_markup)


async def _is_admin(user_id: int) -> bool:
    user = await users_repo.get_by_user_id(user_id)
    return bool(user and user.role == "admin")


async def _ensure_admin_mode(message: Message, state: FSMContext) -> bool:
    current = await state.get_state()
    if current == AdminPanelStates.in_menu.state:
        return True
    await message.answer("Ты не в админ-панели. Напиши /admin и введи код доступа.")
    return False


async def _delete_tracked_admin_inline_message(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    message_id = data.get(ADMIN_INLINE_MESSAGE_ID_KEY)
    if isinstance(message_id, int):
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=message_id)
        except Exception:
            pass
    await state.update_data(**{ADMIN_INLINE_MESSAGE_ID_KEY: None})


def _weekday_ru(d: date) -> str:
    names = {0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс"}
    return names[d.weekday()]


def _fmt_lunch(schedule) -> str:
    if schedule is None:
        return "14:00 — 15:00"
    if schedule.lunch_time is None:
        return "без обеда"
    lunch_end = datetime.combine(date.today(), schedule.lunch_time) + timedelta(minutes=60)
    return f"{schedule.lunch_time.strftime('%H:%M')} — {lunch_end.strftime('%H:%M')}"


async def _render_day_report(target_date: date) -> str:
    appts = await appointments_repo.list_by_date_from_today(target_date)
    if not appts:
        return "На сегодня записей нет 📭" if target_date == date.today() else "На этот день записей нет 📭"

    services = await services_repo.list_all()
    services_map = {s.id: s for s in services}

    total_sum = 0
    lines: list[str] = [f"📋 Записи на {_weekday_ru(target_date)}, {target_date.strftime('%d.%m')}:\n"]
    idx = 1
    for appt in appts:
        user = await users_repo.get_by_user_id(appt.user_id)
        if user is None:
            continue
        service = services_map.get(appt.service_id)
        service_name = service.name if service is not None else f"Услуга #{appt.service_id}"
        service_price = service.price_byn if service is not None else 0
        total_sum += service_price
        lines.append(
            f"{idx}. {appt.start_time.strftime('%H:%M')} — {user.name}\n"
            f"   {service_name} — {service_price} BYN\n"
            f"   📞 {user.phone}\n"
        )
        idx += 1

    lines.append(f"━━━━━━━━━━━━━━━━━━\nВсего: {idx - 1} записи | Сумма: {total_sum} BYN")
    return "\n".join(lines)


@router.message(Command("today"))
async def today_appointments(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    if not await _ensure_admin_mode(message, state):
        return
    await _delete_tracked_admin_inline_message(message, state)
    await message.answer(await _render_day_report(date.today()))


@router.message(Command("tomorrow"))
async def tomorrow_appointments(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    if not await _ensure_admin_mode(message, state):
        return
    await _delete_tracked_admin_inline_message(message, state)
    await message.answer(await _render_day_report(date.today() + timedelta(days=1)))


@router.message(Command("all"))
async def all_future_appointments(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    if not await _ensure_admin_mode(message, state):
        return
    await _delete_tracked_admin_inline_message(message, state)

    appts = await appointments_repo.list_confirmed_from_date(date.today())
    if not appts:
        await message.answer("Будущих записей пока нет 📭")
        return

    services = await services_repo.list_all()
    services_map = {s.id: s for s in services}

    lines: list[str] = ["📋 Все будущие записи:\n"]
    total_sum = 0
    current_day: date | None = None
    for appt in appts:
        user = await users_repo.get_by_user_id(appt.user_id)
        if user is None:
            continue

        service = services_map.get(appt.service_id)
        service_name = service.name if service is not None else f"Услуга #{appt.service_id}"
        service_price = service.price_byn if service is not None else 0
        total_sum += service_price
        if current_day != appt.date:
            current_day = appt.date
            lines.append(f"\n{_weekday_ru(appt.date)}, {appt.date.strftime('%d.%m')}:")
        lines.append(
            f"  {appt.start_time.strftime('%H:%M')} — {user.name} — {service_name} — {service_price} BYN\n"
            f"  📞 {user.phone}"
        )

    lines.append(f"\n━━━━━━━━━━━━━━━━━━\nВсего: {len(appts)} записи | Сумма: {total_sum} BYN")
    await message.answer("\n".join(lines))


def _parse_hhmm_or_none(raw: str):
    try:
        return datetime.strptime(raw, "%H:%M").time()
    except ValueError:
        return None


def _parse_hhmm_compact_or_none(raw: str):
    if len(raw) != 4 or not raw.isdigit():
        return None
    return _parse_hhmm_or_none(f"{raw[:2]}:{raw[2:]}")


async def _masters_panel_text_and_keyboard() -> tuple[str, InlineKeyboardMarkup]:
    masters = await masters_repo.list_all()
    if not masters:
        return "Мастера не найдены.", InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_master:refresh")]]
        )

    lines = ["👨‍🔧 Управление мастерами:\n"]
    keyboard: list[list[InlineKeyboardButton]] = []
    for m in masters:
        status = "ON" if m.is_active else "OFF"
        lines.append(
            f"- {m.name} ({m.master_key}) [{status}] {m.work_start.strftime('%H:%M')}-{m.work_end.strftime('%H:%M')}"
        )
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{'⛔ Выключить' if m.is_active else '✅ Включить'} {m.name}",
                    callback_data=f"admin_master:toggle:{m.master_key}:{1 if m.is_active else 0}",
                )
            ]
        )
        keyboard.append(
            [
                InlineKeyboardButton(text="09-17", callback_data=f"admin_master:set:{m.master_key}:0900:1700"),
                InlineKeyboardButton(text="10-18", callback_data=f"admin_master:set:{m.master_key}:1000:1800"),
                InlineKeyboardButton(text="12-20", callback_data=f"admin_master:set:{m.master_key}:1200:2000"),
            ]
        )

    lines.append("\nДля точного времени: /master_hours &lt;master_key&gt; &lt;HH:MM&gt; &lt;HH:MM&gt;")
    keyboard.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_master:refresh")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard)


async def _branches_panel_text_and_keyboard() -> tuple[str, InlineKeyboardMarkup]:
    branches = await branches_repo.list_all()
    if not branches:
        return "Филиалы не найдены.", InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_branch:refresh")]]
        )

    lines = ["🏬 Управление филиалами:\n"]
    keyboard: list[list[InlineKeyboardButton]] = []
    for b in branches:
        status = "ON" if b.is_active else "OFF"
        lines.append(f"- #{b.id} {b.name} [{status}]")
        keyboard.append(
            [
                InlineKeyboardButton(
                    text=f"{'⛔ Выключить' if b.is_active else '✅ Включить'} #{b.id} {b.name}",
                    callback_data=f"admin_branch:toggle:{b.id}:{1 if b.is_active else 0}",
                )
            ]
        )
    keyboard.append([InlineKeyboardButton(text="🔄 Обновить", callback_data="admin_branch:refresh")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=keyboard)


@router.message(Command("masters"))
async def admin_masters_list(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    if not await _ensure_admin_mode(message, state):
        return
    await _delete_tracked_admin_inline_message(message, state)

    text, kb = await _masters_panel_text_and_keyboard()
    await message.answer(text, reply_markup=kb)


@router.message(Command("master_on"))
async def admin_master_on(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    if not await _ensure_admin_mode(message, state):
        return
    parts = (message.text or "").strip().split()
    if len(parts) != 2:
        await message.answer("Использование: /master_on &lt;master_key&gt;")
        return
    ok = await masters_repo.set_active(parts[1], True)
    await message.answer("Мастер включен ✅" if ok else "Не удалось включить мастера.")


@router.message(Command("master_off"))
async def admin_master_off(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    if not await _ensure_admin_mode(message, state):
        return
    parts = (message.text or "").strip().split()
    if len(parts) != 2:
        await message.answer("Использование: /master_off &lt;master_key&gt;")
        return
    ok = await masters_repo.set_active(parts[1], False)
    await message.answer("Мастер выключен ✅" if ok else "Не удалось выключить мастера.")


@router.message(Command("master_hours"))
async def admin_master_hours(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    if not await _ensure_admin_mode(message, state):
        return
    parts = (message.text or "").strip().split()
    if len(parts) != 4:
        await message.answer("Использование: /master_hours &lt;master_key&gt; &lt;HH:MM&gt; &lt;HH:MM&gt;")
        return
    master_key = parts[1].strip()
    start_t = _parse_hhmm_or_none(parts[2].strip())
    end_t = _parse_hhmm_or_none(parts[3].strip())
    if start_t is None or end_t is None:
        await message.answer("Некорректный формат времени. Пример: 10:00 18:00")
        return
    if start_t >= end_t:
        await message.answer("Время начала должно быть меньше времени окончания.")
        return
    ok = await masters_repo.set_work_hours(master_key, start_t, end_t)
    await message.answer(
        f"График мастера {master_key} обновлен ✅: {start_t.strftime('%H:%M')}–{end_t.strftime('%H:%M')}"
        if ok
        else "Не удалось обновить график мастера."
    )


@router.message(Command("branches"))
async def admin_branches_list(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    if not await _ensure_admin_mode(message, state):
        return
    await _delete_tracked_admin_inline_message(message, state)

    text, kb = await _branches_panel_text_and_keyboard()
    await message.answer(text, reply_markup=kb)


@router.message(Command("branch_on"))
async def admin_branch_on(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    if not await _ensure_admin_mode(message, state):
        return
    parts = (message.text or "").strip().split()
    if len(parts) != 2:
        await message.answer("Использование: /branch_on &lt;id&gt;")
        return
    try:
        branch_id = int(parts[1])
    except ValueError:
        await message.answer("ID филиала должен быть числом.")
        return
    ok = await branches_repo.set_active(branch_id, True)
    await message.answer("Филиал включен ✅" if ok else "Не удалось включить филиал.")


@router.message(Command("branch_off"))
async def admin_branch_off(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    if not await _ensure_admin_mode(message, state):
        return
    parts = (message.text or "").strip().split()
    if len(parts) != 2:
        await message.answer("Использование: /branch_off &lt;id&gt;")
        return
    try:
        branch_id = int(parts[1])
    except ValueError:
        await message.answer("ID филиала должен быть числом.")
        return
    ok = await branches_repo.set_active(branch_id, False)
    await message.answer("Филиал выключен ✅" if ok else "Не удалось выключить филиал.")


@router.callback_query(F.data == "admin_master:refresh")
async def admin_master_refresh(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await safe_callback_answer(callback, "Сначала войдите в админ-панель через /admin", show_alert=True)
        return
    text, kb = await _masters_panel_text_and_keyboard()
    await _safe_edit_admin_panel(callback, text, reply_markup=kb)
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("admin_master:toggle:"))
async def admin_master_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await safe_callback_answer(callback, "Сначала войдите в админ-панель через /admin", show_alert=True)
        return
    try:
        _, _, master_key, current = callback.data.split(":")
        current_is_on = bool(int(current))
    except Exception:
        await safe_callback_answer(callback, "Некорректная команда", show_alert=True)
        return
    await masters_repo.set_active(master_key, not current_is_on)
    text, kb = await _masters_panel_text_and_keyboard()
    await _safe_edit_admin_panel(callback, text, reply_markup=kb)
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("admin_master:set:"))
async def admin_master_set_hours_preset(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await safe_callback_answer(callback, "Сначала войдите в админ-панель через /admin", show_alert=True)
        return
    try:
        _, _, master_key, start_raw, end_raw = callback.data.split(":")
    except Exception:
        await safe_callback_answer(callback, "Некорректная команда", show_alert=True)
        return
    start_t = _parse_hhmm_compact_or_none(start_raw)
    end_t = _parse_hhmm_compact_or_none(end_raw)
    if start_t is None or end_t is None or start_t >= end_t:
        await safe_callback_answer(callback, "Некорректный диапазон времени", show_alert=True)
        return
    await masters_repo.set_work_hours(master_key, start_t, end_t)
    text, kb = await _masters_panel_text_and_keyboard()
    await _safe_edit_admin_panel(callback, text, reply_markup=kb)
    await safe_callback_answer(callback)


@router.callback_query(F.data == "admin_branch:refresh")
async def admin_branch_refresh(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await safe_callback_answer(callback, "Сначала войдите в админ-панель через /admin", show_alert=True)
        return
    text, kb = await _branches_panel_text_and_keyboard()
    await _safe_edit_admin_panel(callback, text, reply_markup=kb)
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("admin_branch:toggle:"))
async def admin_branch_toggle(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await safe_callback_answer(callback, "Сначала войдите в админ-панель через /admin", show_alert=True)
        return
    try:
        _, _, branch_id_raw, current = callback.data.split(":")
        branch_id = int(branch_id_raw)
        current_is_on = bool(int(current))
    except Exception:
        await safe_callback_answer(callback, "Некорректная команда", show_alert=True)
        return
    await branches_repo.set_active(branch_id, not current_is_on)
    text, kb = await _branches_panel_text_and_keyboard()
    await _safe_edit_admin_panel(callback, text, reply_markup=kb)
    await safe_callback_answer(callback)


@router.message(Command("schedule"))
async def show_work_schedule(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    if not await _ensure_admin_mode(message, state):
        return
    await _delete_tracked_admin_inline_message(message, state)

    schedule = await work_schedule_repo.get_latest()
    if schedule is None:
        start_time = ScheduleService.DEFAULT_START
        end_time = ScheduleService.DEFAULT_END
        wd = set(schedule_service.WORKING_WEEKDAYS)
    else:
        start_time = schedule.start_time.strftime("%H:%M")
        end_time = schedule.end_time.strftime("%H:%M")
        wd = set(schedule.weekdays)

    await message.answer(
        "⚙️ Текущий график:\n\n"
        f"Рабочие дни: {_format_workdays_line(wd)}\n"
        f"Время работы: {start_time} — {end_time}\n"
        f"Обед: {_fmt_lunch(schedule)}\n"
        f"Выходные: {_format_off_days_line(wd)}"
    )


SCHEDULE_WEEKDAY_LABELS: dict[int, str] = {
    0: "Пн",
    1: "Вт",
    2: "Ср",
    3: "Чт",
    4: "Пт",
    5: "Сб",
    6: "Вс",
}


def _format_workdays_line(weekdays: set[int] | list[int]) -> str:
    seq = sorted(weekdays)
    return ", ".join(SCHEDULE_WEEKDAY_LABELS[d] for d in seq if d in SCHEDULE_WEEKDAY_LABELS)


def _format_off_days_line(weekdays: set[int]) -> str:
    off = [d for d in range(7) if d not in weekdays]
    if not off:
        return "нет (без выходных)"
    return ", ".join(SCHEDULE_WEEKDAY_LABELS[d] for d in off)


def _schedule_weekdays_keyboard(selected: set[int]) -> InlineKeyboardMarkup:
    """
    Выбор рабочих дней недели (Пн–Вс), включая воскресенье при необходимости.
    """
    buttons: list[list[InlineKeyboardButton]] = []
    day_items = list(SCHEDULE_WEEKDAY_LABELS.keys())
    for i in range(0, len(day_items), 3):
        row_days = day_items[i : i + 3]
        row: list[InlineKeyboardButton] = []
        for d in row_days:
            is_on = d in selected
            row.append(
                InlineKeyboardButton(
                    text=f"{SCHEDULE_WEEKDAY_LABELS[d]} [{'ON' if is_on else 'OFF'}]",
                    callback_data=f"admin_schedule:toggle_weekday:{d}",
                )
            )
        buttons.append(row)

    buttons.append(
        [
            InlineKeyboardButton(
                text="Далее: Время начала",
                callback_data="admin_schedule:confirm_weekdays",
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _schedule_times_keyboard(times: list[str], kind: str) -> InlineKeyboardMarkup:
    """
    kind = 'start' | 'end'
    """
    buttons: list[list[InlineKeyboardButton]] = []
    step = 3
    for i in range(0, len(times), step):
        row = times[i : i + step]
        buttons.append(
            [
                InlineKeyboardButton(
                    text=t,
                    callback_data=f"admin_schedule:set_{kind}:{t}",
                )
                for t in row
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _schedule_entry_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Рабочие дни", callback_data="admin_schedule:edit_days")],
            [InlineKeyboardButton(text="Время начала", callback_data="admin_schedule:edit_start")],
            [InlineKeyboardButton(text="Время окончания", callback_data="admin_schedule:edit_end")],
        ]
    )


def _lunch_time_keyboard(times: list[str]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *[
                [InlineKeyboardButton(text=t, callback_data=f"admin_schedule:lunch_time:{t}")]
                for t in times
            ],
            [InlineKeyboardButton(text="Без обеда", callback_data="admin_schedule:lunch_none")],
            [InlineKeyboardButton(text="Назад к выбору времени", callback_data="admin_schedule:back_to_weekdays")],
        ]
    )


async def _open_schedule_editor(message: Message, state: FSMContext) -> None:
    schedule = await work_schedule_repo.get_latest()
    if schedule is None:
        selected_weekdays = set(schedule_service.WORKING_WEEKDAYS)
    else:
        selected_weekdays = {d for d in schedule.weekdays if d in SCHEDULE_WEEKDAY_LABELS}

    await state.clear()
    await state.set_state(AdminScheduleStates.waiting_weekdays)
    await state.update_data(schedule_weekdays=sorted(selected_weekdays))

    await message.answer(
        "Редактирование расписания. Выберите рабочие дни (Пн–Вс):",
        reply_markup=_schedule_weekdays_keyboard(selected_weekdays),
    )


async def _send_or_replace_schedule_panel(message: Message, state: FSMContext, text: str) -> None:
    await _delete_tracked_admin_inline_message(message, state)
    sent = await message.answer(text, reply_markup=_schedule_entry_keyboard())
    await state.update_data(**{ADMIN_INLINE_MESSAGE_ID_KEY: sent.message_id})


@router.message(Command("set_schedule"))
async def set_work_schedule(message: Message, state: FSMContext) -> None:
    if not await _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    if not await _ensure_admin_mode(message, state):
        return

    parts = (message.text or "").strip().split()
    # UI-вариант: без аргументов.
    if len(parts) == 1:
        await _open_month_overview_message(message, state, date.today().year, date.today().month)
        return

    # Legacy-вариант: /set_schedule 1,2,3,4,5 10:00 18:00
    if len(parts) != 4:
        await message.answer(
            "Использование:\n"
            "/set_schedule 1,2,3,4,5 10:00 18:00\n"
            "или /set_schedule (без аргументов) — UI редактирования."
        )
        return

    weekdays_csv = parts[1]
    start_s = parts[2]
    end_s = parts[3]

    try:
        weekday_nums = [int(x.strip()) for x in weekdays_csv.split(",") if x.strip()]
    except ValueError:
        await message.answer("Некорректный список дней. Пример: 1,2,3,4,5")
        return

    if not weekday_nums:
        await message.answer("Дни не указаны.")
        return

    # Валидация 1..7 и маппинг в python weekday (Пн=0..Вс=6)
    weekdays_py: set[int] = set()
    for n in weekday_nums:
        if n < 1 or n > 7:
            await message.answer("Дни должны быть в диапазоне 1..7 (1=Пн ... 7=Вс).")
            return
        weekdays_py.add(n - 1)

    try:
        start_t = datetime.strptime(start_s, "%H:%M").time()
        end_t = datetime.strptime(end_s, "%H:%M").time()
    except ValueError:
        await message.answer("Некорректное время. Пример: 10:00")
        return

    if start_t >= end_t:
        await message.answer("start_time должен быть меньше end_time.")
        return

    await work_schedule_repo.set_schedule(
        weekdays=sorted(weekdays_py),
        start_time=start_t,
        end_time=end_t,
    )

    weekdays_human = ",".join(str(d + 1) for d in sorted(weekdays_py))
    await message.answer(
        "График сохранен.\n"
        f"- days: {weekdays_human}\n"
        f"- start: {start_t.strftime('%H:%M')}\n"
        f"- end: {end_t.strftime('%H:%M')}"
    )


@router.message(Command("admin"))
async def admin_panel_entry(message: Message, state: FSMContext) -> None:
    await _delete_tracked_admin_inline_message(message, state)
    await state.set_state(AdminPanelStates.waiting_access_code)
    await message.answer("Введи код доступа 🔐")


@router.message(AdminPanelStates.waiting_access_code)
async def admin_panel_access_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip()
    settings = get_settings()
    expected = (settings.admin_panel_access_code or "").strip()

    if not code or code != expected:
        await message.answer("Неверный код ❌\nПопробуй ещё раз.")
        return

    user = await users_repo.get_by_user_id(message.from_user.id)
    if user is None:
        await message.answer("Сначала пройдите регистрацию через /start, затем повторите /admin.")
        await state.clear()
        return

    await users_repo.set_role(message.from_user.id, "admin")
    await state.set_state(AdminPanelStates.in_menu)
    await message.answer(
        f"Привет, {user.name}! 👋\n"
        "Ты вошёл в админ-панель.\n\n"
        "Вот что ты можешь делать:\n\n"
        "📋 Записи:\n"
        "/today — записи на сегодня\n"
        "/tomorrow — записи на завтра\n"
        "/all — все будущие записи\n\n"
        "⚙️ Настройки:\n"
        "/schedule — посмотреть текущий график работы\n"
        "/set_schedule — изменить график работы\n"
        "  (рабочие дни, время начала/конца, обед)\n\n"
        "👨‍🔧 Мастера:\n"
        "/masters — список мастеров и статусов\n"
        "/master_on &lt;key&gt; — включить мастера\n"
        "/master_off &lt;key&gt; — выключить мастера\n"
        "/master_hours &lt;key&gt; 10:00 18:00 — задать часы мастера\n\n"
        "🏬 Филиалы:\n"
        "/branches — список филиалов и статусов\n"
        "/branch_on &lt;id&gt; — включить филиал\n"
        "/branch_off &lt;id&gt; — выключить филиал\n\n"
        "❌ Выход:\n"
        "/exit — выйти из админки\n"
        "  и вернуться в режим клиента",
        reply_markup=admin_menu_keyboard(),
    )


@router.message(Command("exit"))
async def exit_admin_panel(message: Message, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await message.answer("Ты уже в обычном режиме.")
        return
    await _delete_tracked_admin_inline_message(message, state)
    await users_repo.set_role(message.from_user.id, "client")
    await state.clear()
    await message.answer(
        "Ты вышел из админ-панели.\nТеперь бот работает в обычном режиме 👋",
        reply_markup=main_menu_keyboard(),
    )


@router.message(AdminPanelStates.in_menu)
async def admin_panel_fallback(message: Message) -> None:
    await message.answer(
        "Ты в админ-панели.\n"
        "Используй команды из списка.\n"
        "Напиши /exit чтобы выйти."
    )


@router.callback_query(F.data == "admin_schedule:open_menu")
async def admin_schedule_open_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await safe_callback_answer(callback, "Сначала войдите в админ-панель через /admin", show_alert=True)
        return
    await _safe_edit_admin_panel(callback, "Что хочешь изменить?", reply_markup=_schedule_entry_keyboard())
    await safe_callback_answer(callback)


@router.callback_query(F.data == "admin_schedule:back_to_panel")
async def admin_schedule_back_to_panel(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await safe_callback_answer(callback, "Сначала войдите в админ-панель через /admin", show_alert=True)
        return
    await _safe_edit_admin_panel(callback, "Что хочешь изменить?", reply_markup=_schedule_entry_keyboard())
    await safe_callback_answer(callback)


@router.callback_query(F.data == "admin_schedule:edit_days")
async def admin_schedule_edit_days(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await safe_callback_answer(callback, "Сначала войдите в админ-панель через /admin", show_alert=True)
        return
    schedule = await work_schedule_repo.get_latest()
    selected_weekdays = (
        {d for d in schedule.weekdays if d in SCHEDULE_WEEKDAY_LABELS}
        if schedule is not None
        else set(schedule_service.WORKING_WEEKDAYS)
    )
    await state.set_state(AdminScheduleStates.waiting_weekdays)
    await state.update_data(schedule_weekdays=sorted(selected_weekdays))
    await _safe_edit_admin_panel(
        callback,
        "Выбери рабочие дни:",
        reply_markup=_schedule_weekdays_keyboard(selected_weekdays),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "admin_schedule:edit_start")
async def admin_schedule_edit_start(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await safe_callback_answer(callback, "Сначала войдите в админ-панель через /admin", show_alert=True)
        return
    schedule = await work_schedule_repo.get_latest()
    selected_weekdays = sorted(schedule.weekdays) if schedule else sorted(schedule_service.WORKING_WEEKDAYS)
    await state.set_state(AdminScheduleStates.waiting_start_time)
    await state.update_data(schedule_weekdays=selected_weekdays)
    times = _schedule_time_options()
    await _safe_edit_admin_panel(
        callback,
        "Выбери время начала работы:",
        reply_markup=_schedule_times_keyboard(times, kind="start"),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "admin_schedule:edit_end")
async def admin_schedule_edit_end(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await safe_callback_answer(callback, "Сначала войдите в админ-панель через /admin", show_alert=True)
        return
    schedule = await work_schedule_repo.get_latest()
    selected_weekdays = sorted(schedule.weekdays) if schedule else sorted(schedule_service.WORKING_WEEKDAYS)
    start_time = schedule.start_time.strftime("%H:%M") if schedule else ScheduleService.DEFAULT_START
    await state.set_state(AdminScheduleStates.waiting_end_time)
    await state.update_data(schedule_weekdays=selected_weekdays, start_time=start_time)
    times = [t for t in _schedule_time_options() if t > start_time]
    await _safe_edit_admin_panel(
        callback,
        "Выбери время окончания работы:",
        reply_markup=_schedule_times_keyboard(times, kind="end"),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "admin_schedule:lunch_none")
async def admin_schedule_lunch_none(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminScheduleStates.waiting_lunch_time.state:
        await safe_callback_answer(callback, "Сначала выбери время окончания рабочего дня.", show_alert=True)
        return
    data = await state.get_data()
    selected_weekdays = set(data.get("schedule_weekdays") or [])
    start_s = data.get("start_time")
    end_s = data.get("end_time")
    if not selected_weekdays or not start_s or not end_s:
        await safe_callback_answer(callback, "Недостаточно данных для сохранения.", show_alert=True)
        return
    start_t = datetime.strptime(str(start_s), "%H:%M").time()
    end_t = datetime.strptime(str(end_s), "%H:%M").time()
    await work_schedule_repo.set_schedule(
        weekdays=sorted(selected_weekdays),
        start_time=start_t,
        end_time=end_t,
        lunch_time=None,
    )
    await state.set_state(AdminPanelStates.in_menu)
    await _safe_edit_admin_panel(
        callback,
        "✅ График обновлён!\n\n"
        f"Рабочие дни: {_format_workdays_line(selected_weekdays)}\n"
        f"Время работы: {start_t.strftime('%H:%M')} — {end_t.strftime('%H:%M')}\n"
        "Обед: без обеда\n"
        f"Выходные: {_format_off_days_line(selected_weekdays)}",
        reply_markup=_schedule_entry_keyboard(),
    )
    await safe_callback_answer(callback)

def _schedule_time_options(start_hhmm: str = "08:00", end_hhmm: str = "20:00", step_minutes: int = 30) -> list[str]:
    start_dt = datetime.strptime(start_hhmm, "%H:%M")
    end_dt = datetime.strptime(end_hhmm, "%H:%M")
    out: list[str] = []
    current = start_dt
    while current <= end_dt:
        out.append(current.strftime("%H:%M"))
        current += timedelta(minutes=step_minutes)
    return out


@router.callback_query(F.data.startswith("admin_schedule:toggle_weekday:"))
async def admin_schedule_toggle_weekday(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminScheduleStates.waiting_weekdays.state:
        await safe_callback_answer(callback, "Сначала начните редактирование /set_schedule.", show_alert=True)
        return

    payload = callback.data.split(":", 2)[-1]
    try:
        weekday = int(payload)
    except ValueError:
        await safe_callback_answer(callback, "Некорректный день.", show_alert=True)
        return

    data = await state.get_data()
    selected_weekdays = set(data.get("schedule_weekdays") or [])
    if weekday in selected_weekdays:
        selected_weekdays.remove(weekday)
    else:
        selected_weekdays.add(weekday)

    await state.update_data(schedule_weekdays=sorted(selected_weekdays))
    await _safe_edit_admin_panel(
        callback,
        "Выберите рабочие дни (Пн–Вс):",
        reply_markup=_schedule_weekdays_keyboard(selected_weekdays),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "admin_schedule:confirm_weekdays")
async def admin_schedule_confirm_weekdays(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminScheduleStates.waiting_weekdays.state:
        await safe_callback_answer(callback, "Сначала выберите дни.", show_alert=True)
        return

    data = await state.get_data()
    selected_weekdays = set(data.get("schedule_weekdays") or [])
    if not selected_weekdays:
        await safe_callback_answer(callback, "Выберите хотя бы один рабочий день.", show_alert=True)
        return

    await state.set_state(AdminScheduleStates.waiting_start_time)

    times = _schedule_time_options()
    await _safe_edit_admin_panel(
        callback,
        "Выберите время начала рабочего дня:",
        reply_markup=_schedule_times_keyboard(times, kind="start"),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data == "admin_schedule:back_to_weekdays")
async def admin_schedule_back_to_weekdays(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() not in {
        AdminScheduleStates.waiting_start_time.state,
        AdminScheduleStates.waiting_end_time.state,
        AdminScheduleStates.waiting_lunch_time.state,
    }:
        pass

    data = await state.get_data()
    selected_weekdays = set(data.get("schedule_weekdays") or [])
    await state.set_state(AdminScheduleStates.waiting_weekdays)
    await _safe_edit_admin_panel(
        callback,
        "Выберите рабочие дни (Пн–Вс):",
        reply_markup=_schedule_weekdays_keyboard(selected_weekdays),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("admin_schedule:set_start:"))
async def admin_schedule_set_start_time(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminScheduleStates.waiting_start_time.state:
        await safe_callback_answer(callback, "Сначала выберите время начала.", show_alert=True)
        return

    payload = callback.data.split(":", 2)[-1]
    # payload = HH:MM
    try:
        datetime.strptime(payload, "%H:%M")
    except ValueError:
        await safe_callback_answer(callback, "Некорректное время начала.", show_alert=True)
        return

    await state.update_data(start_time=payload)
    await state.set_state(AdminScheduleStates.waiting_end_time)

    times = _schedule_time_options()
    start_dt = datetime.strptime(payload, "%H:%M").time()
    end_times = [t for t in times if datetime.strptime(t, "%H:%M").time() > start_dt]
    await _safe_edit_admin_panel(
        callback,
        "Выберите время конца рабочего дня:",
        reply_markup=_schedule_times_keyboard(end_times, kind="end"),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("admin_schedule:set_end:"))
async def admin_schedule_set_end_time(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminScheduleStates.waiting_end_time.state:
        await safe_callback_answer(callback, "Сначала выберите время конца.", show_alert=True)
        return

    payload = callback.data.split(":", 2)[-1]
    try:
        datetime.strptime(payload, "%H:%M")
    except ValueError:
        await safe_callback_answer(callback, "Некорректное время конца.", show_alert=True)
        return

    data = await state.get_data()
    start_s = data.get("start_time")
    if not start_s:
        await safe_callback_answer(callback, "Сначала выберите время начала.", show_alert=True)
        return
    start_t = datetime.strptime(str(start_s), "%H:%M").time()
    end_t = datetime.strptime(payload, "%H:%M").time()
    options: list[str] = []
    current = datetime.combine(date.today(), start_t)
    end_limit = datetime.combine(date.today(), end_t) - timedelta(minutes=60)
    while current <= end_limit:
        options.append(current.strftime("%H:%M"))
        current += timedelta(minutes=30)

    await state.update_data(end_time=payload)
    await state.set_state(AdminScheduleStates.waiting_lunch_time)
    await _safe_edit_admin_panel(
        callback,
        "Выбери время обеда:",
        reply_markup=_lunch_time_keyboard(options),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith("admin_schedule:lunch_time:"))
async def admin_schedule_lunch_time(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminScheduleStates.waiting_lunch_time.state:
        await safe_callback_answer(callback, "Сначала выбери время окончания рабочего дня.", show_alert=True)
        return

    payload = callback.data.split(":", 2)[-1]
    data = await state.get_data()
    selected_weekdays = set(data.get("schedule_weekdays") or [])
    start_s = data.get("start_time")
    end_s = data.get("end_time")

    if not selected_weekdays or not start_s or not end_s:
        await safe_callback_answer(callback, "Недостаточно данных для сохранения.", show_alert=True)
        return

    start_t = datetime.strptime(str(start_s), "%H:%M").time()
    end_t = datetime.strptime(str(end_s), "%H:%M").time()
    if start_t >= end_t:
        await safe_callback_answer(callback, "start_time должен быть меньше end_time.", show_alert=True)
        return

    lunch_t = datetime.strptime(payload, "%H:%M").time()
    lunch_end_t = (datetime.combine(date.today(), lunch_t) + timedelta(minutes=60)).time()
    await work_schedule_repo.set_schedule(
        weekdays=sorted(selected_weekdays),
        start_time=start_t,
        end_time=end_t,
        lunch_time=lunch_t,
    )

    await state.set_state(AdminPanelStates.in_menu)
    await _safe_edit_admin_panel(
        callback,
        "✅ График обновлён!\n\n"
        f"Рабочие дни: {_format_workdays_line(selected_weekdays)}\n"
        f"Время работы: {start_t.strftime('%H:%M')} — {end_t.strftime('%H:%M')}\n"
        f"Обед: {lunch_t.strftime('%H:%M')} — {lunch_end_t.strftime('%H:%M')}\n"
        f"Выходные: {_format_off_days_line(selected_weekdays)}",
        reply_markup=_schedule_entry_keyboard(),
    )
    await safe_callback_answer(callback)


def _month_title(year: int, month: int) -> str:
    months = {
        1: "ЯНВАРЬ",
        2: "ФЕВРАЛЬ",
        3: "МАРТ",
        4: "АПРЕЛЬ",
        5: "МАЙ",
        6: "ИЮНЬ",
        7: "ИЮЛЬ",
        8: "АВГУСТ",
        9: "СЕНТЯБРЬ",
        10: "ОКТЯБРЬ",
        11: "НОЯБРЬ",
        12: "ДЕКАБРЬ",
    }
    return f"{months.get(month, str(month))} {year}"


def _month_key(year: int, month: int) -> str:
    return f"{year:04d}-{month:02d}"


def _shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    base = year * 12 + (month - 1) + delta
    return base // 12, (base % 12) + 1


def _month_overview_keyboard(year: int, month: int, has_schedule: bool) -> InlineKeyboardMarkup:
    prev_year, prev_month = _shift_month(year, month, -1)
    next_year, next_month = _shift_month(year, month, 1)
    action_text = "Редактировать" if has_schedule else "+ Добавить график"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=action_text,
                    callback_data=f"{MONTHLY_PREFIX}:edit_mode:{year:04d}-{month:02d}",
                ),
                InlineKeyboardButton(
                    text="Следующий месяц →",
                    callback_data=f"{MONTHLY_PREFIX}:overview:{next_year:04d}-{next_month:02d}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="← Предыдущий месяц",
                    callback_data=f"{MONTHLY_PREFIX}:overview:{prev_year:04d}-{prev_month:02d}",
                )
            ],
        ]
    )


def _edit_mode_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    ym = f"{year:04d}-{month:02d}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Весь месяц сразу 📅", callback_data=f"{MONTHLY_PREFIX}:mode:full_month:{ym}")],
            [InlineKeyboardButton(text="По неделям 📆", callback_data=f"{MONTHLY_PREFIX}:mode:by_weeks:{ym}")],
            [InlineKeyboardButton(text="← Назад", callback_data=f"{MONTHLY_PREFIX}:overview:{ym}")],
        ]
    )


def _weekday_pick_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    ym = f"{year:04d}-{month:02d}"
    items = [("ПН", 0), ("ВТ", 1), ("СР", 2), ("ЧТ", 3), ("ПТ", 4), ("СБ", 5), ("ВС", 6)]
    rows: list[list[InlineKeyboardButton]] = []
    rows.append(
        [
            InlineKeyboardButton(text=items[i][0], callback_data=f"{MONTHLY_PREFIX}:pick_weekday:{ym}:{items[i][1]}")
            for i in range(5)
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text=items[5][0], callback_data=f"{MONTHLY_PREFIX}:pick_weekday:{ym}:{items[5][1]}"),
            InlineKeyboardButton(text=items[6][0], callback_data=f"{MONTHLY_PREFIX}:pick_weekday:{ym}:{items[6][1]}"),
        ]
    )
    rows.append([InlineKeyboardButton(text="✓ Сохранить месяц", callback_data=f"{MONTHLY_PREFIX}:save_month:{ym}")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=f"{MONTHLY_PREFIX}:edit_mode:{ym}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _bool_day_keyboard(year: int, month: int, day_key: str) -> InlineKeyboardMarkup:
    ym = f"{year:04d}-{month:02d}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✓ Да", callback_data=f"{MONTHLY_PREFIX}:work_yes:{ym}:{day_key}"),
                InlineKeyboardButton(text="❌ Нет (выходной)", callback_data=f"{MONTHLY_PREFIX}:work_no:{ym}:{day_key}"),
            ],
            [InlineKeyboardButton(text="← Назад", callback_data=f"{MONTHLY_PREFIX}:back_pick_day:{ym}")],
        ]
    )


def _time_rows_keyboard(times: list[str], action: str, year: int, month: int, day_key: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(times), 4):
        row = times[i : i + 4]
        rows.append(
            [
                InlineKeyboardButton(
                    text=t,
                    callback_data=f"{MONTHLY_PREFIX}:{action}:{year:04d}-{month:02d}:{day_key}:{t}",
                )
                for t in row
            ]
        )
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=f"{MONTHLY_PREFIX}:back_pick_day:{year:04d}-{month:02d}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _day_done_keyboard(year: int, month: int, day_key: str) -> InlineKeyboardMarkup:
    ym = f"{year:04d}-{month:02d}"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✓ Сохранить и дальше", callback_data=f"{MONTHLY_PREFIX}:day_done:{ym}:{day_key}")],
            [InlineKeyboardButton(text="← Вернуться к выбору дня", callback_data=f"{MONTHLY_PREFIX}:back_pick_day:{ym}")],
        ]
    )


def _time_options_15m(start_hhmm: str = "08:00", end_hhmm: str = "21:00") -> list[str]:
    start_dt = datetime.strptime(start_hhmm, "%H:%M")
    end_dt = datetime.strptime(end_hhmm, "%H:%M")
    out: list[str] = []
    current = start_dt
    while current <= end_dt:
        out.append(current.strftime("%H:%M"))
        current += timedelta(minutes=15)
    return out


def _day_title(day_key: str) -> str:
    names = {
        "monday": "ПОНЕДЕЛЬНИКА",
        "tuesday": "ВТОРНИКА",
        "wednesday": "СРЕДЫ",
        "thursday": "ЧЕТВЕРГА",
        "friday": "ПЯТНИЦЫ",
        "saturday": "СУББОТЫ",
        "sunday": "ВОСКРЕСЕНЬЯ",
    }
    return names.get(day_key, day_key.upper())


def _weekday_key_by_num(num: int) -> str:
    return {
        0: "monday",
        1: "tuesday",
        2: "wednesday",
        3: "thursday",
        4: "friday",
        5: "saturday",
        6: "sunday",
    }[num]


def _week_ranges(year: int, month: int) -> list[tuple[int, date, date]]:
    last_day = calendar.monthrange(year, month)[1]
    ranges: list[tuple[int, date, date]] = []
    week_number = 1
    day = 1
    while day <= last_day:
        start = date(year, month, day)
        end = date(year, month, min(day + 6, last_day))
        ranges.append((week_number, start, end))
        week_number += 1
        day += 7
    return ranges


def _week_pick_keyboard(year: int, month: int) -> InlineKeyboardMarkup:
    ym = f"{year:04d}-{month:02d}"
    rows: list[list[InlineKeyboardButton]] = []
    for week_number, start_d, end_d in _week_ranges(year, month):
        text = f"Неделя {week_number} ({start_d.day}-{end_d.day})"
        rows.append(
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=f"{MONTHLY_PREFIX}:pick_week:{ym}:{week_number}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=f"{MONTHLY_PREFIX}:edit_mode:{ym}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _day_pick_in_week_keyboard(year: int, month: int, week_number: int) -> InlineKeyboardMarkup:
    ym = f"{year:04d}-{month:02d}"
    rows: list[list[InlineKeyboardButton]] = []
    week_map = _week_ranges(year, month)
    target = next((w for w in week_map if w[0] == week_number), None)
    if target is None:
        return InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text="← Назад", callback_data=f"{MONTHLY_PREFIX}:mode:by_weeks:{ym}")]]
        )
    _, start_d, end_d = target
    labels = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
    days: list[tuple[str, date]] = []
    cur = start_d
    while cur <= end_d:
        days.append((labels[cur.weekday()], cur))
        cur += timedelta(days=1)

    row: list[InlineKeyboardButton] = []
    for idx, (wd, d) in enumerate(days):
        row.append(
            InlineKeyboardButton(
                text=f"{wd} {d.day}",
                callback_data=f"{MONTHLY_PREFIX}:pick_date:{ym}:{d.isoformat()}",
            )
        )
        if (idx + 1) % 4 == 0:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="✓ Сохранить месяц", callback_data=f"{MONTHLY_PREFIX}:save_month:{ym}")])
    rows.append([InlineKeyboardButton(text="← Назад", callback_data=f"{MONTHLY_PREFIX}:mode:by_weeks:{ym}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _month_overview_text(year: int, month: int) -> tuple[str, bool]:
    monthly = await work_schedule_repo.get_month_schedule(_month_key(year, month))
    if monthly is None:
        text = f"📅 {_month_title(year, month)}\n\n❌ График не добавлен"
        return text, False
    text = (
        f"📅 {_month_title(year, month)}\n\n"
        f"Режим: {'Весь месяц' if monthly.edit_mode == 'full_month' else 'По неделям'}\n"
        "✅ График добавлен"
    )
    return text, True


async def _open_month_overview_message(message: Message, state: FSMContext, year: int, month: int) -> None:
    text, has_schedule = await _month_overview_text(year, month)
    await state.set_state(AdminScheduleStates.waiting_month_overview)
    await state.update_data(monthly_year=year, monthly_month=month)
    await _send_or_replace_schedule_panel(
        message,
        state,
        text,
    )
    data = await state.get_data()
    msg_id = data.get(ADMIN_INLINE_MESSAGE_ID_KEY)
    if isinstance(msg_id, int):
        try:
            await message.bot.edit_message_reply_markup(
                chat_id=message.chat.id,
                message_id=msg_id,
                reply_markup=_month_overview_keyboard(year, month, has_schedule),
            )
        except Exception:
            pass


async def _open_month_overview_callback(callback: CallbackQuery, state: FSMContext, year: int, month: int) -> None:
    text, has_schedule = await _month_overview_text(year, month)
    await state.set_state(AdminScheduleStates.waiting_month_overview)
    await state.update_data(monthly_year=year, monthly_month=month)
    await _safe_edit_admin_panel(callback, text, reply_markup=_month_overview_keyboard(year, month, has_schedule))


@router.callback_query(F.data.startswith(f"{MONTHLY_PREFIX}:overview:"))
async def monthly_overview(callback: CallbackQuery, state: FSMContext) -> None:
    payload = callback.data.split(":")[-1]
    y_s, m_s = payload.split("-")
    await _open_month_overview_callback(callback, state, int(y_s), int(m_s))
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith(f"{MONTHLY_PREFIX}:edit_mode:"))
async def monthly_edit_mode(callback: CallbackQuery, state: FSMContext) -> None:
    payload = callback.data.split(":")[-1]
    y_s, m_s = payload.split("-")
    year, month = int(y_s), int(m_s)
    await state.set_state(AdminScheduleStates.waiting_edit_mode)
    await state.update_data(monthly_year=year, monthly_month=month, monthly_mode=None, monthly_draft={})
    await _safe_edit_admin_panel(
        callback,
        f"🔧 РЕДАКТИРОВАНИЕ ГРАФИКА\n{_month_title(year, month)}\n\nКак редактировать?",
        reply_markup=_edit_mode_keyboard(year, month),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith(f"{MONTHLY_PREFIX}:mode:"))
async def monthly_select_mode(callback: CallbackQuery, state: FSMContext) -> None:
    parts = callback.data.split(":")
    mode = parts[2]
    y_s, m_s = parts[3].split("-")
    year, month = int(y_s), int(m_s)
    await state.update_data(monthly_mode=mode, monthly_year=year, monthly_month=month, monthly_draft={})
    if mode == "full_month":
        await state.set_state(AdminScheduleStates.waiting_month_weekday_pick)
        await _safe_edit_admin_panel(
            callback,
            f"📋 ГРАФИК НА ВЕСЬ МЕСЯЦ\n{_month_title(year, month)}\n\nВыберите день недели:",
            reply_markup=_weekday_pick_keyboard(year, month),
        )
    else:
        await state.set_state(AdminScheduleStates.waiting_week_pick)
        await _safe_edit_admin_panel(
            callback,
            f"📆 РЕДАКТИРОВАНИЕ ПО НЕДЕЛЯМ\n{_month_title(year, month)}\n\nВыберите неделю:",
            reply_markup=_week_pick_keyboard(year, month),
        )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith(f"{MONTHLY_PREFIX}:pick_week:"))
async def monthly_pick_week(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, _, ym, week_s = callback.data.split(":")
    y_s, m_s = ym.split("-")
    year, month, week_number = int(y_s), int(m_s), int(week_s)
    await state.set_state(AdminScheduleStates.waiting_day_pick)
    await state.update_data(monthly_year=year, monthly_month=month, monthly_week=week_number)
    await _safe_edit_admin_panel(
        callback,
        f"📆 НЕДЕЛЯ {week_number} ({_month_title(year, month)})\n\nКакой день редактировать?",
        reply_markup=_day_pick_in_week_keyboard(year, month, week_number),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith(f"{MONTHLY_PREFIX}:pick_date:"))
async def monthly_pick_date(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, _, ym, date_iso = callback.data.split(":")
    y_s, m_s = ym.split("-")
    year, month = int(y_s), int(m_s)
    picked = date.fromisoformat(date_iso)
    day_key = picked.isoformat()
    await state.set_state(AdminScheduleStates.waiting_day_working_flag)
    await state.update_data(monthly_current_day=day_key, monthly_year=year, monthly_month=month)
    await _safe_edit_admin_panel(
        callback,
        f"⏰ РЕДАКТИРОВАНИЕ ДНЯ ({picked.strftime('%d.%m.%Y')})\n\nРаботает ли в этот день?",
        reply_markup=_bool_day_keyboard(year, month, day_key),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith(f"{MONTHLY_PREFIX}:pick_weekday:"))
async def monthly_pick_weekday(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, _, ym, weekday_s = callback.data.split(":")
    y_s, m_s = ym.split("-")
    year, month = int(y_s), int(m_s)
    day_key = _weekday_key_by_num(int(weekday_s))
    await state.set_state(AdminScheduleStates.waiting_day_working_flag)
    await state.update_data(monthly_current_day=day_key, monthly_year=year, monthly_month=month)
    await _safe_edit_admin_panel(
        callback,
        f"⏰ РЕДАКТИРОВАНИЕ {_day_title(day_key)}\n\nРаботает ли в этот день?",
        reply_markup=_bool_day_keyboard(year, month, day_key),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith(f"{MONTHLY_PREFIX}:work_no:"))
async def monthly_day_off(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, _, ym, day_key = callback.data.split(":")
    data = await state.get_data()
    draft = dict(data.get("monthly_draft") or {})
    draft[day_key] = {"is_day_off": True}
    await state.update_data(monthly_draft=draft)
    await _safe_edit_admin_panel(
        callback,
        "День отмечен как выходной.\n\nВыберите действие:",
        reply_markup=_day_done_keyboard(int(ym.split("-")[0]), int(ym.split("-")[1]), day_key),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith(f"{MONTHLY_PREFIX}:work_yes:"))
async def monthly_day_work_yes(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, _, ym, day_key = callback.data.split(":")
    y_s, m_s = ym.split("-")
    year, month = int(y_s), int(m_s)
    await state.set_state(AdminScheduleStates.waiting_start_time)
    times = _time_options_15m()
    await _safe_edit_admin_panel(
        callback,
        f"⏰ РЕДАКТИРОВАНИЕ {_day_title(day_key)}\n\nВремя начала смены?",
        reply_markup=_time_rows_keyboard(times, "set_start", year, month, day_key),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith(f"{MONTHLY_PREFIX}:set_start:"))
async def monthly_set_start(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, _, ym, day_key, start_hhmm = callback.data.split(":")
    y_s, m_s = ym.split("-")
    year, month = int(y_s), int(m_s)
    data = await state.get_data()
    draft = dict(data.get("monthly_draft") or {})
    day_data = dict(draft.get(day_key) or {})
    day_data["start_time"] = start_hhmm
    draft[day_key] = day_data
    await state.update_data(monthly_draft=draft)
    await state.set_state(AdminScheduleStates.waiting_end_time)
    times = [t for t in _time_options_15m() if t > start_hhmm]
    await _safe_edit_admin_panel(
        callback,
        f"⏰ РЕДАКТИРОВАНИЕ {_day_title(day_key)}\nНачало: {start_hhmm} ✓\n\nВремя конца смены?",
        reply_markup=_time_rows_keyboard(times, "set_end", year, month, day_key),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith(f"{MONTHLY_PREFIX}:set_end:"))
async def monthly_set_end(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, _, ym, day_key, end_hhmm = callback.data.split(":")
    y_s, m_s = ym.split("-")
    year, month = int(y_s), int(m_s)
    data = await state.get_data()
    draft = dict(data.get("monthly_draft") or {})
    day_data = dict(draft.get(day_key) or {})
    start_hhmm = str(day_data.get("start_time") or "")
    if not start_hhmm or end_hhmm <= start_hhmm:
        await safe_callback_answer(callback, "❌ Ошибка: конец не может быть раньше начала.", show_alert=True)
        return
    day_data["end_time"] = end_hhmm
    draft[day_key] = day_data
    await state.update_data(monthly_draft=draft)
    await state.set_state(AdminScheduleStates.waiting_lunch_time)
    times = [t for t in _time_options_15m() if start_hhmm <= t < end_hhmm]
    await _safe_edit_admin_panel(
        callback,
        f"⏰ РЕДАКТИРОВАНИЕ {_day_title(day_key)}\nНачало: {start_hhmm} ✓\nКонец: {end_hhmm} ✓\n\nВремя обеда (начало)?",
        reply_markup=_time_rows_keyboard(times, "set_lunch_start", year, month, day_key),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith(f"{MONTHLY_PREFIX}:set_lunch_start:"))
async def monthly_set_lunch_start(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, _, ym, day_key, lunch_start = callback.data.split(":")
    y_s, m_s = ym.split("-")
    year, month = int(y_s), int(m_s)
    data = await state.get_data()
    draft = dict(data.get("monthly_draft") or {})
    day_data = dict(draft.get(day_key) or {})
    day_data["lunch_start"] = lunch_start
    draft[day_key] = day_data
    await state.update_data(monthly_draft=draft)
    await state.set_state(AdminScheduleStates.waiting_lunch_end_time)
    end_hhmm = str(day_data.get("end_time") or "")
    times = [t for t in _time_options_15m() if lunch_start < t <= end_hhmm]
    await _safe_edit_admin_panel(
        callback,
        f"⏰ РЕДАКТИРОВАНИЕ {_day_title(day_key)}\nОбед начало: {lunch_start} ✓\n\nВремя обеда (конец)?",
        reply_markup=_time_rows_keyboard(times, "set_lunch_end", year, month, day_key),
    )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith(f"{MONTHLY_PREFIX}:set_lunch_end:"))
async def monthly_set_lunch_end(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, _, ym, day_key, lunch_end = callback.data.split(":")
    y_s, m_s = ym.split("-")
    year, month = int(y_s), int(m_s)
    data = await state.get_data()
    draft = dict(data.get("monthly_draft") or {})
    day_data = dict(draft.get(day_key) or {})
    start_hhmm = str(day_data.get("start_time") or "")
    end_hhmm = str(day_data.get("end_time") or "")
    lunch_start = str(day_data.get("lunch_start") or "")
    if not (start_hhmm and end_hhmm and lunch_start):
        await safe_callback_answer(callback, "Недостаточно данных.", show_alert=True)
        return
    if not (start_hhmm <= lunch_start < lunch_end <= end_hhmm):
        await safe_callback_answer(callback, "❌ Ошибка: обед должен быть внутри смены.", show_alert=True)
        return
    day_data.update(
        {
            "is_day_off": False,
            "lunch_end": lunch_end,
        }
    )
    draft[day_key] = day_data
    await state.update_data(monthly_draft=draft)
    await _safe_edit_admin_panel(
        callback,
        f"⏰ РЕДАКТИРОВАНИЕ {_day_title(day_key)}\n"
        f"Начало: {start_hhmm} ✓\n"
        f"Конец: {end_hhmm} ✓\n"
        f"Обед: {lunch_start} - {lunch_end} ✓",
        reply_markup=_day_done_keyboard(year, month, day_key),
    )
    await safe_callback_answer(callback)


def _to_weekly_payload(draft: dict[str, Any]) -> dict[str, Any]:
    return {"days_of_week": draft}


def _to_weeks_payload(year: int, month: int, draft: dict[str, Any]) -> dict[str, Any]:
    weeks: list[dict[str, Any]] = []
    for week_number, start_d, end_d in _week_ranges(year, month):
        days: dict[str, Any] = {}
        cur = start_d
        while cur <= end_d:
            iso = cur.isoformat()
            day = dict(draft.get(iso) or {"is_day_off": True})
            day["date"] = iso
            days[str(cur.weekday())] = day
            cur += timedelta(days=1)
        weeks.append(
            {
                "week_number": week_number,
                "start_date": start_d.isoformat(),
                "end_date": end_d.isoformat(),
                "days": days,
            }
        )
    return {"weeks": weeks}


@router.callback_query(F.data.startswith(f"{MONTHLY_PREFIX}:day_done:"))
async def monthly_day_done(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, _, ym, _day_key = callback.data.split(":")
    y_s, m_s = ym.split("-")
    year, month = int(y_s), int(m_s)
    data = await state.get_data()
    mode = str(data.get("monthly_mode") or "full_month")
    draft = dict(data.get("monthly_draft") or {})
    if mode == "full_month":
        await state.set_state(AdminScheduleStates.waiting_month_weekday_pick)
        await _safe_edit_admin_panel(
            callback,
            f"📋 ГРАФИК НА ВЕСЬ МЕСЯЦ\n{_month_title(year, month)}\n\nВыберите следующий день недели:",
            reply_markup=_weekday_pick_keyboard(year, month),
        )
        await safe_callback_answer(callback)
        return

    if mode == "by_weeks":
        week_number = int(data.get("monthly_week") or 1)
        await state.set_state(AdminScheduleStates.waiting_day_pick)
        await _safe_edit_admin_panel(
            callback,
            f"📆 НЕДЕЛЯ {week_number} ({_month_title(year, month)})\n\nКакой день редактировать?",
            reply_markup=_day_pick_in_week_keyboard(year, month, week_number),
        )
        await safe_callback_answer(callback)
        return

    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith(f"{MONTHLY_PREFIX}:back_pick_day:"))
async def monthly_back_pick_day(callback: CallbackQuery, state: FSMContext) -> None:
    ym = callback.data.split(":")[-1]
    y_s, m_s = ym.split("-")
    year, month = int(y_s), int(m_s)
    data = await state.get_data()
    mode = str(data.get("monthly_mode") or "full_month")
    if mode == "full_month":
        await state.set_state(AdminScheduleStates.waiting_month_weekday_pick)
        await _safe_edit_admin_panel(
            callback,
            f"📋 ГРАФИК НА ВЕСЬ МЕСЯЦ\n{_month_title(year, month)}\n\nВыберите день недели:",
            reply_markup=_weekday_pick_keyboard(year, month),
        )
    else:
        week_number = int(data.get("monthly_week") or 1)
        await state.set_state(AdminScheduleStates.waiting_day_pick)
        await _safe_edit_admin_panel(
            callback,
            f"📆 НЕДЕЛЯ {week_number} ({_month_title(year, month)})\n\nКакой день редактировать?",
            reply_markup=_day_pick_in_week_keyboard(year, month, week_number),
        )
    await safe_callback_answer(callback)


@router.callback_query(F.data.startswith(f"{MONTHLY_PREFIX}:save_month:"))
async def monthly_save_month(callback: CallbackQuery, state: FSMContext) -> None:
    _, _, _, ym = callback.data.split(":")
    y_s, m_s = ym.split("-")
    year, month = int(y_s), int(m_s)
    data = await state.get_data()
    draft = dict(data.get("monthly_draft") or {})
    mode = str(data.get("monthly_mode") or "full_month")
    payload = _to_weekly_payload(draft) if mode == "full_month" else _to_weeks_payload(year, month, draft)
    await work_schedule_repo.upsert_month_schedule(_month_key(year, month), mode, payload)
    await state.set_state(AdminPanelStates.in_menu)
    await _safe_edit_admin_panel(
        callback,
        f"✅ ГРАФИК СОХРАНЁН\n\nВаш график на {_month_title(year, month)}.",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="← К главному меню",
                        callback_data=f"{MONTHLY_PREFIX}:overview:{year:04d}-{month:02d}",
                    )
                ]
            ]
        ),
    )
    await safe_callback_answer(callback)
