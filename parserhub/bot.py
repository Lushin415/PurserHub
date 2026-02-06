"""Главный модуль Telegram бота ParserHub"""
import sys
from pathlib import Path
from loguru import logger
from telegram import BotCommand
from telegram.ext import Application

from parserhub.config import config
from parserhub.db_service import DatabaseService
from parserhub.session_manager import SessionManager
from parserhub.api_client import WorkersAPI, RealtyAPI

# Импорт handlers
from parserhub.handlers.start import register_start_handlers
from parserhub.handlers.auth import register_auth_handlers
from parserhub.handlers.settings import register_settings_handlers
from parserhub.handlers.workers import register_workers_handlers
from parserhub.handlers.realty import register_realty_handlers
from parserhub.handlers.blacklist import register_blacklist_handlers


def setup_logging():
    """Настройка логирования"""
    logger.remove()  # Удалить дефолтный handler

    # Console logging
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="DEBUG",  # Изменено на DEBUG для отладки авторизации
    )

    # File logging
    logger.add(
        config.LOG_PATH,
        format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}",
        level="DEBUG",
        rotation="10 MB",
        retention="7 days",
    )

    logger.info("Логирование настроено")


async def post_init(application: Application):
    """Инициализация после запуска бота"""
    logger.info("Инициализация бота...")

    # Создать директорию для сессий
    sessions_dir = Path(config.SESSIONS_DIR)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f"Директория сессий: {sessions_dir.resolve()}")

    # Инициализировать БД
    db = DatabaseService(config.DB_PATH)
    await db.init_db()

    # Создать session manager
    session_manager = SessionManager(
        sessions_dir=config.SESSIONS_DIR,
        api_id=config.API_ID,
        api_hash=config.API_HASH,
    )

    # Создать API клиенты
    workers_api = WorkersAPI(config.WORKERS_SERVICE_URL)
    realty_api = RealtyAPI(config.REALTY_SERVICE_URL)

    # Сохранить в bot_data
    application.bot_data["db"] = db
    application.bot_data["session_manager"] = session_manager
    application.bot_data["workers_api"] = workers_api
    application.bot_data["realty_api"] = realty_api

    # Установить команды бота (Menu Button)
    commands = [
        BotCommand("start", "🏠 Главное меню"),
        BotCommand("menu", "📋 Главное меню"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Команды бота установлены (Menu Button)")

    logger.info("Инициализация завершена")


async def post_shutdown(application: Application):
    """Очистка при остановке бота"""
    logger.info("Остановка бота...")

    # Закрыть HTTP клиенты
    if "workers_api" in application.bot_data:
        await application.bot_data["workers_api"].close()

    if "realty_api" in application.bot_data:
        await application.bot_data["realty_api"].close()

    logger.info("Бот остановлен")


def main():
    """Запуск бота"""
    setup_logging()

    logger.info("=" * 50)
    logger.info("ParserHub Bot - Starting")
    logger.info(f"Workers Service: {config.WORKERS_SERVICE_URL}")
    logger.info(f"Realty Service: {config.REALTY_SERVICE_URL}")
    logger.info(f"Sessions Directory: {config.SESSIONS_DIR}")
    logger.info("=" * 50)

    # Создать приложение
    app = (
        Application.builder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # Регистрация handlers (порядок важен!)
    register_start_handlers(app)
    register_auth_handlers(app)
    register_settings_handlers(app)
    register_workers_handlers(app)
    register_realty_handlers(app)
    register_blacklist_handlers(app)

    logger.info("Handlers зарегистрированы")

    # Запуск бота
    logger.info("Запуск polling...")
    app.run_polling(allowed_updates=["message", "callback_query"])


if __name__ == "__main__":
    main()
