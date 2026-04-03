"""
/**
 * @file: callback_safe.py
 * @description: Безопасный ответ на callback_query (истёкшее окно Telegram)
 * @dependencies: aiogram
 * @created: 2026-04-03
 */
"""

from __future__ import annotations

from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery


def _is_callback_query_expired_error(exc: TelegramBadRequest) -> bool:
    desc = (getattr(exc, "message", None) or str(exc)).lower()
    return (
        "query is too old" in desc
        or "response timeout expired" in desc
        or "query id is invalid" in desc
    )


async def safe_callback_answer(callback: CallbackQuery, *args: Any, **kwargs: Any) -> None:
    """
    Обертка над callback.answer: истёкший query (~30 с) не роняет хендлер.
    """
    try:
        await callback.answer(*args, **kwargs)
    except TelegramBadRequest as e:
        if _is_callback_query_expired_error(e):
            return
        raise
