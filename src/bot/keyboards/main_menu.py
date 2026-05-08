"""
/**
 * @file: main_menu.py
 * @description: Главное меню клиента
 * @dependencies: aiogram.types
 * @created: 2026-03-23
 */
"""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from src.infra.auth.roles import ROLE_ADMIN, ROLE_MASTER, normalize_role

# Тексты кнопок админ-меню (синхронизировать с фильтрами F.text в admin.py)
ADMIN_KB_TODAY = "📋 Записи сегодня"
ADMIN_KB_TOMORROW = "📋 Записи завтра"
ADMIN_KB_ALL = "📚 Все будущие записи"
ADMIN_KB_STATS = "📊 Статистика"
ADMIN_KB_MASTER_LOAD = "📈 Загрузка мастеров"
ADMIN_KB_SERVICES = "🛠️ Услуги"
ADMIN_KB_SCHEDULE = "⏰ Текущий график"
ADMIN_KB_SET_SCHEDULE = "📆 Изменить график"
ADMIN_KB_MASTERS = "👨‍🔧 Мастера"
ADMIN_KB_BRANCHES = "🏢 Филиалы"
ADMIN_KB_HELP = "Помощь"


def main_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📅 Записаться")],
            [KeyboardButton(text="📚 Мои записи"), KeyboardButton(text="🔄 Перенести запись")],
            [KeyboardButton(text="❌ Отменить запись")],
            [KeyboardButton(text="📍 Контакты"), KeyboardButton(text="💬 Связаться с админом")],
        ],
        resize_keyboard=True,
    )


def master_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="📋 Ко мне сегодня"), KeyboardButton(text="📋 Ко мне завтра")],
            [KeyboardButton(text="📆 Все записи ко мне")],
            [KeyboardButton(text="⏰ Мои рабочие часы")],
            [KeyboardButton(text="📍 Контакты"), KeyboardButton(text="💬 Связаться с админом")],
        ],
        resize_keyboard=True,
    )


def admin_menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=ADMIN_KB_TODAY), KeyboardButton(text=ADMIN_KB_TOMORROW)],
            [KeyboardButton(text=ADMIN_KB_ALL)],
            [KeyboardButton(text=ADMIN_KB_STATS), KeyboardButton(text=ADMIN_KB_MASTER_LOAD)],
            [KeyboardButton(text=ADMIN_KB_SERVICES)],
            [KeyboardButton(text=ADMIN_KB_SCHEDULE), KeyboardButton(text=ADMIN_KB_SET_SCHEDULE)],
            [KeyboardButton(text=ADMIN_KB_MASTERS), KeyboardButton(text=ADMIN_KB_BRANCHES)],
            [KeyboardButton(text="📍 Контакты"), KeyboardButton(text=ADMIN_KB_HELP)],
        ],
        resize_keyboard=True,
    )


def menu_keyboard_for_role(role: str | None) -> ReplyKeyboardMarkup:
    r = normalize_role(role)
    if r == ROLE_ADMIN:
        return admin_menu_keyboard()
    if r == ROLE_MASTER:
        return master_menu_keyboard()
    return main_menu_keyboard()
