"""
Кабинет мастера: только свои записи и свой график.
"""

from __future__ import annotations

import html
from datetime import date, datetime, time, timedelta

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from src.infra.auth import ROLE_MASTER, is_master_role
from src.infra.db.repositories.appointments_repository import AppointmentsRepository
from src.infra.db.repositories.masters_repository import MastersRepository
from src.infra.db.repositories.services_repository import ServicesRepository
from src.infra.db.repositories.users_repository import UsersRepository
from src.bot.keyboards.main_menu import menu_keyboard_for_role, master_menu_keyboard

router = Router()
users_repo = UsersRepository()
masters_repo = MastersRepository()
appointments_repo = AppointmentsRepository()
services_repo = ServicesRepository()

RU_WEEKDAY_SHORT = {0: "Пн", 1: "Вт", 2: "Ср", 3: "Чт", 4: "Пт", 5: "Сб", 6: "Вс"}
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


def _weekday_ru(d: date) -> str:
    return RU_WEEKDAY_SHORT[d.weekday()]


def _human_booking_date(d: date) -> str:
    today = date.today()
    if d == today:
        suffix = "сегодня"
    elif d == today + timedelta(days=1):
        suffix = "завтра"
    else:
        suffix = RU_WEEKDAY_SHORT[d.weekday()]
    return f"{d.day} {RU_MONTHS_GEN[d.month]} ({suffix})"


def _parse_hhmm(raw: str) -> time | None:
    try:
        return datetime.strptime(raw.strip()[:5], "%H:%M").time()
    except ValueError:
        return None


async def _master_context(message: Message) -> tuple | None:
    user = await users_repo.get_by_user_id(message.from_user.id)
    if user is None or not is_master_role(user.role):
        await message.answer("Этот раздел только для мастеров.")
        return None
    master = await masters_repo.get_by_telegram_user_id(message.from_user.id)
    if master is None:
        await message.answer(
            "Профиль мастера не привязан к вашему Telegram. Обратитесь к администратору."
        )
        return None
    return user, master


async def _render_master_day(master_id: int, target_date: date) -> str:
    appts = await appointments_repo.list_by_date_for_master(target_date, master_id)
    if not appts:
        return (
            "На сегодня записей нет 📭"
            if target_date == date.today()
            else "На этот день записей нет 📭"
        )
    services = await services_repo.list_all()
    services_map = {s.id: s for s in services}
    total_sum = 0
    lines: list[str] = [f"📋 Записи на {_weekday_ru(target_date)}, {target_date.strftime('%d.%m')}:\n"]
    idx = 1
    for appt in appts:
        client = await users_repo.get_by_user_id(appt.user_id)
        if client is None:
            continue
        service = services_map.get(appt.service_id)
        service_name = service.name if service is not None else f"Услуга #{appt.service_id}"
        service_price = service.price_byn if service is not None else 0
        total_sum += service_price
        safe_phone = html.escape(client.phone)
        safe_name = html.escape(client.name)
        lines.append(
            f"{idx}. #{appt.id} {appt.start_time.strftime('%H:%M')} — {safe_name}\n"
            f"   {html.escape(service_name)} — {service_price} BYN\n"
            f"   📞 {safe_phone}\n"
        )
        idx += 1
    lines.append(f"━━━━━━━━━━━━━━━━━━\nВсего: {idx - 1} записей | Сумма: {total_sum} BYN")
    return "\n".join(lines)


async def _render_master_all_future(master_id: int) -> str:
    appts = await appointments_repo.list_confirmed_from_date_for_master(date.today(), master_id)
    if not appts:
        return "Будущих записей нет 📭"
    services = await services_repo.list_all()
    services_map = {s.id: s for s in services}
    lines: list[str] = [f"📆 Все будущие записи ({len(appts)}):\n"]
    for appt in appts:
        client = await users_repo.get_by_user_id(appt.user_id)
        service = services_map.get(appt.service_id)
        service_name = service.name if service is not None else f"Услуга #{appt.service_id}"
        name = html.escape(client.name) if client else "—"
        lines.append(
            f"#{appt.id} {_human_booking_date(appt.date)} {appt.start_time.strftime('%H:%M')} | {name}\n"
            f"   {html.escape(service_name)}\n"
        )
    return "\n".join(lines)


@router.message(F.text == "📋 Ко мне сегодня")
async def master_today(message: Message) -> None:
    ctx = await _master_context(message)
    if ctx is None:
        return
    _, master = ctx
    await message.answer(await _render_master_day(master.id, date.today()))


@router.message(F.text == "📋 Ко мне завтра")
async def master_tomorrow(message: Message) -> None:
    ctx = await _master_context(message)
    if ctx is None:
        return
    _, master = ctx
    await message.answer(await _render_master_day(master.id, date.today() + timedelta(days=1)))


@router.message(F.text == "📆 Все записи ко мне")
async def master_all_future(message: Message) -> None:
    ctx = await _master_context(message)
    if ctx is None:
        return
    _, master = ctx
    await message.answer(await _render_master_all_future(master.id))


@router.message(F.text == "⏰ Мои рабочие часы")
async def master_show_hours(message: Message) -> None:
    ctx = await _master_context(message)
    if ctx is None:
        return
    _, master = ctx
    await message.answer(
        f"Сейчас: {master.work_start.strftime('%H:%M')} — {master.work_end.strftime('%H:%M')}\n\n"
        "Чтобы изменить, отправь команду:\n"
        "/my_hours 10:00 18:00"
    )


@router.message(Command("my_hours"))
async def master_set_hours(message: Message) -> None:
    ctx = await _master_context(message)
    if ctx is None:
        return
    _, master = ctx
    parts = (message.text or "").strip().split()
    if len(parts) != 3:
        await message.answer("Формат: /my_hours 10:00 18:00")
        return
    start_t = _parse_hhmm(parts[1])
    end_t = _parse_hhmm(parts[2])
    if start_t is None or end_t is None:
        await message.answer("Некорректное время. Пример: /my_hours 10:00 18:00")
        return
    if start_t >= end_t:
        await message.answer("Время начала должно быть меньше окончания.")
        return
    ok = await masters_repo.set_work_hours(master.master_key, start_t, end_t)
    if ok:
        await message.answer(
            f"График обновлён ✅: {start_t.strftime('%H:%M')}–{end_t.strftime('%H:%M')}",
            reply_markup=master_menu_keyboard(),
        )
    else:
        await message.answer("Не удалось сохранить график. Попробуйте позже.")



@router.message(Command("master_help"))
async def master_help(message: Message) -> None:
    user = await users_repo.get_by_user_id(message.from_user.id)
    if user is None or not is_master_role(user.role):
        return
    await message.answer(
        "Кабинет мастера:\n"
        "— Кнопки «Ко мне сегодня/завтра» и список будущих записей\n"
        "— /my_hours HH:MM HH:MM — ваши рабочие часы\n"
        f"Роль в системе: {ROLE_MASTER}",
        reply_markup=menu_keyboard_for_role(user.role),
    )
