"""
/**
 * @file: client_support.py
 * @description: Клиентские информационные команды (контакты/связь с админом)
 * @dependencies: infra.config.settings
 * @created: 2026-05-04
 */
"""

from aiogram import F, Router
from aiogram.types import Message

from src.infra.config.settings import get_settings

router = Router()


@router.message(F.text == "📍 Контакты")
async def contacts(message: Message) -> None:
    settings = get_settings()
    await message.answer(settings.contacts_text.strip() or "Контакты пока не настроены.")


@router.message(F.text == "💬 Связаться с админом")
async def contact_admin(message: Message) -> None:
    settings = get_settings()
    await message.answer(
        settings.admin_contact_text.strip()
        or "Контакт администратора пока не настроен. Напишите позже."
    )
