from aiogram import Router, types
from aiogram.filters import CommandStart
from app.keyboards.main_menu import start_keyboard

router = Router()

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    """
    Обработчик команды /start.

    Отправляет приветственное сообщение и клавиатуру главного меню.

    Аргументы:
        message (types.Message): Объект сообщения Telegram.
    """

    await message.answer(text="Привет! Я твой бот, буду помогать с раписанием 🤖",
                         reply_markup=start_keyboard())