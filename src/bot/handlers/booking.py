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
from datetime import date

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

    await state.set_state(BookingStates.waiting_service)

    services = await services_repo.list_all()
    if not services:
        await message.answer(
            "Сейчас запись недоступна: администратор еще не добавил услуги.\n"
            "Напишите администратору и попробуйте позже."
        )
        return

    prompt = await message.answer(
        "Выбери услугу для записи:",
        reply_markup=services_picker_keyboard(services),
    )
    await state.update_data(booking_prompt_message_id=prompt.message_id)


@router.callback_query(F.data.startswith("bk_service:"))
async def choose_service(callback: CallbackQuery, state: FSMContext) -> None:
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
        reply_markup=date_picker_keyboard(dates),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bk_date:"))
async def choose_date(callback: CallbackQuery, state: FSMContext) -> None:
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
            reply_markup=date_picker_keyboard(await schedule_service.next_working_dates(7)),
        )
        await state.set_state(BookingStates.waiting_date)
        await callback.answer()
        return

    await _safe_edit_booking_message(
        callback,
        f"Дата: {target_date.strftime('%d.%m.%Y')}\nВыбери время для записи:",
        reply_markup=time_picker_keyboard(slots),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("bk_time:"))
async def choose_time(callback: CallbackQuery, state: FSMContext) -> None:
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
        f"Подтверди запись:\n{booking_date.strftime('%d.%m.%Y')} в {time_slot}",
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
                reply_markup=date_picker_keyboard(await schedule_service.next_working_dates(7)),
            )
        else:
            await _safe_edit_booking_message(
                callback,
                f"Дата: {booking_date.strftime('%d.%m.%Y')}\nВыбери время для записи:",
                reply_markup=time_picker_keyboard(slots),
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
                reply_markup=time_picker_keyboard(slots),
            )
        else:
            await _safe_edit_booking_message(
                callback,
                "Место уже занято, а свободных мест на эту дату больше нет. Выбери другую дату:",
                reply_markup=date_picker_keyboard(await schedule_service.next_working_dates(7)),
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

    # Уведомляем администраторов сразу после успешного подтверждения записи.
    admins = await users_repo.list_admins()
    if admins and user is not None:
        service = await services_repo.get_by_id(appointment.service_id)
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
        f"{user_name}, ты записан на {appointment.date.strftime('%d.%m.%Y')} в {appointment.start_time.strftime('%H:%M')}",
        reply_markup=menu_keyboard_for_role(user.role if user else "client"),
    )
    await callback.answer()

