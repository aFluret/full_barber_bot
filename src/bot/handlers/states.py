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
    waiting_category = State()
    waiting_service = State()
    waiting_date = State()
    waiting_time = State()
    waiting_confirm = State()


class AdminPanelStates(StatesGroup):
    waiting_access_code = State()
    in_menu = State()


class AdminScheduleStates(StatesGroup):
    waiting_weekdays = State()
    waiting_start_time = State()
    waiting_end_time = State()
    waiting_lunch_time = State()
    waiting_confirm = State()

