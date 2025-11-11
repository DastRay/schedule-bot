import logging

from aiogram.types import Message, CallbackQuery
from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from sqlalchemy import delete, select

import app.keyboards.registration_kb as registration_kb
from app.database.db import AsyncSessionLocal
from app.database.models import User
from app.keyboards.main_menu_kb import get_main_menu_kb
from app.state.states import RegistrationStates
from app.utils.messages.safe_actions_with_messages import safe_delete_message, safe_delete_callback_message

router = Router()
logger = logging.getLogger(__name__)


@router.message(F.text == "Прочие функции")
async def other_functions(message: Message):
    """Меню прочих функций"""
    await safe_delete_message(message)

    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Изменить группу", callback_data="change_group_data")],
            [InlineKeyboardButton(text="Выйти из профиля", callback_data="logout")],
            [InlineKeyboardButton(text="Назад", callback_data="exit_other_functions")]
        ]
    )

    await message.answer(text="Выберите действие:", reply_markup=kb)


@router.callback_query(F.data == "change_group_data")
async def change_personal_data(callback: CallbackQuery, state: FSMContext):
    """Запуск процесса изменения персональных данных"""
    await callback.message.edit_text(text="Выберите ваш факультет:", reply_markup=registration_kb.faculty_keyboard_reg)
    await state.set_state(RegistrationStates.choice_faculty)
    await callback.answer()

@router.callback_query(F.data == "logout")
async def logout_user(callback: CallbackQuery):
    """Выход из профиля - удаление пользователя из базы данных"""
    await safe_delete_callback_message(callback)
    try:
        user_id = callback.from_user.id

        async with AsyncSessionLocal() as session:
            query = await session.execute(select(User).where(User.id == user_id))
            existing_user = query.scalars().first()

            if not existing_user:
                await callback.answer(text="❌ Вы не зарегистрированы!", show_alert=True)
                return

            await session.execute(delete(User).where(User.id == user_id))
            await session.commit()

        updated_keyboard = await get_main_menu_kb(user_id)

        await callback.message.answer(
            text="✅ Вы вышли из профиля.\n\n"
            "Теперь у вас доступен ограниченный функционал. "
            "Для доступа ко всем функциям пройдите регистрацию.",
            reply_markup=updated_keyboard
        )
        await callback.answer()

    except Exception as e:
        logger.error(f"Ошибка при выходе из профиля: {e}", exc_info=True)
        await callback.answer(text="❌ Произошла ошибка при выходе из профиля")


@router.callback_query(F.data == "exit_other_functions")
async def exit_other_functions(callback: CallbackQuery):
    """Выход из меню прочих функций"""
    await safe_delete_callback_message(callback)