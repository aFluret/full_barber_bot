"""
/**
 * @file: models.py
 * @description: MVP-модели данных (dataclass-представление)
 * @dependencies: dataclasses, datetime
 * @created: 2026-03-23
 */
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional


@dataclass(slots=True)
class UserModel:
    user_id: int
    phone: str
    name: str
    role: str
    created_at: Optional[datetime]


@dataclass(slots=True)
class AppointmentModel:
    id: int
    user_id: int
    date: date
    time_slot: time
    status: str
    created_at: datetime
