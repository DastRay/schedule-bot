"""
Конфигурирует систему логирования для проекта.
Логи отправляются в консоль и (при уровне WARNING и выше) в Telegram.

- INFO → только консоль
- WARNING и ERROR → консоль + Telegram
"""

import logging
import traceback

from aiogram import Bot
from app.config import settings
from app.database.models import User
from app.filters.ContextFilter import ContextFilter
from app.utils.custom_logging.BufferedLogHandler import global_buffer_handler
from app.utils.custom_logging.TelegramLogHandler import TelegramLogHandler

logger = logging.getLogger(__name__)


def get_user_info(user: User = None) -> str:
    """
    Формирует текстовую информацию о пользователе для логирования.

    Собирает ключевую информацию о пользователе из базы данных в удобочитаемом формате,
    включая данные о группе и факультете. Используется для контекстного логирования ошибок.

    Параметры:
        user : User, optional
            Объект пользователя SQLAlchemy из таблицы users. Если None, возвращается
            строка-заглушка. По умолчанию None.

    Возвращает:
        str : Отформатированная строка с информацией о пользователе.
    """

    if user:
        group_info = f"группа '{user.group.group_name}'" if user and user.group else "группа не назначена"
        faculty_info = f"факультет '{user.faculty.name}'" if user and user.faculty else "факультет не назначен"
        return f"{group_info}, {faculty_info}"
    return "пользователь не передан"


def log_error_with_context(
        error: Exception,
        handler_name: str,
        user: User = None,
        additional_context: str = "",
        group_name: str = None
):
    """
    Логирует исключения с детальным контекстом выполнения для упрощения отладки.

    Формирует структурированное сообщение об ошибке, включающее информацию о пользователе,
    месте возникновения ошибки, типе исключения и полной трассировке вызовов.

    Параметры:
        error : Exception
            Объект исключения, который требуется логировать.

    handler_name : str
        Название обработчика или функции, в котором произошла ошибка.

    user : User, optional
        Объект пользователя SQLAlchemy. Используется для формирования контекста.
        Если None, информация о пользователе не включается. По умолчанию None.

    additional_context : str, optional
        Дополнительная текстовая информация о контексте ошибки. Может содержать
        данные о состоянии, параметрах запроса, идентификаторах и т.д.
        Например: "состояние=choice_faculty, неделя=plus"
        По умолчанию "".

    group_name : str, optional
        Название группы, если оно известно отдельно от объекта пользователя.
        Используется когда ошибка связана с конкретной группой, но объект
        пользователя недоступен. По умолчанию None.
    """

    user_info = get_user_info(user)

    tb = traceback.extract_tb(error.__traceback__)
    last_frame = tb[-1] if tb else None
    location = f"{last_frame.filename}:{last_frame.lineno}" if last_frame else "неизвестное место"

    context_parts = []
    if additional_context:
        context_parts.append(additional_context)
    if group_name:
        context_parts.append(f"запрошенная группа: {group_name}")

    context_str = f" [{', '.join(context_parts)}]" if context_parts else ""

    logger.error(
        f"\n Ошибка в {handler_name} для {user_info}{context_str}\n"
        f"   📍 Место: {location}\n"
        f"   🎯 Тип ошибки: {type(error).__name__}\n"
        f"   💬 Сообщение: {str(error)}\n"
        f"   🔍 Трассировка: {''.join(traceback.format_tb(error.__traceback__))}"
    )


def setup_logging(bot: Bot):
    """
    Настройка логирования приложения.

    Параметры:
    bot : aiogram.Bot
        Экземпляр Telegram-бота для отправки логов в чат.

    Возвращает:
    logging.Logger
        Root-логгер с двумя обработчиками:
        - ConsoleHandler (INFO+)
        - TelegramLogHandler (WARNING+)
    """

    # --- базовый логгер ---
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)

    context_filter = ContextFilter()

    log_format = (
        "%(asctime)s [%(levelname)s] "
        "[u_id=%(user_id)s u_n=@%(username)s] "
        "%(name)s: %(message)s"
    )

    # --- консоль ---
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(logging.Formatter(log_format))
    console_handler.addFilter(context_filter)
    logger.addHandler(console_handler)

    # --- буфер логов ---
    global_buffer_handler.setLevel(logging.DEBUG)
    global_buffer_handler.setFormatter(logging.Formatter(log_format))
    global_buffer_handler.addFilter(context_filter)
    logger.addHandler(global_buffer_handler)

    # --- телеграм ---
    tg_handler = TelegramLogHandler(bot, settings.TELEGRAM_LOG_CHAT_ID, level=logging.WARNING)
    tg_handler.setFormatter(logging.Formatter(log_format))
    tg_handler.addFilter(context_filter)
    logger.addHandler(tg_handler)