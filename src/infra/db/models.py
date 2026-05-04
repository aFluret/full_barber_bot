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
class ServiceModel:
    """
    Справочник услуг барбершопа.

    Используется для:
    - выбора услуги клиентом/админом;
    - расчета end_time по duration_minutes;
    - отображения цены в админке.
    """

    id: int
    name: str
    price_byn: int
    duration_minutes: int


@dataclass(slots=True)
class AppointmentModel:
    id: int
    user_id: int
    date: date
    service_id: int
    start_time: time
    end_time: time
    status: str
    created_at: datetime
    branch_name: Optional[str] = None
    master_name: Optional[str] = None
    master_key: Optional[str] = None
    comment: Optional[str] = None
    branch_id: Optional[int] = None
    master_id: Optional[int] = None


@dataclass(slots=True)
class BranchModel:
    id: int
    name: str
    address: str
    is_active: bool = True


@dataclass(slots=True)
class MasterModel:
    id: int
    master_key: str
    name: str
    work_start: time
    work_end: time
    is_active: bool = True
