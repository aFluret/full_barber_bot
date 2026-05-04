"""
/**
 * @file: settings.py
 * @description: Конфигурация приложения из переменных окружения
 * @dependencies: pydantic_settings
 * @created: 2026-03-23
 */
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    bot_token: str = "CHANGE_ME"
    admin_user_ids: str = ""
    # Код доступа для отдельной админ-панели по команде /admin.
    # Должен быть строкой, чтобы поддерживать любые значения (в т.ч. с ведущими нулями).
    admin_panel_access_code: str = "1111"
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    app_timezone: str = "Europe/Minsk"
    reminder_24h_offset_minutes: int = 24 * 60
    reminder_2h_offset_minutes: int = 2 * 60
    contacts_text: str = "Барбершоп: ул. Пример, 1\nТелефон: +375 (00) 000-00-00"
    admin_contact_text: str = "Напишите администратору: @barber_admin"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
