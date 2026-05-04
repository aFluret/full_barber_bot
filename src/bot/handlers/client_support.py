"""
/**
 * @file: client_support.py
 * @description: Клиентские информационные команды (контакты/связь с админом)
 * @dependencies: infra.config.settings, users repository, FSM
 * @created: 2026-05-04
 */
"""

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from src.bot.handlers.states import SupportStates
from src.bot.keyboards.main_menu import menu_keyboard_for_role
from src.infra.config.settings import get_settings
from src.infra.db.repositories.users_repository import UsersRepository

router = Router()
users_repo = UsersRepository()


@router.message(F.text == "📍 Контакты")
async def contacts(message: Message) -> None:
    settings = get_settings()
    await message.answer(settings.contacts_text.strip() or "Контакты пока не настроены.")


@router.message(F.text == "💬 Связаться с админом")
async def contact_admin(message: Message, state: FSMContext) -> None:
    settings = get_settings()
    await message.answer(
        (
            "Напиши сообщение для администратора одним сообщением.\n"
            "Чтобы отменить, отправь «Отмена».\n\n"
            f"{settings.admin_contact_text.strip() or ''}"
        ).strip()
    )
    await state.set_state(SupportStates.waiting_message)


@router.message(SupportStates.waiting_message, F.text.casefold() == "отмена")
async def contact_admin_cancel(message: Message, state: FSMContext) -> None:
    user = await users_repo.get_by_user_id(message.from_user.id)
    role = user.role if user else "client"
    await state.clear()
    await message.answer("Отправка сообщения отменена.", reply_markup=menu_keyboard_for_role(role))


@router.message(SupportStates.waiting_message)
async def contact_admin_send(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer("Сообщение пустое. Напиши текст или отправь «Отмена».")
        return

    user = await users_repo.get_by_user_id(message.from_user.id)
    user_name = user.name if user and user.name else (message.from_user.full_name or "Клиент")
    user_phone = user.phone if user and user.phone else "не указан"
    user_role = user.role if user else "client"
    admins = await users_repo.list_admins()
    if not admins:
        await state.clear()
        await message.answer(
            "Сейчас нет доступных администраторов. Попробуй позже.",
            reply_markup=menu_keyboard_for_role(user_role),
        )
        return

    notify_text = (
        "💬 Новое сообщение от клиента\n\n"
        f"Имя: {user_name}\n"
        f"Telegram ID: {message.from_user.id}\n"
        f"Телефон: {user_phone}\n\n"
        f"Сообщение:\n{text}"
    )
    for admin in admins:
        try:
            await message.bot.send_message(chat_id=admin.user_id, text=notify_text)
        except Exception:
            continue

    await state.clear()
    await message.answer(
        "Сообщение отправлено администратору ✅",
        reply_markup=menu_keyboard_for_role(user_role),
    )
