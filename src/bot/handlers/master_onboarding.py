"""
Онбординг мастера после принятия приглашения: филиал → имя для клиентов → рабочие часы в рамках графика барбершопа.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, ReplyKeyboardRemove

from src.app.services.schedule_service import ScheduleService
from src.bot.callback_safe import safe_callback_answer
from src.bot.handlers.states import MasterOnboardingStates
from src.bot.keyboards.main_menu import master_menu_keyboard
from src.infra.db.repositories.branches_repository import BranchesRepository
from src.infra.db.repositories.masters_repository import MastersRepository

router = Router()
branches_repo = BranchesRepository()
masters_repo = MastersRepository()
schedule_service = ScheduleService()

ONB_MASTER_KEY = "onb_master_key"


def _compact(t: time) -> str:
    return t.strftime("%H%M")


def _parse_compact(s: str) -> time | None:
    if len(s) != 4 or not s.isdigit():
        return None
    try:
        return datetime.strptime(s, "%H%M").time()
    except ValueError:
        return None


def _schedule_preset_keyboard(shop_start: time, shop_end: time) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    ss, se = shop_start.strftime("%H:%M"), shop_end.strftime("%H:%M")
    rows.append(
        [
            InlineKeyboardButton(
                text=f"🕐 {ss}–{se} (как у барбершопа)",
                callback_data=f"mon_onb:sc:{_compact(shop_start)}:{_compact(shop_end)}",
            )
        ]
    )
    base = datetime.combine(date.today(), shop_start)
    end_base = datetime.combine(date.today(), shop_end)
    duration_h = (end_base - base).total_seconds() / 3600.0 if end_base > base else 0.0

    if duration_h >= 8:
        early_end = (base + timedelta(hours=8)).time()
        if early_end <= shop_end and early_end > shop_start:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{ss}–{early_end.strftime('%H:%M')} (8 ч от открытия)",
                        callback_data=f"mon_onb:sc:{_compact(shop_start)}:{_compact(early_end)}",
                    )
                ]
            )
        late_start = (end_base - timedelta(hours=8)).time()
        if late_start >= shop_start and late_start < shop_end:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{late_start.strftime('%H:%M')}–{se} (8 ч до закрытия)",
                        callback_data=f"mon_onb:sc:{_compact(late_start)}:{_compact(shop_end)}",
                    )
                ]
            )

    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _ask_display_name(message: Message, state: FSMContext) -> None:
    await state.set_state(MasterOnboardingStates.waiting_display_name)
    await message.answer(
        "Как отображать тебя клиентам? Напиши имя одним сообщением.",
    )


async def begin_master_onboarding(message: Message, state: FSMContext, *, master_key: str) -> None:
    await state.clear()
    await state.update_data(**{ONB_MASTER_KEY: master_key})
    await message.answer(
        "Добро пожаловать! Настроим твой профиль мастера — шаги: филиал, имя, рабочие часы.",
        reply_markup=ReplyKeyboardRemove(),
    )

    branches = await branches_repo.list_active()
    if not branches:
        await message.answer(
            "В системе нет активных филиалов. Обратись к администратору.\n"
            "Когда филиал появится, напиши /start или попроси новую ссылку.",
            reply_markup=master_menu_keyboard(),
        )
        await state.clear()
        return

    if len(branches) == 1:
        b = branches[0]
        ok = await masters_repo.set_branch_binding(master_key, b.id, True)
        if not ok:
            await message.answer(
                "Не удалось привязать к филиалу (ошибка базы). Напиши администратору.",
                reply_markup=master_menu_keyboard(),
            )
            await state.clear()
            return
        await message.answer(f"Ты закреплён за филиалом «{b.name}».")
        await _ask_display_name(message, state)
        return

    await state.set_state(MasterOnboardingStates.waiting_branch)
    kb_rows = [
        [InlineKeyboardButton(text=f"📍 {br.name}", callback_data=f"mon_onb:br:{br.id}")]
        for br in branches
    ]
    await message.answer(
        "Выбери филиал, в котором ты работаешь:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_rows),
    )


@router.callback_query(MasterOnboardingStates.waiting_branch, F.data.startswith("mon_onb:br:"))
async def onb_pick_branch(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    master_key = str(data.get(ONB_MASTER_KEY) or "")
    if not master_key:
        await safe_callback_answer(callback, "Сессия устарела. Нажми /start.", show_alert=True)
        return
    m = await masters_repo.get_by_key(master_key)
    if m is None or m.telegram_user_id != callback.from_user.id:
        await safe_callback_answer(callback, "Нет доступа.", show_alert=True)
        return
    try:
        bid = int(callback.data.rsplit(":", 1)[-1])
    except ValueError:
        await safe_callback_answer(callback, "Некорректные данные.", show_alert=True)
        return
    ok = await masters_repo.set_branch_binding(master_key, bid, True)
    if not ok:
        await safe_callback_answer(callback, "Не удалось сохранить. Напиши администратору.", show_alert=True)
        return
    await safe_callback_answer(callback)
    active = await branches_repo.list_active()
    br = next((x for x in active if x.id == bid), None)
    label = br.name if br else str(bid)
    await callback.message.answer(f"Филиал «{label}» выбран ✅")
    await _ask_display_name(callback.message, state)


@router.message(MasterOnboardingStates.waiting_display_name, F.text)
async def onb_display_name(message: Message, state: FSMContext) -> None:
    name = (message.text or "").strip()
    if not name:
        await message.answer("Напиши имя текстом.")
        return
    if len(name) > 200:
        await message.answer("Слишком длинное имя (макс. 200 символов). Сократи.")
        return
    data = await state.get_data()
    master_key = str(data.get(ONB_MASTER_KEY) or "")
    m = await masters_repo.get_by_key(master_key)
    if m is None or m.telegram_user_id != message.from_user.id:
        await message.answer("Сессия устарела. Нажми /start.")
        await state.clear()
        return
    if not await masters_repo.update_display_name(master_key, name):
        await message.answer("Не удалось сохранить имя. Попробуй ещё раз или напиши администратору.")
        return

    await state.set_state(MasterOnboardingStates.waiting_schedule)
    sched = await schedule_service.get_effective_schedule()
    intro = (
        f"График барбершопа для слотов: {sched.start_time.strftime('%H:%M')}–{sched.end_time.strftime('%H:%M')}.\n"
        "Выбери свои часы приёма (внутри этого окна):"
    )
    await message.answer(intro, reply_markup=_schedule_preset_keyboard(sched.start_time, sched.end_time))


@router.callback_query(MasterOnboardingStates.waiting_schedule, F.data.startswith("mon_onb:sc:"))
async def onb_pick_schedule(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    master_key = str(data.get(ONB_MASTER_KEY) or "")
    m = await masters_repo.get_by_key(master_key)
    if m is None or m.telegram_user_id != callback.from_user.id:
        await safe_callback_answer(callback, "Нет доступа.", show_alert=True)
        await state.clear()
        return
    parts = callback.data.split(":")
    if len(parts) != 4:
        await safe_callback_answer(callback, "Некорректная команда.", show_alert=True)
        return
    st = _parse_compact(parts[2])
    en = _parse_compact(parts[3])
    if st is None or en is None or st >= en:
        await safe_callback_answer(callback, "Некорректное время.", show_alert=True)
        return
    sched = await schedule_service.get_effective_schedule()
    if st < sched.start_time or en > sched.end_time:
        await safe_callback_answer(
            callback,
            f"Выбери интервал не шире {sched.start_time.strftime('%H:%M')}–{sched.end_time.strftime('%H:%M')}.",
            show_alert=True,
        )
        return
    if not await masters_repo.set_work_hours(master_key, st, en):
        await safe_callback_answer(callback, "Не удалось сохранить график.", show_alert=True)
        return
    await state.clear()
    await safe_callback_answer(callback, "Сохранено!")
    await callback.message.answer(
        "Настройка завершена ✅ Добро пожаловать в кабинет мастера!",
        reply_markup=master_menu_keyboard(),
    )
