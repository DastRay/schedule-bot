"""
Предоставляет безопасные функции для удаления сообщений в Telegram с использованием Aiogram.
Поддерживаются объекты Message, CallbackQuery, а также удаление по chat_id и message_id.
Все ошибки TelegramBadRequest обрабатываются и логируются корректно.
"""

import logging
from aiogram.types import Message, CallbackQuery
from aiogram.exceptions import TelegramBadRequest
from aiogram import Bot

from app.bot.bot import bot

logger = logging.getLogger(__name__)


def _handle_delete_error(context: str, e: TelegramBadRequest,
                         chat_id: int | None = None, message_id: int | None = None):
    """
    Унифицированная обработка ошибок TelegramBadRequest при удалении сообщений.

    Параметры:
        context (str): Контекст операции (например, "message", "callback", "by_id").
        e (TelegramBadRequest): Исключение Telegram.
        chat_id (int | None): ID чата, если известен.
        message_id (int | None): ID сообщения, если известен.
    """

    text = str(e)
    msg_info = f"(chat_id={chat_id}, message_id={message_id})"
    prefix = f"[{context}]"

    if "message to delete not found" in text:
        logger.debug(f"⚠️ {prefix} Сообщение уже удалено {msg_info}.")
    elif "message can't be deleted" in text:
        logger.debug(f"⚠️ {prefix} Сообщение нельзя удалить (возможно, старое или системное) {msg_info}.")
    elif "not enough rights" in text:
        logger.debug(f"⚠️ {prefix} Недостаточно прав для удаления сообщения {msg_info}. "
                       f"Бот должен быть администратором.")
    elif "chat not found" in text:
        logger.debug(f"⚠️ {prefix}️ Чат не найден при удалении {msg_info}.")
    else:
        logger.error(f"❌ {prefix} Неизвестная ошибка TelegramBadRequest при удалении {msg_info}: {e}")


async def safe_delete_message(message: Message) -> bool:
    """
    Безопасно удаляет сообщение Telegram через объект Message.

    Обрабатывает все распространённые ошибки Telegram и логирует их.

    Параметры:
        message (Message): Объект сообщения для удаления.

    Возвращает:
        bool: True, если сообщение успешно удалено; False в противном случае.
    """

    if message is None:
        logger.debug("safe_delete_message: объект message отсутствует.")
        return False

    try:
        await message.delete()
        return True

    except TelegramBadRequest as e:
        _handle_delete_error("message", e, chat_id=message.chat.id, message_id=message.message_id)
        return False

    except Exception as e:
        logger.error(f"❌ [message] Ошибка при удалении сообщения {message.message_id}: {e}")
        return False


async def safe_delete_callback_message(callback: CallbackQuery) -> bool:
    """
    Безопасно удаляет сообщение, связанное с callback-запросом.

    Параметры:
        callback (CallbackQuery): Callback-запрос Telegram.

    Возвращает:
        bool: True, если сообщение успешно удалено; False в противном случае.
    """

    if callback is None or callback.message is None:
        logger.debug("safe_delete_message_by_id: некорректные параметры для удаления.")
        return False

    try:
        await callback.message.delete()
        await callback.answer()
        return True

    except TelegramBadRequest as e:
        await callback.answer()
        _handle_delete_error("callback", e, chat_id=callback.message.chat.id, message_id=callback.message.message_id)
        return False

    except Exception as e:
        await callback.answer()
        logger.error(
            f"❌ [callback] Неизвестная ошибка при удалении callback-сообщения {callback.message.message_id}: {e}"
        )
        return False


async def safe_delete_message_by_id(chat_id: int, message_id: int) -> bool:
    """
    Безопасно удаляет сообщение по chat_id и message_id.

    Параметры:
        chat_id (int): ID чата, содержащего сообщение.
        message_id (int): ID сообщения для удаления.

    Возвращает:
        bool: True, если сообщение успешно удалено; False в противном случае.
    """

    if bot is None or chat_id is None or message_id is None:
        logger.debug("safe_delete_message_by_id: некорректные параметры для удаления.")
        return False

    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
        return True

    except TelegramBadRequest as e:
        _handle_delete_error("by_id", e, chat_id=chat_id, message_id=message_id)
        return False

    except Exception as e:
        logger.error(f"❌ [by_id] Ошибка при удалении сообщения {message_id}: {e}")
        return False


async def safe_try_delete(target, *args, **kwargs) -> bool:
    """
    Универсальная безопасная функция удаления сообщения.

    Определяет тип аргумента и вызывает соответствующую функцию:
        - Message → safe_delete_message
        - CallbackQuery → safe_delete_callback_message
        - (Bot, chat_id, message_id) → safe_delete_message_by_id

    Примеры использования:
        await safe_try_delete(message)
        await safe_try_delete(callback)
        await safe_try_delete(bot, chat_id, message_id)

    Возвращает:
        bool: True, если сообщение успешно удалено; False в противном случае.
    """

    try:
        if isinstance(target, Message):
            return await safe_delete_message(target)

        elif isinstance(target, CallbackQuery):
            return await safe_delete_callback_message(target)

        elif isinstance(target, Bot) and len(args) >= 2:
            chat_id, message_id = args[:2]
            return await safe_delete_message_by_id(target, chat_id, message_id)
        else:
            logger.warning("safe_try_delete: некорректные аргументы, ничего не удалено.")
            return False

    except Exception as e:
        logger.error(f"❌ Ошибка в safe_try_delete: {e}")
        return False


async def safe_edit_message(callback: CallbackQuery, text: str, **kwargs):
    """
    Безопасное редактирование сообщения Telegram с обработкой ошибки идентичности содержимого.

    Обрабатывает случай, когда попытка редактирования сообщения приводит к ошибке
    "message is not modified", возникающей когда новое содержимое сообщения идентично текущему.

    Параметры:
        callback : CallbackQuery
            Объект callback запроса содержащий сообщение для редактирования
        text : str
            Новый текст сообщения
        **kwargs : dict
            Дополнительные параметры для метода callback.message.edit_text():
            - parse_mode : str, optional
                Режим парсинга текста
            - disable_web_page_preview : bool, optional
                Отключение предпросмотра веб-страниц
            - reply_markup : InlineKeyboardMarkup, optional
                Клавиатура для прикрепления к сообщению
            - И другие параметры, поддерживаемые методом edit_text()

    Возвращает:
        bool
            - True: сообщение успешно отредактировано или не требует изменений
            - False: никогда не возвращается (в случае ошибки выбрасывается исключение)
    """

    if not callback.message:
        raise ValueError("CallbackQuery не содержит сообщения для редактирования")

    try:
        await callback.message.edit_text(text, **kwargs)
        return True
    except Exception as e:
        if "message is not modified" in str(e):
            logger.debug(f"Не удалось изменить сообщение: {e} ")
            return True
        else:
            raise e