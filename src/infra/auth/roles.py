"""
Роли пользователя бота и нормализация значений из БД.
"""

from __future__ import annotations

ROLE_ADMIN = "admin"
ROLE_CLIENT = "client"
ROLE_MASTER = "master"

# Устаревшее имя роли в черновиках / старых данных
_LEGACY_BARBER = "barber"


def normalize_role(raw: str | None) -> str:
    if raw is None:
        return ROLE_CLIENT
    r = str(raw).strip().lower()
    if not r:
        return ROLE_CLIENT
    if r == _LEGACY_BARBER:
        return ROLE_MASTER
    if r in (ROLE_ADMIN, ROLE_CLIENT, ROLE_MASTER):
        return r
    return ROLE_CLIENT


def is_admin_role(role: str | None) -> bool:
    return normalize_role(role) == ROLE_ADMIN


def is_master_role(role: str | None) -> bool:
    return normalize_role(role) == ROLE_MASTER


def is_client_role(role: str | None) -> bool:
    return normalize_role(role) == ROLE_CLIENT
