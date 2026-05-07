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
    reminder_text_24h: str = (
        "Напоминание: завтра у вас запись {date} в {time}."
    )
    reminder_text_2h: str = (
        "Напоминание: скоро запись {date} в {time}."
    )
    notify_client_created_text: str = (
        "Готово! Ты записан ✅\n\n"
        "Филиал: {branch}\n"
        "Мастер: {master}\n"
        "Услуга: {service}\n"
        "Длительность: {duration}\n"
        "Дата: {date}\n"
        "Время: {time}\n"
        "Стоимость: {price}\n\n"
        "Комментарий: {comment}\n\n"
        "Если планы изменятся — напиши заранее\n"
        "До встречи! ✂️"
    )
    notify_admin_created_text: str = (
        "🔥 Новая запись\n\n"
        "Клиент: {client}\n"
        "Филиал: {branch}\n"
        "Мастер: {master}\n"
        "Дата: {date}\n"
        "Время: {time}\n"
        "Услуга: {service}\n"
        "Телефон: {phone}\n"
        "Комментарий: {comment}"
    )
    notify_master_created_text: str = (
        "🧾 Новая запись к вам\n\n"
        "Клиент: {client}\n"
        "Телефон: {phone}\n"
        "Филиал: {branch}\n"
        "Услуга: {service}\n"
        "Дата: {date}\n"
        "Время: {time}\n"
        "Комментарий: {comment}"
    )
    notify_client_cancelled_text: str = (
        "Запись отменена ✅\n"
        "Можешь записаться заново на любую услугу."
    )
    notify_admin_cancelled_text: str = (
        "❌ Отмена записи\n\n"
        "Клиент: {client}\n"
        "Филиал: {branch}\n"
        "Мастер: {master}\n"
        "Услуга: {service}\n"
        "Дата: {date}\n"
        "Время: {time}"
    )
    notify_master_cancelled_text: str = (
        "❌ Клиент отменил запись\n\n"
        "Клиент: {client}\n"
        "Услуга: {service}\n"
        "Дата: {date}\n"
        "Время: {time}"
    )
    notify_client_rescheduled_text: str = (
        "Запись успешно перенесена ✅\n\n"
        "Услуга: {service}\n"
        "Дата: {new_date}\n"
        "Время: {new_time}"
    )
    notify_admin_rescheduled_text: str = (
        "🔄 Перенос записи\n\n"
        "Клиент: {client}\n"
        "Филиал: {branch}\n"
        "Мастер: {master}\n"
        "Услуга: {service}\n"
        "Было: {old_date}, {old_time}\n"
        "Стало: {new_date}, {new_time}"
    )
    notify_master_rescheduled_text: str = (
        "🔄 Запись перенесена\n\n"
        "Клиент: {client}\n"
        "Услуга: {service}\n"
        "Было: {old_date}, {old_time}\n"
        "Стало: {new_date}, {new_time}"
    )
    notify_client_no_show_text: str = (
        "Статус записи изменен на no-show.\n"
        "Если это ошибка, свяжитесь с администратором."
    )
    notify_admin_no_show_text: str = (
        "🚫 No-show\n\n"
        "Клиент: {client}\n"
        "Филиал: {branch}\n"
        "Мастер: {master}\n"
        "Услуга: {service}\n"
        "Дата: {date}\n"
        "Время: {time}"
    )
    notify_master_no_show_text: str = (
        "🚫 Клиент не пришел (no-show)\n\n"
        "Клиент: {client}\n"
        "Услуга: {service}\n"
        "Дата: {date}\n"
        "Время: {time}"
    )
    contacts_text: str = "Барбершоп: ул. Пример, 1\nТелефон: +375 (00) 000-00-00"
    admin_contact_text: str = "Напишите администратору: @barber_admin"
    booking_mode: str = "solo"  # solo | barbershop
    branches_csv: str = "Основной филиал"
    masters_csv: str = "Илья"
    enable_any_master_option: bool = True
    # Неиспользуется кодом: можно держать список id для документирования bootstrap (роль admin в БД).
    admin_bootstrap_user_ids: str = ""
    # Формат: "ilya:123456789,maksim:987654321" (master_key -> telegram user_id мастера)
    master_telegram_map: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
