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
from src.bot.handlers.states import AdminPanelStates, AdminScheduleStates

router = Router()
appointments_repo = AppointmentsRepository()
users_repo = UsersRepository()
services_repo = ServicesRepository()
work_schedule_repo = WorkScheduleRepository()
schedule_service = ScheduleService()

def _admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Записи на сегодня",
                    callback_data="admin_panel:today_appointments",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Записи на завтра",
                    callback_data="admin_panel:tomorrow_appointments",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Записи на другой день",
                    callback_data="admin_panel:other_days",
                )
            ],
        ]
    )


def _admin_other_days_keyboard(days: list[date]) -> InlineKeyboardMarkup:
    buttons: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(days), 3):
        row = days[i : i + 3]
        buttons.append(
            [
                InlineKeyboardButton(
                    text=d.strftime("%d.%m"),
                    callback_data=f"admin_panel:other_day_pick:{d.isoformat()}",
                )
                for d in row
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _safe_edit_admin_panel(
    callback: CallbackQuery,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    reply_markup = reply_markup or _admin_panel_keyboard()
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramBadRequest as e:
        # Частый кейс: "message is not modified" или сообщение недоступно.
        if "message is not modified" in str(e).lower():
            return
        await callback.message.answer(text, reply_markup=reply_markup)


def _is_admin(user_id: int) -> bool:
    settings = get_settings()
    raw = settings.admin_user_ids.strip()
    if not raw:
        return False
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            ids.append(int(part))
    return user_id in ids


def _format_line(time_range: str, name: str, phone: str, service_text: str) -> str:
    return f"{time_range} — {name} ({phone}) — {service_text}"


async def _send_for_date(message: Message, target_date: date) -> None:
    appts = await appointments_repo.list_by_date_from_today(target_date)
    if not appts:
        await message.answer("Записей нет.")
        return

    services = await services_repo.list_all()
    services_map = {s.id: s for s in services}

    lines: list[str] = []
    # N+1 — допустимо для MVP.
    for appt in appts:
        user = await users_repo.get_by_user_id(appt.user_id)
        if user is None:
            continue

        service = services_map.get(appt.service_id)
        service_text = (
            f"{service.name} — {service.price_byn} BYN" if service is not None else f"Услуга #{appt.service_id}"
        )
        lines.append(
            _format_line(
                time_range=f"{appt.start_time.strftime('%H:%M')}–{appt.end_time.strftime('%H:%M')}",
                name=user.name,
                phone=user.phone,
                service_text=service_text,
            )
        )

    await message.answer("\n".join(lines) if lines else "Записей нет.")


@router.message(Command("today"))
async def today_appointments(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    await _send_for_date(message, date.today())


@router.message(Command("tomorrow"))
async def tomorrow_appointments(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return
    await _send_for_date(message, date.today() + timedelta(days=1))


@router.message(Command("all"))
async def all_future_appointments(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return

    appts = await appointments_repo.list_confirmed_from_date(date.today())
    if not appts:
        await message.answer("Будущих записей нет.")
        return

    services = await services_repo.list_all()
    services_map = {s.id: s for s in services}

    lines: list[str] = []
    for appt in appts:
        user = await users_repo.get_by_user_id(appt.user_id)
        if user is None:
            continue

        service = services_map.get(appt.service_id)
        service_text = (
            f"{service.name} — {service.price_byn} BYN" if service is not None else f"Услуга #{appt.service_id}"
        )
        lines.append(
            _format_line(
                time_range=f"{appt.start_time.strftime('%H:%M')}–{appt.end_time.strftime('%H:%M')}",
                name=user.name,
                phone=user.phone,
                service_text=service_text,
            )
        )

    await message.answer("\n".join(lines) if lines else "Будущих записей нет.")


@router.message(Command("schedule"))
async def show_work_schedule(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return

    schedule = await work_schedule_repo.get_latest()
    if schedule is None:
        # Фоллбек на дефолтный график MVP.
        weekdays = sorted(schedule_service.WORKING_WEEKDAYS)
        start_time = ScheduleService.DEFAULT_START
        end_time = ScheduleService.DEFAULT_END
    else:
        weekdays = sorted(schedule.weekdays)
        start_time = schedule.start_time.strftime("%H:%M")
        end_time = schedule.end_time.strftime("%H:%M")

    # Пользовательский формат: 1=Пн ... 7=Вс
    weekdays_human = ",".join(str(d + 1) for d in weekdays)
    await message.answer(
        "Текущий график (1=Пн ... 7=Вс):\n"
        f"- days: {weekdays_human}\n"
        f"- start: {start_time}\n"
        f"- end: {end_time}\n"
        f"- slot duration: {ScheduleService.DEFAULT_STEP_MINUTES} минут"
    )


SCHEDULE_WEEKDAY_LABELS: dict[int, str] = {
    0: "Пн",
    1: "Вт",
    2: "Ср",
    3: "Чт",
    4: "Пт",
    5: "Сб",
}


def _schedule_weekdays_keyboard(selected: set[int]) -> InlineKeyboardMarkup:
    """
    Выбор рабочих дней (Пн..Сб). Воскресенье не включаем по TZ_MARK.
    """
    buttons: list[list[InlineKeyboardButton]] = []
    day_items = list(sorted(SCHEDULE_WEEKDAY_LABELS.keys()))
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


def _schedule_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Сохранить", callback_data="admin_schedule:save_schedule")],
            [InlineKeyboardButton(text="⟵ Назад к дням", callback_data="admin_schedule:back_to_weekdays")],
        ]
    )


@router.message(Command("set_schedule"))
async def set_work_schedule(message: Message, state: FSMContext) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return

    parts = (message.text or "").strip().split()
    # UI-вариант: без аргументов.
    if len(parts) == 1:
        schedule = await work_schedule_repo.get_latest()
        if schedule is None:
            selected_weekdays = set(schedule_service.WORKING_WEEKDAYS)
            start_t = datetime.strptime(ScheduleService.DEFAULT_START, "%H:%M").time()
            end_t = datetime.strptime(ScheduleService.DEFAULT_END, "%H:%M").time()
        else:
            # Ограничиваем UI диапазоном Пн..Сб.
            selected_weekdays = {d for d in schedule.weekdays if d in SCHEDULE_WEEKDAY_LABELS}
            start_t = schedule.start_time
            end_t = schedule.end_time

        await state.clear()
        await state.set_state(AdminScheduleStates.waiting_weekdays)
        await state.update_data(schedule_weekdays=sorted(selected_weekdays))

        await message.answer(
            "Редактирование расписания. Выберите рабочие дни (Пн..Сб):",
            reply_markup=_schedule_weekdays_keyboard(selected_weekdays),
        )
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
    await state.set_state(AdminPanelStates.waiting_access_code)
    await message.answer("Введите код доступа")


@router.message(AdminPanelStates.waiting_access_code)
async def admin_panel_access_code(message: Message, state: FSMContext) -> None:
    code = (message.text or "").strip()
    settings = get_settings()
    expected = (settings.admin_panel_access_code or "").strip()

    if not code or code != expected:
        await message.answer("Неверный код доступа. Попробуйте еще раз.")
        return

    await state.set_state(AdminPanelStates.in_menu)
    await message.answer(
        "Админ-панель открыта. Выберите пункт:",
        reply_markup=_admin_panel_keyboard(),
    )


def _admin_day_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⟵ В меню",
                    callback_data="admin_panel:back_to_menu",
                )
            ]
        ]
    )


async def _render_admin_day(target_date: date) -> tuple[str, InlineKeyboardMarkup]:
    appts = await appointments_repo.list_by_date_from_today(target_date)
    if not appts:
        return ("На этот день записей нет", _admin_panel_keyboard())

    services = await services_repo.list_all()
    services_map = {s.id: s for s in services}

    lines: list[str] = []
    for appt in appts:
        user = await users_repo.get_by_user_id(appt.user_id)
        if user is None:
            continue
        service = services_map.get(appt.service_id)
        service_text = (
            f"{service.name} — {service.price_byn} BYN" if service is not None else f"Услуга #{appt.service_id}"
        )
        lines.append(
            _format_line(
                time_range=f"{appt.start_time.strftime('%H:%M')}–{appt.end_time.strftime('%H:%M')}",
                name=user.name,
                phone=user.phone,
                service_text=service_text,
            )
        )

    text = f"Записи на {target_date.strftime('%d.%m.%Y')}:\n" + ("\n".join(lines) if lines else "")
    return (text, _admin_day_keyboard())


@router.callback_query(F.data == "admin_panel:back_to_menu")
async def admin_panel_back_to_menu(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await callback.answer("Сначала войдите в админ-панель через /admin", show_alert=True)
        return
    await _safe_edit_admin_panel(callback, "Админ-панель: выберите пункт:", reply_markup=_admin_panel_keyboard())
    await callback.answer()


@router.callback_query(F.data == "admin_panel:today_appointments")
async def admin_panel_show_today_appointments(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await callback.answer("Сначала войдите в админ-панель через /admin", show_alert=True)
        return

    text, kb = await _render_admin_day(date.today())
    await _safe_edit_admin_panel(callback, text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "admin_panel:tomorrow_appointments")
async def admin_panel_show_tomorrow_appointments(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await callback.answer("Сначала войдите в админ-панель через /admin", show_alert=True)
        return

    text, kb = await _render_admin_day(date.today() + timedelta(days=1))
    await _safe_edit_admin_panel(callback, text, reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "admin_panel:other_days")
async def admin_panel_show_other_days(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await callback.answer("Сначала войдите в админ-панель через /admin", show_alert=True)
        return

    horizon = 5
    dates = await schedule_service.next_working_dates(horizon + 10)
    start = date.today() + timedelta(days=1)
    other_days = [d for d in dates if d >= start][:horizon]

    if not other_days:
        await _safe_edit_admin_panel(callback, "Ближайшие рабочие дни недоступны.", reply_markup=_admin_panel_keyboard())
        await callback.answer()
        return

    await _safe_edit_admin_panel(
        callback,
        "Выберите ближайший день:",
        reply_markup=_admin_other_days_keyboard(other_days),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_panel:other_day_pick:"))
async def admin_panel_pick_other_day(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await callback.answer("Сначала войдите в админ-панель через /admin", show_alert=True)
        return

    payload = callback.data.split(":", 3)[-1]
    try:
        target_date = date.fromisoformat(payload)
    except ValueError:
        await callback.answer("Некорректная дата.", show_alert=True)
        return

    text, kb = await _render_admin_day(target_date)
    await _safe_edit_admin_panel(callback, text, reply_markup=kb)
    await callback.answer()


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
        await callback.answer("Сначала начните редактирование /set_schedule.", show_alert=True)
        return

    payload = callback.data.split(":", 2)[-1]
    try:
        weekday = int(payload)
    except ValueError:
        await callback.answer("Некорректный день.", show_alert=True)
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
        "Выберите рабочие дни (Пн..Сб):",
        reply_markup=_schedule_weekdays_keyboard(selected_weekdays),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_schedule:confirm_weekdays")
async def admin_schedule_confirm_weekdays(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminScheduleStates.waiting_weekdays.state:
        await callback.answer("Сначала выберите дни.", show_alert=True)
        return

    data = await state.get_data()
    selected_weekdays = set(data.get("schedule_weekdays") or [])
    if not selected_weekdays:
        await callback.answer("Выберите хотя бы один рабочий день.", show_alert=True)
        return

    await state.set_state(AdminScheduleStates.waiting_start_time)

    times = _schedule_time_options()
    await _safe_edit_admin_panel(
        callback,
        "Выберите время начала рабочего дня:",
        reply_markup=_schedule_times_keyboard(times, kind="start"),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_schedule:back_to_weekdays")
async def admin_schedule_back_to_weekdays(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() not in {
        AdminScheduleStates.waiting_start_time.state,
        AdminScheduleStates.waiting_end_time.state,
        AdminScheduleStates.waiting_confirm.state if hasattr(AdminScheduleStates, "waiting_confirm") else AdminScheduleStates.waiting_end_time.state,
    }:
        # waiting_confirm мы не используем отдельно, но оставляем защиту.
        pass

    data = await state.get_data()
    selected_weekdays = set(data.get("schedule_weekdays") or [])
    await state.set_state(AdminScheduleStates.waiting_weekdays)
    await _safe_edit_admin_panel(
        callback,
        "Выберите рабочие дни (Пн..Сб):",
        reply_markup=_schedule_weekdays_keyboard(selected_weekdays),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("admin_schedule:set_start:"))
async def admin_schedule_set_start_time(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminScheduleStates.waiting_start_time.state:
        await callback.answer("Сначала выберите время начала.", show_alert=True)
        return

    payload = callback.data.split(":", 2)[-1]
    # payload = HH:MM
    try:
        datetime.strptime(payload, "%H:%M")
    except ValueError:
        await callback.answer("Некорректное время начала.", show_alert=True)
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
    await callback.answer()


@router.callback_query(F.data.startswith("admin_schedule:set_end:"))
async def admin_schedule_set_end_time(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminScheduleStates.waiting_end_time.state:
        await callback.answer("Сначала выберите время конца.", show_alert=True)
        return

    payload = callback.data.split(":", 2)[-1]
    try:
        datetime.strptime(payload, "%H:%M")
    except ValueError:
        await callback.answer("Некорректное время конца.", show_alert=True)
        return

    await state.update_data(end_time=payload)
    await state.set_state(AdminScheduleStates.waiting_confirm)

    await _safe_edit_admin_panel(
        callback,
        "Проверьте настройки и сохраните:",
        reply_markup=_schedule_confirm_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data == "admin_schedule:save_schedule")
async def admin_schedule_save(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminScheduleStates.waiting_confirm.state:
        await callback.answer("Сначала завершите выбор времени.", show_alert=True)
        return

    data = await state.get_data()
    selected_weekdays = set(data.get("schedule_weekdays") or [])
    start_s = data.get("start_time")
    end_s = data.get("end_time")

    if not selected_weekdays or not start_s or not end_s:
        await callback.answer("Недостаточно данных для сохранения.", show_alert=True)
        return

    start_t = datetime.strptime(str(start_s), "%H:%M").time()
    end_t = datetime.strptime(str(end_s), "%H:%M").time()
    if start_t >= end_t:
        await callback.answer("start_time должен быть меньше end_time.", show_alert=True)
        return

    await work_schedule_repo.set_schedule(
        weekdays=sorted(selected_weekdays),
        start_time=start_t,
        end_time=end_t,
    )

    weekdays_human = ",".join(str(d + 1) for d in sorted(selected_weekdays))
    await state.clear()
    await _safe_edit_admin_panel(
        callback,
        "График сохранен.\n"
        f"- days: {weekdays_human}\n"
        f"- start: {start_t.strftime('%H:%M')}\n"
        f"- end: {end_t.strftime('%H:%M')}",
        reply_markup=_admin_panel_keyboard(),
    )
    await callback.answer()
