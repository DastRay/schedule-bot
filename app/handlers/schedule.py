"""
Реализует обработчики для просмотра расписания студентами.

Ошибки при формировании расписания фиксируются через logging.
"""

import logging
import datetime

from sqlalchemy import select
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery

from app.utils import week_mark
from app.utils.schedule.worker import get_schedule_for_group
from app.keyboards.faculty_kb import abbr_faculty
from app.keyboards.find_kb import faculty_keyboard_find, groups_keyboards_find
from app.keyboards.schedule_kb import get_choice_week_kb
from app.state.states import ShowSheduleStates
from app.utils.schedule.schedule_formatter import escape_md_v2, format_schedule
from app.keyboards.schedule_kb import get_other_schedules_kb
from app.database.db import AsyncSessionLocal
from app.database.models import User, Lesson


router = Router()
logger = logging.getLogger(__name__)

@router.callback_query(F.data.startswith("cancel_"), F.data.endswith("_find"))
async def cancel_find(callback: CallbackQuery, state: FSMContext):
    """
    Обработка отмены поиска.

    Действия:
    - Очищает состояние.
    - Сообщает пользователю об отмене.
    """

    await state.clear()
    await callback.message.edit_text(f"❌ Поиск отменён.")

@router.callback_query(F.data=="exit_other_schedules")
async def exit_other_schedules(callback: CallbackQuery):
    """Отмена выбора 'другого' расписания"""
    await callback.message.delete()


@router.message(F.text=="Другие расписания")
async def other_schedules(message: Message):
    """Просмотр 'другого' расписания"""
    await message.answer(text="Выберите расписание которое хотите посмотреть:", reply_markup=get_other_schedules_kb())

@router.callback_query(F.data=="other_schedule")
async def get_schedule_start(callback: CallbackQuery, state: FSMContext):
    """
    Начало сценария просмотра расписания.

    Действия:
    - Отправляет клавиатуру факультетов.
    - Устанавливает состояние choice_faculty.
    """

    await callback.message.edit_text("Выберите факультет:", reply_markup=faculty_keyboard_find)
    await state.set_state(ShowSheduleStates.choice_faculty)
    await callback.answer()


@router.message(F.text == "Расписание на сегодня")
async def get_schedule_today(message: Message):
    """
    Отображает расписание на сегодняшний день для пользователя,
    исходя из его faculty_id и group_id в таблице user.
    """

    user_id = message.from_user.id
    today = datetime.date.today()
    weekday = today.isoweekday()  # Понедельник=1, ..., Воскресенье=7
    current_week_mark =  week_mark.WEEK_MARK_TXT

    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(User).where(User.id == user_id))
            user = result.scalar_one_or_none()
            if not user:
                await message.answer("❌ Вы ещё не зарегистрированы.")
                return

            group = user.group
            if not group:
                await message.answer("⚠️ Ваша группа не найдена.")
                return

            result = await session.execute(
                select(Lesson)
                .where(Lesson.group_id == group.id)
                .where(Lesson.weekday == weekday)
            )
            lessons = result.scalars().all()

            lessons_today = [
                l for l in lessons
                if l.week_mark in ("every", current_week_mark)
            ]

            if not lessons_today:
                await message.answer("Сегодня пар нет 🎉")
                return

            text_blocks = format_schedule(
                lessons=lessons_today,
                week=current_week_mark,
                header_prefix=f"📅 Расписание на сегодня ({today.strftime('%d.%m.%Y')})"
            )

            for text in text_blocks:
                await message.answer(text, parse_mode="MarkdownV2", disable_web_page_preview=True)


    except Exception as e:
        logger.error(f"⚠️ Ошибка при выводе расписания на сегодня: {e}")
        await message.answer("⚠️ Ошибка при получении расписания.")

@router.callback_query(F.data=="professor_schedule")
async def professor_schedule(callback: CallbackQuery):
    await callback.message.edit_text("Этот функционал ещё не доступен 😢")
    await callback.answer()

@router.callback_query(F.data == "weekly_schedule")
async def weekly_schedule(callback: CallbackQuery):
    """
    Обработчик кнопки "Расписание на текущую неделю".

    1. Извлекает факультет и группу пользователя из БД.
    2. Определяет текущий маркер недели (plus / minus).
    3. Получает из таблицы Lesson все занятия для группы.
    4. Форматирует и отправляет расписание на текущую неделю.
    """
    user_id = callback.from_user.id
    try:
        async with AsyncSessionLocal() as session:

            result = await session.execute(
                select(User).where(User.id == user_id)
            )
            user = result.scalar_one_or_none()
            if not user:
                await callback.message.answer("❌ Вы ещё не зарегистрированы.")
                return

            current_week = week_mark.WEEK_MARK_TXT
            lessons_query = await session.execute(
                select(Lesson)
                .where(Lesson.group_id == user.group_id)
                .order_by(Lesson.weekday, Lesson.lesson_number)
            )
            lessons = lessons_query.scalars().all()

            if not lessons:
                await callback.message.answer("📭 Расписание для вашей группы отсутствует.")
                return

            messages = format_schedule(
                lessons,
                week=current_week,
                header_prefix=f"📅 Расписание группы {user.group.group_name} на текущую неделю"
            )

            await callback.message.edit_text(messages[0], parse_mode="MarkdownV2", disable_web_page_preview=True)
            for msg in messages[1:]:
                await callback.message.answer(msg, parse_mode="MarkdownV2", disable_web_page_preview=True)

            await callback.answer()

    except Exception as e:
        logger.exception(f"⚠️ Ошибка при обработке weekly_schedule: {e}")
        await callback.message.answer("⚠️ Произошла ошибка при получении расписания.")



@router.callback_query(StateFilter(ShowSheduleStates.choice_faculty), F.data.startswith("faculty:"))
async def get_schedule_faculty(callback: CallbackQuery, state: FSMContext):
    """
    Обработка выбора факультета.

    Действия:
    - Определяет название факультета по сокращению.
    - Показывает клавиатуру с группами.
    - Если групп нет → сообщение об ошибке.
    """

    faculty_name = abbr_faculty[callback.data.split(":")[1]]
    groups_kb = groups_keyboards_find.get(faculty_name)
    if not groups_kb:
        await callback.message.edit_text("⚠️ Для этого факультета нет групп.")
        return
    await callback.message.edit_text(f"Выберите группу факультета {faculty_name}:", reply_markup=groups_kb)
    await state.set_state(ShowSheduleStates.choice_group)

@router.callback_query(StateFilter(ShowSheduleStates.choice_group), F.data.startswith("group:"))
async def choice_type_week(callback: CallbackQuery, state: FSMContext):
    """
    Обработка выбора группы.

    Действия:
    - Сохраняет выбранную группу в состояние.
    - Просит выбрать тип расписания (неделя plus/minus/full).
    """

    group_name = callback.data.split(":")[1]
    await state.update_data(group_name=group_name)
    await state.set_state(ShowSheduleStates.choice_week)

    await callback.message.edit_text(f"Выберите тип расписания:\n"
                                     f"Сейчас неделя {week_mark.WEEK_MARK_STICKER}", reply_markup=get_choice_week_kb())

@router.callback_query(StateFilter(ShowSheduleStates.choice_week), F.data.startswith("week:"))
async def show_schedule(callback: CallbackQuery, state: FSMContext):
    """
    Показывает расписание для выбранной группы и типа недели
    (чётная, нечётная, полное) с использованием общей функции форматирования.
    """

    state_data = await state.get_data()
    group_name = state_data.get("group_name")
    week = callback.data.split(":")[1]

    try:
        lessons = await get_schedule_for_group(group_name)
        if not lessons:
            await callback.message.edit_text(f"Расписание для {group_name} пустое.")
            return

        messages = format_schedule(lessons=lessons, week=week, header_prefix=f"📅 Расписание для {group_name}")

        if not messages:
            await callback.message.edit_text(f"На выбранную неделю ({week}) расписание для {group_name} пустое.")
            return

        await callback.message.edit_text(messages[0], parse_mode="MarkdownV2", disable_web_page_preview=True)
        for msg in messages[1:]:
            await callback.message.answer(msg.strip(), parse_mode="MarkdownV2", disable_web_page_preview=True)

    except Exception as e:
        logger.exception(f"⚠️ Ошибка при выводе расписания для {group_name}: {e}")
        await callback.message.edit_text(
            f"⚠️ Произошла ошибка при выводе расписания для *{escape_md_v2(group_name)}*.",
            parse_mode="MarkdownV2"
        )