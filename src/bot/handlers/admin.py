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
from src.infra.db.repositories.work_schedule_repository import WorkScheduleRepository
from src.app.services.schedule_service import ScheduleService
from src.bot.handlers.states import AdminPanelStates

router = Router()
appointments_repo = AppointmentsRepository()
users_repo = UsersRepository()
work_schedule_repo = WorkScheduleRepository()
schedule_service = ScheduleService()

def _admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Расписание барбера",
                    callback_data="admin_panel:schedule",
                )
            ],
            [
                InlineKeyboardButton(
                    text="Приёмы на сегодня",
                    callback_data="admin_panel:today_appointments",
                )
            ],
        ]
    )


async def _safe_edit_admin_panel(callback: CallbackQuery, text: str) -> None:
    try:
        await callback.message.edit_text(text, reply_markup=_admin_panel_keyboard())
    except TelegramBadRequest as e:
        # Частый кейс: "message is not modified" или сообщение недоступно.
        if "message is not modified" in str(e).lower():
            return
        await callback.message.answer(text, reply_markup=_admin_panel_keyboard())


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


def _format_line(time_slot: str, name: str, phone: str) -> str:
    return f"{time_slot} — {name} ({phone})"


async def _send_for_date(message: Message, target_date: date) -> None:
    appts = await appointments_repo.list_by_date_from_today(target_date)
    if not appts:
        await message.answer("Записей нет.")
        return

    lines: list[str] = []
    # N+1 — допустимо для MVP.
    for appt in appts:
        user = await users_repo.get_by_user_id(appt.user_id)
        if user is None:
            continue
        lines.append(
            _format_line(
                time_slot=appt.time_slot.strftime("%H:%M"),
                name=user.name,
                phone=user.phone,
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

    lines: list[str] = []
    for appt in appts:
        user = await users_repo.get_by_user_id(appt.user_id)
        if user is None:
            continue
        lines.append(
            _format_line(
                time_slot=appt.time_slot.strftime("%H:%M"),
                name=user.name,
                phone=user.phone,
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


@router.message(Command("set_schedule"))
async def set_work_schedule(message: Message) -> None:
    if not _is_admin(message.from_user.id):
        await message.answer("Недостаточно прав.")
        return

    # Формат:
    # /set_schedule 1,2,3,4,5 10:00 18:00
    parts = (message.text or "").strip().split()
    if len(parts) != 4:
        await message.answer(
            "Использование:\n"
            "/set_schedule 1,2,3,4,5 10:00 18:00\n"
            "Где 1=Пн ... 7=Вс."
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


@router.callback_query(F.data == "admin_panel:schedule")
async def admin_panel_show_schedule(callback: CallbackQuery, state: FSMContext) -> None:
    # Фоллбек-страховка: если callback пришел не в ожидаемом состоянии.
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await callback.answer("Сначала войдите в админ-панель через /admin", show_alert=True)
        return

    schedule = await work_schedule_repo.get_latest()
    if schedule is None:
        weekdays = sorted(schedule_service.WORKING_WEEKDAYS)
        start_time = ScheduleService.DEFAULT_START
        end_time = ScheduleService.DEFAULT_END
    else:
        weekdays = sorted(schedule.weekdays)
        start_time = schedule.start_time.strftime("%H:%M")
        end_time = schedule.end_time.strftime("%H:%M")

    weekdays_human = ",".join(str(d + 1) for d in weekdays)

    today = date.today()
    slots_today = await schedule_service.get_candidate_slots_for_date(today)
    today_is_working = today.weekday() in set(weekdays)

    slots_text = ", ".join(slots_today) if slots_today else "-"

    await _safe_edit_admin_panel(
        callback,
        "Расписание барбера:\n"
        f"- дни: {weekdays_human} (1=Пн ... 7=Вс)\n"
        f"- время: {start_time} - {end_time}\n"
        f"- сегодня: {'рабочий' if today_is_working else 'нерабочий'}\n"
        f"- слоты на сегодня: {slots_text}",
    )
    await callback.answer()


@router.callback_query(F.data == "admin_panel:today_appointments")
async def admin_panel_show_today_appointments(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != AdminPanelStates.in_menu.state:
        await callback.answer("Сначала войдите в админ-панель через /admin", show_alert=True)
        return

    appts = await appointments_repo.list_by_date_from_today(date.today())
    if not appts:
        text = "Приёмы на сегодня: записей нет."
        await _safe_edit_admin_panel(callback, text)
        await callback.answer()
        return

    lines: list[str] = []
    for appt in appts:
        user = await users_repo.get_by_user_id(appt.user_id)
        if user is None:
            continue
        lines.append(
            _format_line(
                time_slot=appt.time_slot.strftime("%H:%M"),
                name=user.name,
                phone=user.phone,
            )
        )

    text = "Приёмы на сегодня:\n" + ("\n".join(lines) if lines else "Записей нет.")
    await _safe_edit_admin_panel(callback, text)
    await callback.answer()
