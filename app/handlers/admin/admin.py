import asyncio
import logging
from aiogram import F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy import update, select, func

from app.database.models import User
from app.filters.is_admin import IsAdminFilter
from app.keyboards.admin_kb import get_admin_kb
from app.state.states import AddAdminStates
from app.database.db import AsyncSessionLocal
from app.utils.admins.admin_list import add_admin_to_list, remove_admin_from_list, get_admin_username
from app.utils.custom_logging.BufferedLogHandler import global_buffer_handler
from app.utils.messages.safe_delete_messages import safe_delete_message, safe_delete_callback_message
import app.utils.admins.admin_list as admin_list

router = Router()
logger = logging.getLogger(__name__)


def escape_md_v2(text: str) -> str:
    """
    Экранирует специальные символы для Telegram MarkdownV2.

    Telegram MarkdownV2 требует экранирования некоторых символов, чтобы они
    не интерпретировались как разметка. Эта функция добавляет обратный слэш
    перед каждым спецсимволом из списка, чтобы текст отображался корректно.

    Параметры:
        text (str): Исходный текст.

    Возвращает:
        str: Экранированный текст, безопасный для использования с MarkdownV2.
    """

    escape_chars = r"_*[]()~`>#+-=|{}.!\\"
    for ch in escape_chars:
        text = text.replace(ch, f"\\{ch}")
    return text


@router.callback_query(F.data=="exit_admin_panel", IsAdminFilter())
async def exit_admin_panel(callback: CallbackQuery, state: FSMContext):
    """
    Закрывает окно административной панели.

    Действия:
        Удаляет сообщение с панелью администратора.

    Параметры:
        callback (CallbackQuery): Объект callback-запроса от Telegram.
        state (FSMContext): Контекст конечного автомата состояний FSM.
    """

    await safe_delete_callback_message(callback)
    await state.clear()


@router.message(F.text == "Админ панель", IsAdminFilter())
async def admin_panel_message(message: Message, state: FSMContext):
    """
    Обрабатывает текстовую команду "Админ панель".

    Действия:
    1. Очищает текущее состояние FSM (если оно было установлено ранее).
    2. Отправляет пользователю сообщение с интерфейсом панели администратора.

    Параметры:
        message (Message): Объект сообщения от пользователя.
        state (FSMContext): Контекст конечного автомата состояний FSM.
    """

    await safe_delete_message(message)

    await message.answer(text="Админ панель:", reply_markup=get_admin_kb())
    await state.clear()


@router.callback_query(F.data=="admin_panel", IsAdminFilter())
async def admin_panel_callback(callback: CallbackQuery, state: FSMContext):
    """
    Обрабатывает переход к административной панели через inline-кнопку.

    Действия:
    1. Редактирует текущее сообщение, заменяя его на меню панели администратора.
    2. Очищает состояние FSM.
    3. Подтверждает получение callback-запроса.

    Параметры:
        callback (CallbackQuery): Объект callback-запроса.
        state (FSMContext): Контекст состояния FSM.
    """

    await callback.message.edit_text(text="Админ панель:", reply_markup=get_admin_kb())
    await callback.answer()
    await state.clear()


@router.callback_query(F.data == "add_admin", IsAdminFilter())
async def add_admin(callback: CallbackQuery, state: FSMContext):
    """
    Инициирует процесс добавления нового администратора.

    Действия:
    1. Отображает сообщение с инструкцией ввести ID пользователя.
    2. Добавляет кнопку "◀️ Назад" для возврата в админ-панель.
    3. Переводит FSM в состояние ожидания ID (`AddAdminStates.waiting_id`).
    4. Сохраняет ID сообщения, чтобы потом его можно было удалить.

    Параметры:
        callback (CallbackQuery): Объект callback-запроса.
        state (FSMContext): Контекст состояния FSM.
    """

    await state.clear()

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
        ])
    await callback.message.edit_text(text="Введите ID пользователя для назначения администратором.\n"
                                     "Для того чтобы узнать id можно воспользоваться @username_to_id_bot",
                                     reply_markup=kb)
    await state.set_state(AddAdminStates.waiting_id)
    await callback.answer()
    await state.update_data(message_id=callback.message.message_id)


@router.message(StateFilter(AddAdminStates.waiting_id), IsAdminFilter())
async def reading_id(message: Message, state: FSMContext):
    """
    Обрабатывает ввод ID пользователя для назначения администратором.

    Действия:
    1. Удаляет предыдущее сообщение, где бот просил ввести ID.
    2. Проверяет, что введено число (ID).
    3. Не разрешает назначить администратором самого себя.
    4. Проверяет наличие пользователя в БД.
    5. Если пользователь найден и не администратор — обновляет его роль на 1.
    6. Добавляет пользователя в список администраторов.
    7. Обрабатывает ошибки и очищает состояние FSM.

    Параметры:
        message (Message): Объект сообщения с введённым ID.
        state (FSMContext): Контекст состояния FSM.

    Исключения:
        ValueError: Если введённое значение не является числом.
        Exception: Любые другие ошибки работы с БД или Telegram API.
    """

    cancel_kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Назад", callback_data="admin_panel")]
        ])

    await safe_delete_message(message)

    try:
        data = await state.get_data()
        message_id_to_delete = data.get("message_id")
        if message_id_to_delete:
            try:
                await message.bot.delete_message(chat_id=message.chat.id, message_id=message_id_to_delete)
            except Exception as e:
                logger.debug(f"⚠️ Не удалось удалить сообщение отправленное add_admin: {e}")

        try:
            user_id = int(message.text)
        except ValueError:
            # ❌ НЕВЕРНЫЙ ФОРМАТ ID - ПРОСИМ ПОВТОРИТЬ ВВОД
            error_msg = await message.answer("❌ Неверный формат ID. Введите числовой ID.")
            logger.debug(f"⚠️ Введён некорректный ID: '{message.text}'")

            await asyncio.sleep(1)

            new_instruction_msg = await message.answer(
                text="Повторно введите ID пользователя для назначения администратором.\n"
                     "Для того чтобы узнать id можно воспользоваться @username_to_id_bot",
                reply_markup=cancel_kb
            )

            await state.update_data(message_id=new_instruction_msg.message_id)

            await asyncio.sleep(1)
            await safe_delete_message(error_msg)

            return

        if user_id == message.from_user.id:
            await message.answer("❌ Вы не можете назначить администратором самого себя.")
            await state.clear()
            return

        async with AsyncSessionLocal() as session:
            user = await session.get(User, user_id)
            if not user:
                error_msg = await message.answer(f"❌ Пользователь с ID {user_id} не найден.")
                logger.info(f"⚠️ Попытка назначить администратором несуществующего пользователя {user_id}.")

                await asyncio.sleep(1)

                new_instruction_msg = await message.answer(
                    text="Повторно введите ID пользователя для назначения администратором.\n"
                         "Для того чтобы узнать id можно воспользоваться @username_to_id_bot",
                    reply_markup=cancel_kb
                )

                await state.update_data(message_id=new_instruction_msg.message_id)

                await asyncio.sleep(1)
                await safe_delete_message(error_msg)

                return

            if user.role == 1:
                await message.answer(f"ℹ️ Пользователь {user_id} уже является администратором.")
                logger.info(f"ℹ️ Попытка назначить администратором пользователя {user_id}, который уже им является.")
                await state.clear()
                return

            await session.execute(
                update(User)
                .where(User.id == user_id)
                .values(role=1)
            )
            await session.commit()

            success_message = f"✅ Пользователь {user_id} назначен администратором."
            await message.answer(success_message)
            logger.info(success_message)

            await add_admin_to_list(user_id)

            await state.clear()

    except Exception as e:
        await message.answer("❌ Ошибка при назначении администратора.")
        logger.error(
            f"❌ Ошибка при назначении администратора в reading_id. "
            f"Введённый ID: '{message.text}': {e}"
        )
        await state.clear()


@router.callback_query(F.data == "list_of_admins", IsAdminFilter())
async def list_admins(callback: CallbackQuery):
    """
    Отображает список всех администраторов системы.

    Действия:
    1. Получает список администраторов из памяти (LIST_ADMINS).
    2. Формирует список в формате MarkdownV2 с нумерацией, ID, ссылками и username.
    3. Подсвечивает текущего администратора пометкой "⭐ Это вы".
    4. Добавляет кнопки для удаления других администраторов.
    5. Добавляет справку с ссылками, если @user_name ссылка не работает.

    Параметры:
        callback (CallbackQuery): Объект callback-запроса.
    """

    admins = admin_list.LIST_ADMINS

    if not admins:
        await callback.message.edit_text(
            text="📋 Список администраторов пуст.",
            reply_markup=InlineKeyboardBuilder()
            .button(text="◀️ Назад", callback_data="admin_panel")
            .as_markup()
        )
        await callback.answer()
        return

    text = "📋 *Список администраторов:*\n\n"

    for i, (admin_id, username) in enumerate(admins.items(), 1):
        escaped_id = escape_md_v2(str(admin_id))

        username = get_admin_username(admin_id)
        username_text = f" — {escape_md_v2(username)}" if username is not None else ""

        current_user_marker = " ⭐ Это вы" if admin_id == callback.from_user.id else ""

        text += (f"{i}\\. ID `{escaped_id}`{username_text}"
                 f"\n     {current_user_marker}\n")

    text += (
        "\nПри не рабочей или отсутствующей ссылке воспользуйтесь:\n"
        "app\\: tg\\:\\/\\/user\\?id\\=ID\n"
        "web\\: https\\:\\/\\/web\\.telegram\\.org\\/k\\/\\#ID\n"
    )

    builder = InlineKeyboardBuilder()
    for admin_id, username in admins.items():
        if admin_id != callback.from_user.id:
            builder.button(
                text=f"🗑️ Удалить {admin_id}",
                callback_data=f"remove_admin_{admin_id}"
            )

    builder.button(text="◀️ Назад", callback_data="admin_panel")
    builder.adjust(1)

    await callback.message.edit_text(
        text,
        parse_mode="MarkdownV2",
        reply_markup=builder.as_markup(),
        disable_web_page_preview=True
    )
    await callback.answer()


@router.callback_query(F.data.startswith("remove_admin_"), IsAdminFilter())
async def remove_admin_handler(callback: CallbackQuery):
    """
    Обрабатывает удаление администратора по нажатию кнопки "🗑️ Удалить".

    Действия:
    1. Извлекает ID администратора из callback.data.
    2. Проверяет, что администратор не удаляет сам себя.
    3. Устанавливает пользователю роль на роль пользователя в базе данных.
    4. Удаляет администратора из списка LIST_ADMINS.
    5. Логирует все действия и отправляет уведомление.

    Параметры:
        callback (CallbackQuery): Объект callback-запроса.

    Исключения:
        ValueError: Если ID некорректный.
        IndexError: Если формат callback.data некорректен.
        Exception: Любые другие ошибки в процессе удаления.
    """

    try:
        admin_id = int(callback.data.split("_")[2])

        if admin_id == callback.from_user.id:
            await callback.answer(text="❌ Вы не можете снять права с самого себя", show_alert=True)
            return

        async with AsyncSessionLocal() as session:
            user = await session.get(User, admin_id)
            if not user:
                await callback.answer(text="❌ Пользователь не найден", show_alert=True)
                return

            await session.execute(
                update(User)
                .where(User.id == admin_id)
                .values(role=0)
            )
            await session.commit()

        txt = f"✅ ID: {admin_id} снят с должности администратора."
        logger.info(txt)
        await callback.message.edit_text(text=txt)
        await callback.answer()

        await remove_admin_from_list(admin_id)

        await asyncio.sleep(1)
        await callback.message.edit_text(text="Админ панель", reply_markup=get_admin_kb())

    except (ValueError, IndexError) as e:
        await callback.answer(text="❌ Ошибка при обработке запроса", show_alert=True)
        logger.error(f"❌ Ошибка в при снятии роли администратора: {e}")
    except Exception as e:
        await callback.answer(text="❌ Произошла ошибка", show_alert=True)
        logger.error(f"❌ Ошибка в при снятии роли администратора: {e}")


@router.callback_query(F.data == "get_logs", IsAdminFilter())
async def send_buffered_logs(callback: CallbackQuery):
    """
    Отправляет текущие хранящиеся логи (из буфера).
    """

    await safe_delete_callback_message(callback)

    file_bytes = global_buffer_handler.get_logs_as_file()
    input_file = BufferedInputFile(file_bytes.getvalue(), filename="buffered_logs.txt")

    await callback.message.answer_document(
        document=input_file,
        caption=f"Последние {global_buffer_handler.capacity} логов",
    )

@router.callback_query(F.data == "get_users", IsAdminFilter())
async def get_users(callback: CallbackQuery):
    """
    Обработчик кнопки "Пользователи".
    Выводит общее количество зарегистрированных пользователей.
    """

    try:
        async with AsyncSessionLocal() as session:
            total_users_query = await session.execute(
                select(func.count(User.id))
            )
            total_users = total_users_query.scalar_one()

            message = f"👥 *Общее количество зарегистрированных пользователей:* {total_users}"

            await callback.message.edit_text(
                message,
                parse_mode="MarkdownV2",
            )
            await callback.answer()

    except Exception as e:
        logger.error(f"⚠️ Ошибка при получении количества пользователей: {e}")
        await callback.message.edit_text(
            "⚠️ Произошла ошибка при получении количества пользователей."
        )
        await callback.answer()
