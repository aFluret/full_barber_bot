"""
/**
 * @file: appointment.py
 * @description: Просмотр и отмена активной записи пользователя
 * @dependencies: app.services.booking_service, bot.keyboards.main_menu, fsm
 * @created: 2026-03-23
 */
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.app.services.booking_service import BookingService
from src.bot.keyboards.main_menu import menu_keyboard_for_role

router = Router()
booking_service = BookingService()


@router.message(F.text == "📋 Моя запись")
async def my_appointment(message: Message) -> None:
    user_id = message.from_user.id
    user = await booking_service.get_user(user_id)
    user_name = user.name if user and user.name else "Клиент"
    user_role = user.role if user else "client"
    appt = await booking_service.get_active_appointment(user_id)
    if appt is None:
        await message.answer(f"{user_name}, у тебя пока нет активной записи.", reply_markup=menu_keyboard_for_role(user_role))
        return

    await message.answer(
        f"{user_name}, твоя запись:\n"
        f"{appt.date.strftime('%d.%m.%Y')} в {appt.start_time.strftime('%H:%M')}–{appt.end_time.strftime('%H:%M')}",
        reply_markup=menu_keyboard_for_role(user_role),
    )


@router.message(F.text == "❌ Отменить запись")
async def cancel_appointment(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    user = await booking_service.get_user(user_id)
    user_name = user.name if user and user.name else "Клиент"
    user_role = user.role if user else "client"
    await state.clear()

    appt = await booking_service.cancel_active_appointment(user_id)
    if appt is None:
        await message.answer(f"{user_name}, активной записи не найдено.", reply_markup=menu_keyboard_for_role(user_role))
        return

    await message.answer(
        f"{user_name}, запись отменена. Слот снова доступен.",
        reply_markup=menu_keyboard_for_role(user_role),
    )
