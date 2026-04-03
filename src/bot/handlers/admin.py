"""
/**
 * @file: admin.py
 * @description: Админ-команды просмотра записей (MVP)
 * @dependencies: infra.db.repositories, infra.config.settings
 * @created: 2026-03-23
 */
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

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
from src.app.services.schedule_service import ScheduleService
from src.bot.callback_safe import safe_callback_answer
from src.bot.handlers.states import AdminPanelStates, AdminScheduleStates
from src.bot.keyboards.main_menu import admin_menu_keyboard, main_menu_keyboard

router = Router()
appointments_repo = AppointmentsRepository()
users_repo = UsersRepository()
services_repo = ServicesRepository()
work_schedule_repo = WorkScheduleRepository()
schedule_service = ScheduleService()
ADMIN_INLINE_MESSAGE_ID_KEY = "admin_inline_message_id"

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
        await _send_or_replace_schedule_panel(message, state, "Что хочешь изменить?")
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
