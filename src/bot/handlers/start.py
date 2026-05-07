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
from src.app.services.master_invite_service import MasterInviteService
from src.infra.auth.roles import ROLE_MASTER
from src.bot.handlers.states import RegistrationStates
from src.bot.keyboards.main_menu import menu_keyboard_for_role

router = Router()
booking_service = BookingService()
invite_service = MasterInviteService()

PENDING_INVITE_KEY = "pending_master_invite_token"


def _redeem_error_ru(code: str) -> str:
    return {
        "not_found": "приглашение не найдено",
        "expired_or_used": "ссылка уже использована или срок действия истёк",
        "not_registered": "нужна регистрация в боте",
        "already_master": "этот аккаунт уже подключён как мастер",
        "db_error": "не удалось записать в базу (возможно, дублируется имя мастера)",
    }.get(code, code)


def _start_args(message: Message) -> str | None:
    text = (message.text or "").strip()
    if not text.lower().startswith("/start"):
        return None
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return None
    return parts[1].strip() or None


@router.message(CommandStart())
async def start_command(message: Message, state: FSMContext) -> None:
    user_id = message.from_user.id
    token = invite_service.parse_payload(_start_args(message))

    if token:
        if not await invite_service.is_token_valid(token):
            await message.answer(
                "Приглашение недействительно: срок истёк или ссылка уже была использована."
            )
            await state.clear()
            return

    existing = await booking_service.get_user(user_id)

    if existing is not None:
        if token:
            ok, err, master = await invite_service.redeem(token, user_id)
            if ok and master:
                await message.answer(
                    f"Готово ✅ Ты подключён как мастер «{master.name}».\n"
                    "Используй меню ниже — там твои записи и рабочие часы.",
                    reply_markup=menu_keyboard_for_role(ROLE_MASTER),
                )
            else:
                await message.answer(
                    "Не удалось принять приглашение: "
                    f"{_redeem_error_ru(err)}.\n"
                    "Напиши администратору.",
                    reply_markup=menu_keyboard_for_role(existing.role),
                )
            await state.clear()
            return
        await message.answer(
            f"Привет, {existing.name} 👋\nБарбер Илья на связи.\nВыбери действие в меню ниже.",
            reply_markup=menu_keyboard_for_role(existing.role),
        )
        await state.clear()
        return

    if token:
        await state.update_data(**{PENDING_INVITE_KEY: token})

    contact_button = KeyboardButton(text="Поделиться контактом", request_contact=True)
    intro = (
        "Ты перешёл по приглашению мастера.\n"
        "Сначала заверши короткую регистрацию — после неё откроется кабинет мастера.\n\n"
        "Отправь номер телефона 📱"
        if token
        else "Отправь номер телефона 📱"
    )
    await message.answer(
        intro,
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
        await message.answer("Как тебя зовут? 👤")
        return

    data = await state.get_data()
    phone = str(data.get("phone", "")).strip()
    user_id = message.from_user.id
    pending = str(data.get(PENDING_INVITE_KEY) or "").strip()

    await booking_service.register_user(user_id=user_id, phone=phone, name=name)

    if pending:
        ok, err, master = await invite_service.redeem(pending, user_id)
        await state.clear()
        if ok and master:
            await message.answer(
                f"Регистрация завершена ✅ Ты подключён как мастер «{master.name}».",
                reply_markup=menu_keyboard_for_role(ROLE_MASTER),
            )
        else:
            await message.answer(
                f"Регистрация прошла, но по приглашению не вышло стать мастером: {_redeem_error_ru(err)}.\n"
                "Обратись к администратору — он может выдать доступ вручную.",
                reply_markup=menu_keyboard_for_role("client"),
            )
        return

    await state.clear()

    await message.answer(
        f"Спасибо, {name}! Теперь ты можешь записаться.\nВыбери действие:",
        reply_markup=menu_keyboard_for_role("client"),
    )
