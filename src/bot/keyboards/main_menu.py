"""
/**
 * @file: main_menu.py
 * @description: Главное меню клиента
 * @dependencies: aiogram.types
 * @created: 2026-03-23
 */
"""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Записаться")],
            [KeyboardButton(text="📋 Моя запись")],
            [KeyboardButton(text="❌ Отменить запись")],
        ],
        resize_keyboard=True,
    )


def admin_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="/today"), KeyboardButton(text="/tomorrow")],
            [KeyboardButton(text="/all")],
            [KeyboardButton(text="/schedule"), KeyboardButton(text="/set_schedule")],
            [KeyboardButton(text="/exit")],
        ],
        resize_keyboard=True,
    )


def menu_keyboard_for_role(role: str | None) -> ReplyKeyboardMarkup:
    if (role or "").strip().lower() == "admin":
        return admin_menu_keyboard()
    return main_menu_keyboard()
