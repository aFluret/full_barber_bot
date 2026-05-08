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
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from src.bot.callback_safe import safe_callback_answer
from src.bot.handlers.states import SupportStates
from src.bot.keyboards.main_menu import menu_keyboard_for_role
from src.infra.auth.roles import ROLE_MASTER, normalize_role
from src.infra.config.settings import get_settings
from src.infra.db.repositories.users_repository import UsersRepository

router = Router()
users_repo = UsersRepository()

CONTACT_ADMIN_CANCEL_CB = "client_support:cancel_admin_message"
CONTACT_PROMPT_MID_KEY = "support_contact_prompt_message_id"


async def _remove_contact_prompt_keyboard(chat_message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    mid = data.get(CONTACT_PROMPT_MID_KEY)
    if isinstance(mid, int):
        try:
            await chat_message.bot.edit_message_reply_markup(
                chat_id=chat_message.chat.id,
                message_id=mid,
                reply_markup=None,
            )
        except Exception:
            pass


async def _finish_contact_admin_cancel(user_id: int, reply_target: Message, state: FSMContext) -> None:
    await _remove_contact_prompt_keyboard(reply_target, state)
    user = await users_repo.get_by_user_id(user_id)
    role = user.role if user else "client"
    await state.clear()
    await reply_target.answer(
        "Отправка сообщения отменена.",
        reply_markup=menu_keyboard_for_role(role),
    )


@router.message(F.text == "📍 Контакты")
async def contacts(message: Message) -> None:
    settings = get_settings()
    await message.answer(settings.contacts_text.strip() or "Контакты пока не настроены.")


@router.message(F.text == "💬 Связаться с админом")
async def contact_admin(message: Message, state: FSMContext) -> None:
    settings = get_settings()
    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Отмена", callback_data=CONTACT_ADMIN_CANCEL_CB)]]
    )
    sent = await message.answer(
        (
            "Напиши сообщение для администратора одним сообщением.\n"
            "Чтобы отменить, нажми кнопку «Отмена» ниже.\n\n"
            f"{settings.admin_contact_text.strip() or ''}"
        ).strip(),
        reply_markup=cancel_kb,
    )
    await state.set_state(SupportStates.waiting_message)
    await state.update_data(**{CONTACT_PROMPT_MID_KEY: sent.message_id})


@router.callback_query(SupportStates.waiting_message, F.data == CONTACT_ADMIN_CANCEL_CB)
async def contact_admin_cancel_callback(callback: CallbackQuery, state: FSMContext) -> None:
    await _finish_contact_admin_cancel(callback.from_user.id, callback.message, state)
    await safe_callback_answer(callback)


@router.message(SupportStates.waiting_message, F.text.casefold() == "отмена")
async def contact_admin_cancel(message: Message, state: FSMContext) -> None:
    await _finish_contact_admin_cancel(message.from_user.id, message, state)


@router.message(SupportStates.waiting_message)
async def contact_admin_send(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if not text:
        await message.answer(
            "Сообщение пустое. Напиши текст, нажми «Отмена» под предыдущим сообщением или отправь «Отмена»."
        )
        return

    user = await users_repo.get_by_user_id(message.from_user.id)
    user_name = user.name if user and user.name else (message.from_user.full_name or "Клиент")
    user_phone = user.phone if user and user.phone else "не указан"
    user_role = user.role if user else "client"
    admins = await users_repo.list_admins()
    if not admins:
        await _remove_contact_prompt_keyboard(message, state)
        await state.clear()
        await message.answer(
            "Сейчас нет доступных администраторов. Попробуй позже.",
            reply_markup=menu_keyboard_for_role(user_role),
        )
        return

    who = "барбера" if normalize_role(user.role if user else None) == ROLE_MASTER else "клиента"
    notify_text = (
        f"💬 Новое сообщение от {who}\n\n"
        f"Имя: {user_name}\n"
        f"Телефон: {user_phone}\n\n"
        f"Сообщение:\n{text}"
    )
    for admin in admins:
        try:
            await message.bot.send_message(chat_id=admin.user_id, text=notify_text)
        except Exception:
            continue

    await _remove_contact_prompt_keyboard(message, state)
    await state.clear()
    await message.answer(
        "Сообщение отправлено администратору ✅",
        reply_markup=menu_keyboard_for_role(user_role),
    )
