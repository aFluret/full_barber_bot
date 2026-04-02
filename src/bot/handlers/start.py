"""
/**
 * @file: start.py
 * @description: Регистрация пользователя и показ главного меню
 * @dependencies: app.services.booking_service, bot.keyboards.main_menu, fsm states
 * @created: 2026-03-23
 */
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import KeyboardButton, Message, ReplyKeyboardMarkup

from src.app.services.booking_service import BookingService
from src.bot.handlers.states import RegistrationStates
from src.bot.keyboards.main_menu import menu_keyboard_for_role

router = Router()
booking_service = BookingService()


@router.message(CommandStart())
async def start_command(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    existing = await booking_service.get_user(user_id)
    if existing is not None:
        await message.answer(
            f"Привет, {existing.name} 👋\nБарбер Илья на связи.\nВыбери действие в меню ниже.",
            reply_markup=menu_keyboard_for_role(existing.role),
        )
        await state.clear()
        return

    contact_button = KeyboardButton(text="Поделиться контактом", request_contact=True)
    await message.answer(
        "Чтобы записать тебя на стрижку, поделись своим номером телефона.",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[contact_button]],
            resize_keyboard=True,
            one_time_keyboard=True,
        ),
    )
    await state.set_state(RegistrationStates.waiting_contact)


@router.message(RegistrationStates.waiting_contact, F.contact)
async def handle_contact(message: Message, state: FSMContext) -> None:
    if message.contact.user_id != message.from_user.id:
        await message.answer("Пожалуйста, отправьте ваш собственный контакт.")
        return

    phone = message.contact.phone_number
    await state.update_data(phone=phone)
    await state.set_state(RegistrationStates.waiting_name)
    await message.answer("Отлично, спасибо 👍. Как тебя зовут?\nНапиши имя, чтобы я знал как к тебе обращаться.")


@router.message(RegistrationStates.waiting_contact)
async def handle_contact_fallback(message: Message) -> None:
    await message.answer("Нужен контакт. Нажми кнопку «Поделиться контактом».")


@router.message(RegistrationStates.waiting_name)
async def handle_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Имя не должно быть пустым. Введи имя еще раз.")
        return

    data = await state.get_data()
    phone = str(data.get("phone", "")).strip()
    user_id = message.from_user.id

    await booking_service.register_user(user_id=user_id, phone=phone, name=name)
    await state.clear()

    await message.answer(
        f"Спасибо, {name}! Теперь ты можешь записаться.\nВыбери действие:",
        reply_markup=menu_keyboard_for_role("client"),
    )
