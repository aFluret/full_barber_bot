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
from datetime import date, timedelta

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
from src.app.services.schedule_service import ScheduleService
from src.bot.handlers.states import BookingStates
from src.bot.keyboards.booking import (
    categories_picker_keyboard,
    confirm_booking_keyboard,
    date_picker_keyboard,
    services_picker_keyboard,
    time_picker_keyboard,
)
from src.bot.keyboards.main_menu import menu_keyboard_for_role

router = Router()
booking_service = BookingService()
schedule_service = ScheduleService()
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
        await callback.answer("Сначала выберите категорию.", show_alert=True)
        return

    payload = callback.data.split(":", 1)[1].strip()
    if payload not in SERVICE_CATEGORIES:
        await callback.answer("Некорректная категория.", show_alert=True)
        return

    await state.update_data(booking_category_key=payload)
    await state.set_state(BookingStates.waiting_service)

    services = await services_repo.list_all()
    cat_services = _category_services(services, payload)
    if not cat_services:
        await _safe_edit_booking_message(callback, "В этой категории услуги недоступны.")
        await callback.answer()
        return

    await _safe_edit_booking_message(
        callback,
        "Выбери услугу ✂️",
        reply_markup=services_picker_keyboard(cat_services, back_callback_data="bk_back:category"),
    )
    await callback.answer()


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
    await callback.answer()


@router.callback_query(F.data == "bk_restart_service")
async def restart_booking_from_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    user_id = callback.from_user.id
    existing = await booking_service.get_user(user_id)
    if existing is None:
        await callback.message.answer("Сначала пройдите регистрацию: нажмите /start.")
        await callback.answer()
        return

    services = await services_repo.list_all()
    if not services:
        await callback.message.answer(
            "Сейчас запись недоступна: администратор еще не добавил услуги.\n"
            "Напишите администратору и попробуйте позже."
        )
        await callback.answer()
        return

    categories = _build_categories_present(services)
    if not categories:
        await callback.message.answer("Список услуг недоступен.")
        await callback.answer()
        return

    await state.set_state(BookingStates.waiting_category)
    await callback.message.answer(
        "Выбери категорию ✂️",
        reply_markup=categories_picker_keyboard(categories),
    )
    await callback.answer()


@router.callback_query(F.data == "bk_back:category")
async def back_to_category(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() not in {BookingStates.waiting_service.state, BookingStates.waiting_date.state}:
        await callback.answer("Сначала выберите категорию.", show_alert=True)
        return

    await state.set_state(BookingStates.waiting_category)
    services = await services_repo.list_all()
    categories = _build_categories_present(services)

    await _safe_edit_booking_message(
        callback,
        "Выбери категорию ✂️",
        reply_markup=categories_picker_keyboard(categories),
    )
    await callback.answer()


@router.callback_query(F.data == "bk_back:date")
async def back_to_date(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != BookingStates.waiting_time.state:
        await callback.answer("Сначала выбери время.", show_alert=True)
        return

    data = await state.get_data()
    service_id = data.get("booking_service_id")
    if not service_id:
        await callback.answer("Сначала выбери услугу.", show_alert=True)
        return

    await state.set_state(BookingStates.waiting_date)
    dates = await schedule_service.next_working_dates(7)
    if not dates:
        await _safe_edit_booking_message(callback, "На ближайшее время рабочие дни недоступны.")
        await callback.answer()
        return

    await _safe_edit_booking_message(
        callback,
        "Выбери дату для записи:",
        reply_markup=date_picker_keyboard(dates, back_callback_data="bk_back:category"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bk_service:"))
async def choose_service(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != BookingStates.waiting_service.state:
        await callback.answer("Сначала выберите категорию.", show_alert=True)
        return

    payload = callback.data.split(":", 1)[1].strip()
    try:
        service_id = int(payload)
    except ValueError:
        await callback.answer("Некорректная услуга.", show_alert=True)
        return

    await state.update_data(booking_service_id=service_id)
    await state.set_state(BookingStates.waiting_date)

    dates = await schedule_service.next_working_dates(7)
    if not dates:
        await _safe_edit_booking_message(callback, "На ближайшее время рабочие дни недоступны.")
        await callback.answer()
        return

    await _safe_edit_booking_message(
        callback,
        "Выбери дату для записи:",
        reply_markup=date_picker_keyboard(dates, back_callback_data="bk_back:category"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bk_date:"))
async def choose_date(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != BookingStates.waiting_date.state:
        await callback.answer("Сначала выбери дату.", show_alert=True)
        return

    payload = callback.data.split(":", 1)[1]
    try:
        target_date = date.fromisoformat(payload)
    except ValueError:
        await callback.answer("Некорректная дата.", show_alert=True)
        return

    await state.update_data(booking_date=target_date.isoformat())
    await state.set_state(BookingStates.waiting_time)

    data = await state.get_data()
    service_id = data.get("booking_service_id")
    if not service_id:
        await callback.answer("Сначала выбери услугу.", show_alert=True)
        return

    slots = await booking_service.list_available_time_slots(target_date, service_id=int(service_id))
    if not slots:
        await _safe_edit_booking_message(
            callback,
            "На выбранную дату свободных мест нет. Выбери другую дату:",
            reply_markup=date_picker_keyboard(await schedule_service.next_working_dates(7), back_callback_data="bk_back:category"),
        )
        await state.set_state(BookingStates.waiting_date)
        await callback.answer()
        return

    await _safe_edit_booking_message(
        callback,
        f"Дата: {target_date.strftime('%d.%m.%Y')}\nВыбери время для записи:",
        reply_markup=time_picker_keyboard(slots, back_callback_data="bk_back:date"),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bk_time:"))
async def choose_time(callback: CallbackQuery, state: FSMContext) -> None:
    if await state.get_state() != BookingStates.waiting_time.state:
        await callback.answer("Сначала выбери время.", show_alert=True)
        return

    time_slot = callback.data.split(":", 1)[1].strip()
    if not time_slot:
        await callback.answer("Некорректное время.", show_alert=True)
        return

    data = await state.get_data()
    booking_date_iso = data.get("booking_date")
    if not booking_date_iso:
        await callback.answer("Сначала выбери дату.", show_alert=True)
        return

    await state.update_data(booking_time=time_slot)
    await state.set_state(BookingStates.waiting_confirm)

    booking_date = date.fromisoformat(str(booking_date_iso))
    await _safe_edit_booking_message(
        callback,
        f"Подтверди запись:\n{_human_booking_date(booking_date)} в {time_slot}",
        reply_markup=confirm_booking_keyboard(),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bk_confirm:"))
async def confirm_or_back(callback: CallbackQuery, state: FSMContext) -> None:
    action = callback.data.split(":", 1)[1]
    data = await state.get_data()

    booking_date_iso = data.get("booking_date")
    booking_time = data.get("booking_time")
    service_id = data.get("booking_service_id")
    if not booking_date_iso:
        await callback.answer("Сначала выбери дату.", show_alert=True)
        return

    booking_date = date.fromisoformat(str(booking_date_iso))

    if action == "0":
        # Назад к выбору времени.
        await state.set_state(BookingStates.waiting_time)
        if not service_id:
            await callback.answer("Сначала выбери услугу.", show_alert=True)
            return
        slots = await booking_service.list_available_time_slots(booking_date, service_id=int(service_id))
        if not slots:
            await state.set_state(BookingStates.waiting_date)
            await _safe_edit_booking_message(
                callback,
                "Свободных мест больше нет. Выбери другую дату:",
                reply_markup=date_picker_keyboard(await schedule_service.next_working_dates(7), back_callback_data="bk_back:category"),
            )
        else:
            await _safe_edit_booking_message(
                callback,
                f"Дата: {booking_date.strftime('%d.%m.%Y')}\nВыбери время для записи:",
                reply_markup=time_picker_keyboard(slots, back_callback_data="bk_back:date"),
            )
        await callback.answer()
        return

    if action != "1":
        await callback.answer()
        return

    if not booking_time:
        await callback.answer("Сначала выбери время.", show_alert=True)
        return

    if not service_id:
        await callback.answer("Сначала выбери услугу.", show_alert=True)
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
        await callback.answer()
        return
    except SlotUnavailableError:
        # Слот мог стать занятым между отображением и подтверждением.
        if not service_id:
            await callback.answer("Сначала выбери услугу.", show_alert=True)
            return
        slots = await booking_service.list_available_time_slots(booking_date, service_id=int(service_id))
        if slots:
            await _safe_edit_booking_message(
                callback,
                "Место уже занято. Выбери другое время:",
                reply_markup=time_picker_keyboard(slots, back_callback_data="bk_back:date"),
            )
        else:
            await _safe_edit_booking_message(
                callback,
                "Место уже занято, а свободных мест на эту дату больше нет. Выбери другую дату:",
                reply_markup=date_picker_keyboard(await schedule_service.next_working_dates(7), back_callback_data="bk_back:category"),
            )
            await state.set_state(BookingStates.waiting_date)
        await callback.answer()
        return
    except Exception:
        # Чтобы пользователь не видел "тишину" при внутренних сбоях.
        await _safe_edit_booking_message(
            callback,
            "Произошла ошибка при создании записи. Попробуй ещё раз.",
        )
        await callback.answer()
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
    await callback.answer()

