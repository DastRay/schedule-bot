import asyncio
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from app.database.models import Professor
from app.keyboards.schedule_kb import get_other_schedules_kb
from app.state.states import ProfessorScheduleStates
from app.utils.custom_logging.TelegramLogHandler import send_chat_info_log
from app.utils.messages.safe_delete_messages import safe_delete_callback_message, safe_delete_message
from app.utils.schedule.schedule_formatter import format_schedule_professor, escape_md_v2
from app.utils.schedule.search_professors import search_professors_fuzzy
from app.utils.schedule.sync_lock import is_sync_running
from app.utils.schedule.worker import get_lesson_for_professor
from app.keyboards.schedule_kb import get_schedule_professors_kb
import app.utils.week_mark.week_mark as week_mark


router = Router()
logger = logging.getLogger(__name__)


async def get_professor_schedule_for_today(professor_name: str):
    """
    Получает расписание преподавателя на сегодня.

    Параметры:
        professor_name (str): Имя преподавателя

    Возвращает:
        Tuple[Professor, List, List, str]:
            - Professor объект или None
            - Все занятия преподавателя
            - Отфильтрованные занятия на сегодня
            - Текущий тип недели
    """

    current_weekday = datetime.now().isoweekday()
    professor, all_lessons = await get_lesson_for_professor(professor_name)

    if not professor or not all_lessons:
        return professor, all_lessons, [], ""

    today_lessons = [lesson for lesson in all_lessons if lesson.weekday == current_weekday]

    current_week = week_mark.WEEK_MARK_TXT
    week_filter = "plus" if current_week == "plus" else "minus"

    filtered_lessons = [
        lesson for lesson in today_lessons
        if lesson.week_mark in (week_filter, "every", "none", None)
    ]

    return professor, all_lessons, filtered_lessons, week_filter


async def format_and_send_schedule(target, professor_name: str, professor, filtered_lessons, week_filter, reply_markup):
    """
    Форматирует и отправляет пользователю расписание преподавателя на текущий день.

    Функция принимает уже отфильтрованные занятия преподавателя, преобразует их
    в список текстовых сообщений (через `format_schedule_professor`) и отправляет
    пользователю. Если расписание не помещается в одно сообщение, оно разбивается
    на несколько с предупреждением в логах.

    Параметры:
        target (Message | CallbackQuery.message): Объект для отправки сообщений.
        professor_name (str): Имя преподавателя (для логирования).
        professor (Professor): Объект преподавателя из базы данных.
        filtered_lessons (list): Отфильтрованный список занятий на сегодня.
        week_filter (str): Маркер текущей недели (`plus`, `minus` или `every`).
        reply_markup (InlineKeyboardMarkup): Клавиатура, прикрепляемая к последнему сообщению.

    Исключения:
        Exception: Если возникла ошибка при отправке сообщений пользователю.
    """

    header_prefix = f"👨‍🏫 Расписание преподавателя {professor.name} на сегодня"
    messages = format_schedule_professor(filtered_lessons, week=week_filter, header_prefix=header_prefix)

    if not messages:
        await target.answer("❌ Не удалось сформировать расписание.")
        return

    len_messages = len(messages)
    for i, msg_text in enumerate(messages):
        is_last = (i == len_messages - 1)
        await target.answer(
            msg_text,
            reply_markup=reply_markup if is_last else None,
            parse_mode="MarkdownV2",
            disable_web_page_preview=True
        )
        await asyncio.sleep(1.1)


async def send_no_lessons_message(target, professor_name: str, professor=None, reply_markup=None):
    """
    Отправляет сообщение, если у преподавателя нет пар на текущий день.

    Формирует шаблон сообщения с текущим днём недели и маркером недели
    (например, верхняя/нижняя неделя) и сообщает пользователю, что
    занятий сегодня нет.

    Параметры:
        target (Message | CallbackQuery.message): Объект для отправки сообщения.
        professor_name (str): Имя преподавателя (используется при отсутствии объекта professor).
        professor (Optional[Professor]): Объект преподавателя, если доступен.
        reply_markup (Optional[InlineKeyboardMarkup]): Клавиатура, прикрепляемая к сообщению.

    """

    weekday_names = {
        1: "Понедельник", 2: "Вторник", 3: "Среда",
        4: "Четверг", 5: "Пятница", 6: "Суббота", 7: "Воскресенье"
    }

    current_weekday = datetime.now().isoweekday()
    day_name = weekday_names.get(current_weekday, "сегодня")

    name_to_display = professor.name if professor else professor_name

    text = (
        f"👨‍🏫 *Расписание преподавателя {escape_md_v2(name_to_display)}*\n\n"
        f"📅 *{day_name}* {week_mark.WEEK_MARK_STICKER}\n\n"
        f"Сегодня пар нет\\."
    )

    await target.answer(
        text=text,
        reply_markup=reply_markup,
        parse_mode="MarkdownV2"
    )


async def show_professor_schedule_menu(message: Message, professor_name: str, state: FSMContext):
    """
    Показывает пользователю меню выбора типа расписания преподавателя и автоматически отображает расписание на сегодня.

    При первом вызове сохраняет имя преподавателя в состояние FSM,
    создает inline-клавиатуру и пытается сразу показать расписание на текущий день.
    Если занятий нет, сообщает пользователю об этом.

    Параметры:
        message (Message): Объект входящего сообщения от пользователя.
        professor_name (str): Имя преподавателя, для которого запрашивается расписание.
        state (FSMContext): Контекст состояний FSM для сохранения данных.

    Исключения:
        Exception: Если возникла ошибка при получении или отправке расписания.
    """

    if is_sync_running():
        await message.answer(
            "⏳ Просмотр расписания временно недоступен — идёт обновление данных. Пожалуйста, попробуйте позже."
        )
        await state.clear()
        return
    
    await state.update_data(professor_name=professor_name)
    schedule_type_kb = get_schedule_professors_kb(professor_name)

    try:
        professor, all_lessons, filtered_lessons, week_filter = await get_professor_schedule_for_today(professor_name)

        if professor and filtered_lessons:
            await format_and_send_schedule(
                target=message,
                professor_name=professor_name,
                professor=professor,
                filtered_lessons=filtered_lessons,
                week_filter=week_filter,
                reply_markup=schedule_type_kb
            )
            return

        await send_no_lessons_message(message, professor_name, professor, schedule_type_kb)

    except Exception as e:
        logger.error(f"Ошибка при получении расписания на сегодня для {professor_name}: {e}")
        await message.answer(
            text=f"👨‍🏫 *Преподаватель: {escape_md_v2(professor_name)}*\n\nВыберите тип расписания:",
            reply_markup=schedule_type_kb,
            parse_mode="MarkdownV2"
        )


async def show_professor_selection_keyboard(message: Message, professors: list[Professor], query: str):
    """
    Показывает клавиатуру с найденными преподавателями для выбора.

    Параметры:
        message (Message): Сообщение для ответа
        professors (list[Professor]): Список найденных преподавателей
        query (str): Исходный поисковый запрос
    """
    keyboard = []

    for professor in professors:
        keyboard.append([
            InlineKeyboardButton(
                text=f"👨‍🏫 {professor.name}",
                callback_data=f"select_prof:{professor.name}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(text="◀️ Назад", callback_data="cancel")
    ])

    selection_kb = InlineKeyboardMarkup(inline_keyboard=keyboard)

    await message.answer(
        text=f"🔍 По запросу `{escape_md_v2(query)}` найдено несколько преподавателей\\.\n\n",
        reply_markup=selection_kb,
        parse_mode="MarkdownV2"
    )


@router.callback_query(F.data == "cancel")
async def cancel(callback: CallbackQuery, state: FSMContext):
    """
    Обработчик нажатия кнопки "◀️ Назад" в меню выбора расписаний.

    При вызове возвращает пользователя к начальному меню, где можно выбрать
    тип расписания для просмотра.

    Параметры:
        callback (CallbackQuery): Объект обратного вызова от Telegram, содержащий данные нажатой кнопки.
        state (FSMContext): Контекст машины состояний пользователя для управления состоянием диалога.

    Логика:
        - Изменяет текст текущего сообщения на меню выбора расписаний.
        - Устанавливает соответствующую клавиатуру.
        - Сбрасывает текущее состояние FSM.
        - Отправляет callback.answer() для подтверждения действия.
    """

    await callback.message.edit_text(
        text="Выберите расписание которое хотите посмотреть:",
        reply_markup=get_other_schedules_kb()
    )
    await callback.answer()
    await state.clear()


@router.callback_query(F.data == "professor_schedule")
async def professor_schedule(callback: CallbackQuery, state: FSMContext):
    """
    Обработчик нажатия кнопки выбора расписания преподавателя.

    Отправляет пользователю запрос на ввод фамилии и инициалов преподавателя.
    После этого переводит FSM в состояние ожидания ввода имени.

    Параметры:
        callback (CallbackQuery): Объект callback-запроса.
        state (FSMContext): Контекст машины состояний для хранения текущего шага пользователя.

    Логика:
        - Отправляет сообщение с инструкцией по вводу имени преподавателя.
        - Устанавливает клавиатуру с кнопкой "Назад".
        - Переводит FSM в состояние `ProfessorScheduleStates.waiting_name`.
        - Сохраняет ID текущего сообщения для последующего удаления.
    """

    if is_sync_running():
        await callback.message.edit_text(
            "⏳ Просмотр расписания временно недоступен — идёт обновление данных. Пожалуйста, попробуйте позже."
        )
        await callback.answer()
        await state.clear()
        return

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="cancel")]
        ])

    await callback.message.edit_text(
        text="👨‍🏫 Введите фамилию и инициалы преподавателя:\n\n"
             "Например: `Иванов И И`",
        reply_markup=cancel_kb,
        parse_mode="MarkdownV2"
    )

    await callback.answer()
    await state.set_state(ProfessorScheduleStates.waiting_name)
    await state.update_data(message_id_to_delete=callback.message.message_id)


@router.message(StateFilter(ProfessorScheduleStates.waiting_name))
async def waiting_name(message: Message, state: FSMContext):
    """
    Обработчик текстового ввода имени преподавателя пользователем.

    Использует RapidFuzz для нечеткого поиска преподавателей:
    - При точном совпадении сразу показывает расписание
    - При нескольких совпадениях показывает клавиатуру для выбора
    - При отсутствии совпадений показывает ошибку

    Параметры:
        message (Message): Сообщение от пользователя.
        state (FSMContext): Контекст FSM, содержащий данные, сохранённые ранее.
    """

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="cancel")]
        ])

    await safe_delete_message(message)

    data = await state.get_data()
    message_id_to_delete = data.get("message_id_to_delete")
    if message_id_to_delete:
        try:
            await message.bot.delete_message(chat_id=message.chat.id, message_id=message_id_to_delete)
        except Exception as e:
            logger.debug(f"⚠️ Не удалось удалить сообщение c именем преподавателя: {e}")

    if is_sync_running():
        await message.answer(
            "⏳ Просмотр расписания временно недоступен — идёт обновление данных. Пожалуйста, попробуйте позже."
        )
        await state.clear()
        return

    name = message.text.strip()

    exact_professor, similar_professors = await search_professors_fuzzy(query=name, limit=5, score_cutoff=85.0)

    if exact_professor:
        await show_professor_schedule_menu(message, exact_professor.name, state)
        return

    if not similar_professors:
        msg = await message.answer(
            text=f"❌ Преподаватель `{escape_md_v2(name)}` не найден\\.\n\n"
                 "Проверьте написание и попробуйте снова\\.",
            reply_markup=cancel_kb,
            parse_mode="MarkdownV2"
        )

        await state.update_data(message_id_to_delete=msg.message_id)
        await state.set_state(ProfessorScheduleStates.waiting_name)
        return

    if len(similar_professors) == 1:
        best_match = similar_professors[0]
        await show_professor_schedule_menu(message, best_match.name, state)
        await state.clear()
        return

    await show_professor_selection_keyboard(message, similar_professors, name)
    await state.clear()


@router.callback_query(F.data.startswith("select_prof:"))
async def handle_professor_selection(callback: CallbackQuery, state: FSMContext):
    """
    Обработчик выбора преподавателя из списка.

    Параметры:
        callback (CallbackQuery): Callback с выбранным преподавателем
        state (FSMContext): Контекст состояния
    """

    professor_name = callback.data.split(":")[1]

    await safe_delete_callback_message(callback)
    await show_professor_schedule_menu(callback.message, professor_name, state)


@router.callback_query(F.data.startswith("prof_today:"))
async def handle_professor_today(callback: CallbackQuery):
    """
    Обрабатывает запрос пользователя на показ расписания преподавателя на текущий день.

    Извлекает имя преподавателя из callback data, получает данные о расписании
    через функцию `get_professor_schedule_for_today`, фильтрует занятия по текущему дню,
    форматирует и отправляет их пользователю. Если занятий нет, отображает
    соответствующее уведомление.

    Параметры:
        callback (CallbackQuery): Объект callback-запроса от пользователя.

    Исключения:
        Exception: Если произошла ошибка при загрузке или форматировании расписания.
    """
    if is_sync_running():
        await callback.message.edit_text(
            "⏳ Просмотр расписания временно недоступен — идёт обновление данных. Пожалуйста, попробуйте позже."
        )
        await callback.answer()
        return
    
    professor_name = ""
    try:
        professor_name = callback.data.split(":")[1]
        professor, all_lessons, filtered_lessons, week_filter = await get_professor_schedule_for_today(professor_name)

        if not professor:
            await callback.message.edit_text(f"❌ Преподаватель {professor_name} не найден.")
            await callback.answer()
            return

        if not all_lessons:
            await callback.message.edit_text(f"❌ Нет расписания для преподавателя {professor_name}.")
            await callback.answer()
            return

        try:
            await callback.message.delete()
        except TelegramBadRequest as e:
            if "message can't be deleted" in str(e):
                logger.debug(f"⚠️ Не удалось удалить сообщение {callback.message.message_id}: слишком старое (старше 48 часов)")
            else:
                logger.debug(f"⚠️ Не удалось удалить сообщение {callback.message.message_id}: {e}")
        except Exception as delete_error:
            logger.debug(f"⚠️ Не удалось удалить сообщение {callback.message.message_id}: {delete_error}")

        schedule_type_kb = get_schedule_professors_kb(professor_name)

        if not filtered_lessons:
            await send_no_lessons_message(callback.message, professor_name, professor, schedule_type_kb)
            await callback.answer(f"Сегодня нет пар у {professor.name}")
            return

        await callback.answer(f"📅 Сегодня {week_mark.WEEK_MARK_STICKER}")
        await format_and_send_schedule(
            target=callback.message,
            professor_name=professor_name,
            professor=professor,
            filtered_lessons=filtered_lessons,
            week_filter=week_filter,
            reply_markup=schedule_type_kb
        )

    except Exception as e:
        logger.error(f"Ошибка при показе расписания на сегодня преподавателя {professor_name}: {e}")
        await callback.message.edit_text(f"❌ Ошибка при загрузке расписания преподавателя {professor_name}")
        await callback.answer()


@router.callback_query(F.data.startswith("prof_week_"))
async def handle_professor_week(callback: CallbackQuery):
    """
    Обработчик показа расписания преподавателя на неделю.

    В зависимости от типа (➕ неделя, ➖ неделя или вся неделя)
    формирует расписание преподавателя и отправляет пользователю.

    Параметры:
        callback (CallbackQuery): Callback-запрос с данными в формате "prof_week_[тип]:Фамилия И.О.".
            Где тип может быть "plus", "minus" или "full".

    Логика:
        1. Извлекает тип недели и имя преподавателя из callback data.
        2. Получает занятия через `get_lesson_for_professor`.
        3. Форматирует расписание в зависимости от выбранного типа недели.
        4. Отправляет одно или несколько сообщений с результатом.

    Исключения:
        Exception: При ошибке загрузки или отображения расписания.
    """

    if is_sync_running():
        await callback.message.edit_text(
            "⏳ Просмотр расписания временно недоступен — идёт обновление данных. Пожалуйста, попробуйте позже."
        )
        await callback.answer()
        return

    professor_name = ""
    try:
        data_parts = callback.data.split(":")
        week_type = data_parts[0].replace("prof_week_", "")
        professor_name = data_parts[1]

        professor, lessons = await get_lesson_for_professor(professor_name)

        if not professor:
            await callback.message.edit_text(f"❌ Преподаватель {professor} не найден.")
            await callback.answer()
            return

        if not lessons:
            await callback.message.edit_text(f"❌ Нет расписания для преподавателя {professor_name}")
            await callback.answer()
            return

        week_names = {
            "plus": "➕ Неделя",
            "minus": "➖ Неделя",
            "full": "🗓 Вся неделя"
        }

        if week_type == "full":
            header_prefix = f"👨‍🏫 Расписание преподавателя {professor.name}"
        else:
            header_prefix = f"👨‍🏫 Расписание преподавателя {professor.name} на неделю"

        messages = format_schedule_professor(
            lessons,
            week=week_type,
            header_prefix=header_prefix
        )

        try:
            await callback.message.delete()
        except TelegramBadRequest as e:
            if "message can't be deleted for everyone" in str(e):
                logger.debug(f"⚠️ Не удалось удалить сообщение {callback.message.message_id}: слишком старое (старше 48 часов)")
            else:
                logger.debug(f"⚠️ Не удалось удалить сообщение {callback.message.message_id}: {e}")
        except Exception as e:
            logger.debug(f"⚠️ Не удалось удалить сообщение {callback.message.message_id}: {e}")

        if messages:
            len_messages = len(messages)
            await callback.answer(week_names.get(week_type, "🗓 Неделя"))
            for i, msg_text in enumerate(messages):
                is_last = (i == len_messages - 1)
                await callback.message.answer(
                    msg_text,
                    reply_markup=callback.message.reply_markup if is_last else None,
                    parse_mode="MarkdownV2",
                    disable_web_page_preview=True
                )
                await asyncio.sleep(1.1)
        else:
            await callback.message.answer("❌ Не удалось сформировать расписание.")
            await callback.answer()

    except Exception as e:
        logger.error(f"Ошибка при показе расписания преподавателя {professor_name}: {e}.")
        await callback.message.edit_text(f"❌ Ошибка при загрузке расписания преподавателя {professor_name}")
        await callback.answer()