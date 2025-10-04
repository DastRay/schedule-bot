from aiogram import Router, types
from aiogram.filters import CommandStart
from app.keyboards.main_menu_kb import start_keyboard

router = Router()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    """
    @brief Обработчик команды /start.

    @details Отправляет приветственное сообщение и клавиатуру главного меню.

    @param message (types.Message): Объект сообщения Telegram.
    """

    await message.answer(text="Привет! Я твой бот, буду помогать с раcписанием 🤖",
                         reply_markup=start_keyboard())