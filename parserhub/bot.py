"""Главный модуль Telegram бота ParserHub"""
import sys
import asyncio
import httpx
from pathlib import Path
from loguru import logger
from telegram import BotCommand
from telegram.ext import Application

from parserhub.config import config
from parserhub.db_service import DatabaseService
from parserhub.session_manager import SessionManager
from parserhub.api_client import WorkersAPI, RealtyAPI
from parserhub.services.subscription_service import SubscriptionService

# Импорт handlers
from parserhub.handlers.start import register_start_handlers
from parserhub.handlers.auth import register_auth_handlers
from parserhub.handlers.settings import register_settings_handlers
from parserhub.handlers.subscription import register_subscription_handlers
from parserhub.handlers.workers import register_workers_handlers
from parserhub.handlers.realty import register_realty_handlers
from parserhub.handlers.blacklist import register_blacklist_handlers
from parserhub.handlers.admin import register_admin_handlers


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

    # Инициализировать сервис подписок
    subscription_service = SubscriptionService(config.DB_PATH)
    await subscription_service.init_table()

    # Создать API клиенты
    workers_api = WorkersAPI(config.WORKERS_SERVICE_URL)
    realty_api = RealtyAPI(config.REALTY_SERVICE_URL)

    # Сохранить в bot_data
    application.bot_data["db"] = db
    application.bot_data["session_manager"] = session_manager
    application.bot_data["subscription"] = subscription_service
    application.bot_data["workers_api"] = workers_api
    application.bot_data["realty_api"] = realty_api

    # Очистить зомби-задачи (задачи, которых уже нет в сервисах после рестарта)
    await _reconcile_tasks(db, config.WORKERS_SERVICE_URL, config.REALTY_SERVICE_URL)

    # Запустить фоновую очистку истёкших подписок
    application.bot_data["cleaner_task"] = asyncio.create_task(
        _subscription_cleaner_loop(application)
    )
    # Запустить фоновую очистку зависших сессий авторизации Pyrogram
    application.bot_data["auth_cleaner_task"] = asyncio.create_task(
        _auth_cleaner_loop(application)
    )
    # Запустить фоновую очистку словаря антиспама
    application.bot_data["antispam_task"] = asyncio.create_task(
        _antispam_cleaner_loop(application)
    )

    # Установить команды бота (Menu Button)
    commands = [
        BotCommand("start", "🏠 Главное меню"),
    ]
    await application.bot.set_my_commands(commands)
    logger.info("Команды бота установлены (Menu Button)")

    logger.info("Инициализация завершена")


async def _reconcile_tasks(db: DatabaseService, workers_url: str, realty_url: str):
    """При старте удаляем из active_tasks задачи, которых уже нет в сервисах (зомби)"""
    tasks = await db.get_all_running_tasks()
    if not tasks:
        logger.info("Reconcile: активных задач нет")
        return

    removed = 0
    async with httpx.AsyncClient(timeout=5.0) as client:
        for task in tasks:
            try:
                if task.service == "workers":
                    r = await client.get(f"{workers_url}/workers/status/{task.task_id}")
                elif task.service == "realty":
                    r = await client.get(f"{realty_url}/parse/status/{task.task_id}")
                else:
                    continue

                if r.status_code == 404:
                    await db.delete_task(task.task_id)
                    removed += 1
                    logger.info(f"Reconcile: зомби-задача удалена {task.task_id} (user={task.user_id})")

            except Exception as e:
                # Сервис недоступен — не удаляем, он может подняться
                logger.warning(f"Reconcile: не удалось проверить {task.task_id}: {e}")

    logger.info(f"Reconcile завершён: проверено {len(tasks)}, удалено зомби: {removed}")


async def _antispam_cleaner_loop(application: Application):
    """Фоновая задача: очистка словаря антиспама для освобождения RAM"""
    from parserhub.validators import AntiSpam
    while True:
        try:
            await asyncio.sleep(600)  # Раз в 10 минут
            AntiSpam.cleanup_old()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"AntiSpam cleaner error: {e}")


async def _auth_cleaner_loop(application: Application):
    """Фоновая задача: очистка зависших сессий авторизации Pyrogram"""
    while True:
        try:
            await asyncio.sleep(300)  # Проверять каждые 5 минут
            session_mgr = application.bot_data.get("session_manager")
            if session_mgr:
                await session_mgr.cleanup_stale_clients()
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Auth cleaner error: {e}")


async def _subscription_cleaner_loop(application: Application):
    """Фоновая задача: удаление истёкших подписок раз в сутки"""
    while True:
        try:
            await asyncio.sleep(24 * 60 * 60)  # раз в сутки
            service: SubscriptionService = application.bot_data["subscription"]
            removed = await service.delete_expired()
            if removed:
                logger.info(f"Expired subscriptions cleaned: {removed}")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Subscription cleaner error: {e}")


async def post_shutdown(application: Application):
    """Очистка при остановке бота"""
    logger.info("Остановка бота...")

    # НЕ удаляем active_tasks при shutdown — задачи в сервисах восстанавливаются сами
    # после рестарта (workers-service из 'paused', realty-monitor из 'suspended').
    # _reconcile_tasks при следующем post_init очистит настоящие зомби (404 от сервиса).

    # Закрыть HTTP клиенты
    if "workers_api" in application.bot_data:
        await application.bot_data["workers_api"].close()

    if "realty_api" in application.bot_data:
        await application.bot_data["realty_api"].close()

    # Отменить все фоновые задачи и дождаться их завершения
    tasks_to_cancel = []
    for task_key in ("cleaner_task", "auth_cleaner_task", "antispam_task"):
        task = application.bot_data.get(task_key)
        if task and not task.done():
            task.cancel()
            tasks_to_cancel.append(task)

    if tasks_to_cancel:
        await asyncio.gather(*tasks_to_cancel, return_exceptions=True)

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
    # ConversationHandler'ы регистрируются ДО start_handlers, чтобы их fallback'и
    # перехватывали кнопки меню ("❌ Отмена", "🔙 Назад" и т.д.) раньше
    # глобального menu_button_handler — иначе состояние диалога не сбрасывается.
    register_auth_handlers(app)
    register_settings_handlers(app)
    register_subscription_handlers(app)
    register_workers_handlers(app)
    register_realty_handlers(app)
    register_blacklist_handlers(app)
    register_admin_handlers(app)
    register_start_handlers(app)  # ПОСЛЕДНИМ: catch-all для главного меню

    logger.info("Handlers зарегистрированы")

    # Запуск бота
    logger.info("Запуск polling...")
    app.run_polling(allowed_updates=["message", "callback_query", "pre_checkout_query"])


if __name__ == "__main__":
    main()
