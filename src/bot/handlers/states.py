"""
/**
 * @file: states.py
 * @description: FSM states для сценария регистрации и записи
 * @dependencies: aiogram
 * @created: 2026-03-23
 */
"""

from aiogram.fsm.state import State, StatesGroup


class RegistrationStates(StatesGroup):
    waiting_contact = State()
    waiting_name = State()


class BookingStates(StatesGroup):
    waiting_date = State()
    waiting_time = State()
    waiting_confirm = State()


class AdminPanelStates(StatesGroup):
    waiting_access_code = State()
    in_menu = State()

