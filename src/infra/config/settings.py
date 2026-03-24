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
    supabase_url: str = ""
    supabase_service_role_key: str = ""
    app_timezone: str = "Europe/Minsk"
    reminder_24h_offset_minutes: int = 24 * 60
    reminder_2h_offset_minutes: int = 2 * 60

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
