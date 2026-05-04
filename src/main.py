"""
/**
 * @file: main.py
 * @description: Точка входа Telegram-бота для MVP
 * @dependencies: bot.handlers, infra.config.settings
 * @created: 2026-03-23
 */
"""

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.bot import DefaultBotProperties
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.bot.handlers.admin import router as admin_router
from src.bot.handlers.appointment import router as appointment_router
from src.bot.handlers.booking import router as booking_router
from src.bot.handlers.client_support import router as client_support_router
from src.bot.handlers.start import router as start_router
from src.app.services.reminder_service import ReminderService
from src.infra.config.settings import get_settings


def build_dispatcher() -> Dispatcher:
    dispatcher = Dispatcher(storage=MemoryStorage())
    dispatcher.include_router(start_router)
    dispatcher.include_router(client_support_router)
    dispatcher.include_router(booking_router)
    dispatcher.include_router(appointment_router)
    dispatcher.include_router(admin_router)
    return dispatcher


async def main() -> None:
    settings = get_settings()
    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode="HTML"),
    )
    dispatcher = build_dispatcher()

    # Воркер напоминаний (MVP): раз в 60 секунд ищем due/unsent записи в Supabase
    # и отправляем сообщения клиентам. Это переживает рестарты бота.
    reminder_service = ReminderService()
    scheduler = AsyncIOScheduler(timezone="UTC")

    async def _schedule_job() -> None:
        await reminder_service.send_due_reminders(bot)

    scheduler.add_job(_schedule_job, trigger="interval", seconds=60, max_instances=1, coalesce=True)
    scheduler.start()

    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
