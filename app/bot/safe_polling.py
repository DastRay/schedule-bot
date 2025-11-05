import asyncio
import logging
from aiogram.exceptions import TelegramUnauthorizedError, TelegramNetworkError, TelegramRetryAfter
from app.bot.bot import dp, bot

logger = logging.getLogger(__name__)

async def safe_polling():
    """Цикл безопасного polling с перезапуском при временных ошибках."""

    while True:
        try:
            await dp.start_polling(
                bot,
                skip_updates=True,
                timeout=15
            )
            break
        except TelegramUnauthorizedError:
            logger.critical("❌ Ошибка авторизации Telegram — неверный токен.")
            print("❌ Telegram отклонил токен. Проверьте TELEGRAM_BOT_TOKEN.")
            break
        except TelegramRetryAfter as e:
            wait_time = e.retry_after + 2
            logger.warning(f"⚠️ Flood control GetUpdates. Ожидание {wait_time} секунд...")
            await asyncio.sleep(wait_time)
        except TelegramNetworkError:
            logger.error(f"⚠️ Ошибка сети при обращении к Telegram API. Перезапуск через 5 секунд...")
            await asyncio.sleep(5)
        except Exception as e:
            logger.error(f"⚠️ Необработанная ошибка в polling, перезапуск через 3 секунды. Ошибка: {e}")
            await asyncio.sleep(3)