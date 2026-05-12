"""
Онбординг мастера после принятия приглашения: филиал → имя для клиентов → начало/конец смены и обед (кнопками).
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
ONB_WORK_START = "onb_work_start"
ONB_WORK_END = "onb_work_end"


def _parse_compact(s: str) -> time | None:
    if len(s) != 4 or not s.isdigit():
        return None
    try:
        return datetime.strptime(s, "%H%M").time()
    except ValueError:
        return None


def _start_time_options(shop_start: time, shop_end: time, step_minutes: int = 30) -> list[str]:
    base = datetime.combine(date.today(), shop_start)
    end_limit = datetime.combine(date.today(), shop_end) - timedelta(minutes=step_minutes)
    out: list[str] = []
    cur = base
    while cur <= end_limit:
        out.append(cur.strftime("%H:%M"))
        cur += timedelta(minutes=step_minutes)
    return out


def _end_time_options(work_start: time, shop_end: time, step_minutes: int = 30) -> list[str]:
    start_base = datetime.combine(date.today(), work_start)
    end_base = datetime.combine(date.today(), shop_end)
    out: list[str] = []
    cur = start_base + timedelta(minutes=step_minutes)
    while cur <= end_base:
        out.append(cur.strftime("%H:%M"))
        cur += timedelta(minutes=step_minutes)
    return out


def _lunch_start_options(work_start: time, work_end: time, lunch_duration_minutes: int) -> list[str]:
    start_base = datetime.combine(date.today(), work_start)
    end_limit = datetime.combine(date.today(), work_end) - timedelta(minutes=lunch_duration_minutes)
    out: list[str] = []
    cur = start_base
    while cur <= end_limit:
        out.append(cur.strftime("%H:%M"))
        cur += timedelta(minutes=30)
    return out


def _time_grid_keyboard(times: list[str], *, kind: str) -> InlineKeyboardMarkup:
    """kind: ws | we | lt — префикс в callback_data."""
    step = 3
    rows: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(times), step):
        chunk = times[i : i + step]
        rows.append(
            [
                InlineKeyboardButton(
                    text=t,
                    callback_data=f"mon_onb:{kind}:{datetime.strptime(t, '%H:%M').strftime('%H%M')}",
                )
                for t in chunk
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _lunch_keyboard(times: list[str]) -> InlineKeyboardMarkup:
    kb = _time_grid_keyboard(times, kind="lt")
    rows = list(kb.inline_keyboard)
    rows.append([InlineKeyboardButton(text="Без обеда", callback_data="mon_onb:ln")])
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

    await state.set_state(MasterOnboardingStates.waiting_schedule_start)
    sched = await schedule_service.get_effective_schedule()
    opts = _start_time_options(sched.start_time, sched.end_time)
    if not opts:
        await message.answer(
            "Окно барбершопа слишком короткое для приёма. Напиши администратору.",
            reply_markup=master_menu_keyboard(),
        )
        await state.clear()
        return
    intro = (
        f"График барбершопа для слотов: {sched.start_time.strftime('%H:%M')}–{sched.end_time.strftime('%H:%M')}.\n\n"
        "Выбери время начала работы:"
    )
    await message.answer(intro, reply_markup=_time_grid_keyboard(opts, kind="ws"))


async def _load_onb_master(callback: CallbackQuery, state: FSMContext) -> tuple[str, object] | None:
    data = await state.get_data()
    master_key = str(data.get(ONB_MASTER_KEY) or "")
    if not master_key:
        await safe_callback_answer(callback, "Сессия устарела. Нажми /start.", show_alert=True)
        return None
    m = await masters_repo.get_by_key(master_key)
    if m is None or m.telegram_user_id != callback.from_user.id:
        await safe_callback_answer(callback, "Нет доступа.", show_alert=True)
        await state.clear()
        return None
    return master_key, m


@router.callback_query(MasterOnboardingStates.waiting_schedule_start, F.data.startswith("mon_onb:ws:"))
async def onb_pick_work_start(callback: CallbackQuery, state: FSMContext) -> None:
    ctx = await _load_onb_master(callback, state)
    if not ctx:
        return
    master_key, _m = ctx
    parts = callback.data.split(":")
    if len(parts) != 3:
        await safe_callback_answer(callback, "Некорректная команда.", show_alert=True)
        return
    st = _parse_compact(parts[2])
    if st is None:
        await safe_callback_answer(callback, "Некорректное время.", show_alert=True)
        return
    sched = await schedule_service.get_effective_schedule()
    if st < sched.start_time or st > sched.end_time:
        await safe_callback_answer(callback, "Время вне графика барбершопа.", show_alert=True)
        return
    last_allowed = (
        datetime.combine(date.today(), sched.end_time) - timedelta(minutes=30)
    ).time()
    if st > last_allowed:
        await safe_callback_answer(callback, "Слишком позднее начало для этого окна.", show_alert=True)
        return

    await state.update_data(**{ONB_WORK_START: st.strftime("%H:%M")})
    await state.set_state(MasterOnboardingStates.waiting_schedule_end)
    ends = _end_time_options(st, sched.end_time)
    if not ends:
        await safe_callback_answer(callback, "Нет доступного окончания смены.", show_alert=True)
        return
    await safe_callback_answer(callback)
    await callback.message.answer(
        f"Начало: {st.strftime('%H:%M')} ✓\nВыбери время окончания работы:",
        reply_markup=_time_grid_keyboard(ends, kind="we"),
    )


@router.callback_query(MasterOnboardingStates.waiting_schedule_end, F.data.startswith("mon_onb:we:"))
async def onb_pick_work_end(callback: CallbackQuery, state: FSMContext) -> None:
    ctx = await _load_onb_master(callback, state)
    if not ctx:
        return
    data = await state.get_data()
    start_s = data.get(ONB_WORK_START)
    if not start_s:
        await safe_callback_answer(callback, "Сначала выбери начало работы.", show_alert=True)
        return
    start_t = datetime.strptime(str(start_s), "%H:%M").time()

    parts = callback.data.split(":")
    if len(parts) != 3:
        await safe_callback_answer(callback, "Некорректная команда.", show_alert=True)
        return
    end_t = _parse_compact(parts[2])
    if end_t is None or end_t <= start_t:
        await safe_callback_answer(callback, "Некорректное окончание.", show_alert=True)
        return
    sched = await schedule_service.get_effective_schedule()
    if end_t > sched.end_time or start_t < sched.start_time:
        await safe_callback_answer(callback, "Интервал должен быть внутри графика барбершопа.", show_alert=True)
        return

    await state.update_data(**{ONB_WORK_END: end_t.strftime("%H:%M")})
    await state.set_state(MasterOnboardingStates.waiting_schedule_lunch)
    lunch_opts = _lunch_start_options(start_t, end_t, schedule_service.LUNCH_DURATION_MINUTES)
    await safe_callback_answer(callback)
    await callback.message.answer(
        f"Смена: {start_t.strftime('%H:%M')}–{end_t.strftime('%H:%M')} ✓\n"
        f"Выбери начало обеда (перерыв {schedule_service.LUNCH_DURATION_MINUTES} мин) или «Без обеда».",
        reply_markup=_lunch_keyboard(lunch_opts),
    )


async def _finish_schedule(
    callback: CallbackQuery,
    state: FSMContext,
    master_key: str,
    start_t: time,
    end_t: time,
    lunch_time: time | None,
) -> None:
    if not await masters_repo.set_work_schedule(master_key, start_t, end_t, lunch_time):
        await safe_callback_answer(callback, "Не удалось сохранить график.", show_alert=True)
        return
    await state.clear()
    if lunch_time is None:
        lunch_line = "Обед: без перерыва."
    else:
        le = (
            datetime.combine(date.today(), lunch_time)
            + timedelta(minutes=schedule_service.LUNCH_DURATION_MINUTES)
        ).time()
        lunch_line = f"Обед: {lunch_time.strftime('%H:%M')}–{le.strftime('%H:%M')}."
    await safe_callback_answer(callback, "Сохранено!")
    await callback.message.answer(
        f"Настройка завершена ✅ Добро пожаловать в кабинет мастера!\n{lunch_line}",
        reply_markup=master_menu_keyboard(),
    )


@router.callback_query(MasterOnboardingStates.waiting_schedule_lunch, F.data == "mon_onb:ln")
async def onb_pick_lunch_none(callback: CallbackQuery, state: FSMContext) -> None:
    ctx = await _load_onb_master(callback, state)
    if not ctx:
        return
    master_key, _ = ctx
    data = await state.get_data()
    start_s, end_s = data.get(ONB_WORK_START), data.get(ONB_WORK_END)
    if not start_s or not end_s:
        await safe_callback_answer(callback, "Недостаточно данных. Начни с шага «имя».", show_alert=True)
        return
    start_t = datetime.strptime(str(start_s), "%H:%M").time()
    end_t = datetime.strptime(str(end_s), "%H:%M").time()
    await _finish_schedule(callback, state, master_key, start_t, end_t, None)


@router.callback_query(MasterOnboardingStates.waiting_schedule_lunch, F.data.startswith("mon_onb:lt:"))
async def onb_pick_lunch_start(callback: CallbackQuery, state: FSMContext) -> None:
    ctx = await _load_onb_master(callback, state)
    if not ctx:
        return
    master_key, _ = ctx
    data = await state.get_data()
    start_s, end_s = data.get(ONB_WORK_START), data.get(ONB_WORK_END)
    if not start_s or not end_s:
        await safe_callback_answer(callback, "Недостаточно данных.", show_alert=True)
        return
    work_start = datetime.strptime(str(start_s), "%H:%M").time()
    work_end = datetime.strptime(str(end_s), "%H:%M").time()

    parts = callback.data.split(":")
    if len(parts) != 3:
        await safe_callback_answer(callback, "Некорректная команда.", show_alert=True)
        return
    lunch_t = _parse_compact(parts[2])
    if lunch_t is None:
        await safe_callback_answer(callback, "Некорректное время.", show_alert=True)
        return
    lunch_end = (
        datetime.combine(date.today(), lunch_t) + timedelta(minutes=schedule_service.LUNCH_DURATION_MINUTES)
    ).time()
    if lunch_t < work_start or lunch_end > work_end:
        await safe_callback_answer(callback, "Обед должен укладываться в твою смену.", show_alert=True)
        return

    await _finish_schedule(callback, state, master_key, work_start, work_end, lunch_t)
