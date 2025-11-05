"""
Реализует пользовательский обработчик логов для Python logging,
который асинхронно отправляет сообщения в Telegram-чат.
Встроена защита от спама (rate-limiting), поддержка длинных сообщений
и буфер последних записей для контекста при ошибках.

Функциональность:
- Асинхронная очередь для логов.
- Ограничение длины сообщений (4000 символов).
- Повторная попытка при ошибке отправки.
- Хранение последних N логов в памяти.
- При ошибке отправляются: буфер последних логов + сам файл ошибки.
"""

from datetime import datetime
import logging
import asyncio
from io import BytesIO

from aiogram import Bot
from asyncio import Queue
from aiogram.exceptions import TelegramRetryAfter, TelegramMigrateToChat

from app.bot.bot import bot as bot_info_log
from app.config import settings
from aiogram.types import BufferedInputFile
from app.utils.custom_logging.BufferedLogHandler import global_buffer_handler


class TelegramLogHandler(logging.Handler):
    """
    Асинхронный обработчик логов для отправки сообщений в Telegram.

    Особенности:
    - Работает через очередь asyncio.Queue.
    - Сообщения дробятся на части, если превышают 4000 символов.
    - Между сообщениями выдерживается RATE_LIMIT.
    - При ошибках (ERROR+) отправляются:
        • recent_logs.txt — последние логи
        • error_<timestamp>.txt — сам лог ошибки

    Поля:
    MAX_MESSAGE_LENGTH : int
        Максимальная длина одного сообщения в Telegram.
    RATE_LIMIT : float
        Минимальная задержка между отправками сообщений (секунды).
    TIME_LIMIT : int
        Задержка между повторными попытками при ошибке
    MAX_RETRIES : int
        Максимальное количество повторных попыток
    _queue : asyncio.Queue
        Очередь сообщений для отправки.
    _worker_task : asyncio.Task | None
        Задача фонового воркера.
    """

    MAX_MESSAGE_LENGTH = 4000
    RATE_LIMIT = 1.5
    TIME_SLEEP = 21
    MAX_RETRIES = 3

    _queue: Queue
    _worker_task: asyncio.Task | None = None

    def __init__(self, bot: Bot, chat_id: int, level=logging.WARNING):
        """
        Параметры:
        bot : aiogram.Bot
            Экземпляр бота для отправки сообщений.
        chat_id : int
            ID чата, куда будут отправляться логи.
        level : int
            Минимальный уровень логов (по умолчанию WARNING).
        """

        super().__init__(level)
        self.bot = bot
        self.chat_id = chat_id
        self._queue = Queue()
        self._start_worker()

    def _start_worker(self):
        """
        Запускает фоновую задачу-воркер, если она ещё не запущена.

        - Проверяет наличие self._worker_task; если оно пусто, создаёт задачу
            asyncio.create_task(self._worker()) и сохраняет её в self._worker_task.
        """

        if not self._worker_task:
            self._worker_task = asyncio.create_task(self._worker())

    async def _worker(self):
        """
        Фоновый воркер последовательно отправляет сообщения из очереди в Telegram.

        Логика работы:
            1. Бесконечный цикл: ждём сообщение из self._queue (await self._queue.get()).
            2. Пытаемся отправить сообщение через await self.bot.send_message(self.chat_id, message).
               - При успешной отправке помечаем sent = True.
               - При ошибке — ждем фиксированное время (21 сек) и пробуем снова (повторные попытки).
            3. После успешной отправки делаем await asyncio.sleep(self.RATE_LIMIT) — соблюдение rate-limit.
            4. Вызываем self._queue.task_done() для уведомления об обработке элемента очереди.
            5. Переходим к следующему сообщению.

        Особенности реализации:
            - Повторная попытка при любом исключении.
            - Фиксированная пауза при ошибке отправки (21 сек).
            - Ошибки типа error и выше отправляются в файлом
        """

        while True:
            item = await self._queue.get()
            retries = 0
            sent = False

            while not sent and retries < self.MAX_RETRIES:
                try:
                    if isinstance(item, str):
                        await self.bot.send_message(self.chat_id, item)

                    elif isinstance(item, dict) and item.get("as_file"):
                        file_bytes: BytesIO = item["file"]
                        caption = item.get("caption", "Ошибка")
                        filename = item.get("filename", "error_log.txt")

                        file_bytes.seek(0)
                        input_file = BufferedInputFile(file_bytes.getvalue(), filename=filename)

                        await self.bot.send_document(
                            chat_id=self.chat_id,
                            document=input_file,
                            caption=caption,
                            disable_notification=False,
                        )

                    sent = True

                except TelegramRetryAfter as e:
                    wait_time = e.retry_after + 2
                    logging.info(
                        f"⚠️ Flood control. Ожидание {wait_time} секунд перед повторной попыткой {retries + 1}/{self.MAX_RETRIES}"
                    )
                    await asyncio.sleep(wait_time)
                    retries += 1

                except Exception as e:
                    wait_time = min(self.TIME_SLEEP * retries, 60)
                    logging.info(
                        f"⚠️ Не удалось отправить лог (попытка {retries}). Повтор через {wait_time} сек. Ошибка: {e}"
                    )
                    await asyncio.sleep(wait_time)
                    retries += 1

            if not sent:
                logging.error(f"⚠️ Не удалось отправить лог после {self.MAX_RETRIES} попыток")

            await asyncio.sleep(self.RATE_LIMIT)
            self._queue.task_done()

    def emit(self, record: logging.LogRecord):
        """
        Обрабатывает запись лога и помещает её в очередь на отправку в Telegram чат.

        В зависимости от уровня важности лога выбирает способ отправки:
        - WARNING и выше: отправляет как файл с контекстом последних логов
        - INFO: отправляет как текстовое сообщение

        Параметры:
            record : logging.LogRecord
                Объект записи лога, содержащий всю информацию о событии

        Возвращает:
            None: Функция не возвращает значения, результаты помещаются в очередь.

        Исключения:
            Любые исключения перехватываются и передаются в self.handleError()

        Логика работы:
          1. Форматирует запись лога согласно настройкам форматтера
          2. Для WARNING/ERROR/CRITICAL:
             - Создает объединенный файл с последними логами и текущей ошибкой
             - Помещает файл в очередь на отправку как документ Telegram
          3. Для INFO:
             - Разбивает длинные сообщения на части по MAX_MESSAGE_LENGTH
             - Помещает текстовые сообщения в очередь на отправку
          4. При ошибках обработки вызывает стандартный handleError
        """

        try:
            log_entry = self.format(record)

            # --- WARNING и выше (отправка файлом) ---
            if record.levelno >= logging.WARNING:
                timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

                level_config = {
                    logging.WARNING: {
                        "log_type": "ПРЕДУПРЕЖДЕНИЕ",
                        "filename_prefix": "warning",
                        "emoji": "⚠️"
                    },
                    logging.ERROR: {
                        "log_type": "ОШИБКА",
                        "filename_prefix": "error",
                        "emoji": "❌"
                    },
                    logging.CRITICAL: {
                        "log_type": "КРИТИЧЕСКАЯ ОШИБКА",
                        "filename_prefix": "critical",
                        "emoji": "💥"
                    }
                }

                config = level_config.get(record.levelno, level_config[logging.WARNING])

                combined_file = BytesIO()

                buffer_content = global_buffer_handler.get_logs_as_text(self.formatter)
                combined_file.write(
                    f"=== ПОСЛЕДНИЕ ЛОГИ ({len(global_buffer_handler.buffer)} записей) ===\n".encode("utf-8"))
                combined_file.write(buffer_content.encode("utf-8"))
                combined_file.write(f"\n\n=== {config['log_type']} ===\n".encode("utf-8"))
                combined_file.write(log_entry.encode("utf-8"))

                self._queue.put_nowait({
                    "as_file": True,
                    "file": combined_file,
                    "filename": f"{config['filename_prefix']}_{timestamp}.txt",
                    "caption": f"{config['emoji']} {config['log_type']}"
                })
                return

            # --- INFO (отправка текстом) ---
            messages = self._split_message(log_entry)
            for idx, chunk in enumerate(messages, 1):
                if len(messages) > 1:
                    chunk = f"[{idx}/{len(messages)}] {chunk}"
                self._queue.put_nowait(chunk)

        except Exception:
            self.handleError(record)

    def _split_message(self, message: str):
        """
        Разбивает сообщение на части по MAX_MESSAGE_LENGTH символов.

        Параметры:
        message : str
            Исходное сообщение.

        Возвращает:
        list[str]
            Список частей сообщения.
        """

        chunks = []
        start = 0
        while start < len(message):
            end = start + self.MAX_MESSAGE_LENGTH
            chunks.append(message[start:end])
            start = end
        return chunks


async def send_chat_info_log(text: str, max_retries: int = 3):
    """
    Отправляет информационный лог уровня INFO в Telegram-чат.
    Используется вручную (например, уведомлений об операций).

    Параметры:
        text : str
            Текст сообщения.
        max_retries : int
            Максимальное количество повторных попыток (по умолчанию 3)
    """

    now = datetime.now()
    timestamp = now.strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]
    formatted_text = f"{timestamp} [INFO] {text}"

    retries = 0
    sent = False

    while not sent and retries < max_retries:
        try:
            await bot_info_log.send_message(settings.TELEGRAM_LOG_CHAT_ID, text=formatted_text)
            sent = True

        except TelegramRetryAfter as e:
            retries += 1
            wait_time = e.retry_after + 2
            logging.info(
                f"Flood control при отправке info лога. Ожидание {wait_time} секунд. "
                f"Попытка {retries}/{max_retries}"
            )
            await asyncio.sleep(wait_time)

        except TelegramMigrateToChat as e:
            logging.warning(f"Группа для логов была перенесена в супер группу. Обновите chat_id.\nТекст ошибки: {e}")
            break

        except Exception as e:
            retries += 1
            logging.info(
                f"Не удалось отправить info лог. Ошибка: {e}. "
                f"Попытка {retries}/{max_retries}"
            )
            if retries < max_retries:
                await asyncio.sleep(5)

    if not sent:
        logging.error(f"Не удалось отправить info лог после {max_retries} попыток: {text}")