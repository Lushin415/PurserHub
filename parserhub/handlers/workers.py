"""Обработчики мониторинга ПВЗ"""
import asyncio
from datetime import datetime, timezone
from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.error import BadRequest
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)
from httpx import HTTPStatusError
from loguru import logger

from parserhub.db_service import DatabaseService
from parserhub.api_client import WorkersAPI
from parserhub.models import ActiveTask
from parserhub.validators import Validators
from parserhub.services.subscription_service import SubscriptionService
from parserhub.handlers.admin import _is_admin
from parserhub.handlers.start import cancel_and_return_to_menu, MAIN_MENU_FILTER, MenuButton, show_main_menu


# Состояния для ConversationHandler
class WorkersState:
    SELECT_MODE = 1
    INPUT_CITY = 2
    INPUT_DATE_FROM = 3
    INPUT_DATE_TO = 4
    INPUT_MIN_PRICE = 5
    INPUT_MAX_PRICE = 6
    INPUT_SHK_FILTER = 7
    CONFIRM = 8


# Reply-кнопки подменю
class WorkersBtn:
    START = "🚀 Поиск сотрудников"
    MY_TASKS = "📋 Задачи мониторинга"
    MODE_WORKER = "👷 Работники"
    MODE_EMPLOYER = "🏢 Работодатели"
    CONFIRM = "✅ Запустить"
    CITY_MSK = "🏙 Москва"
    CITY_SPB = "🌊 Санкт-Петербург"
    CITY_ALL = "🌍 Все источники"


# Callback data (только для inline-кнопок: задачи, уведомления)
class WorkersCB:
    WORKERS_MENU = "workers_menu"
    MY_TASKS = "my_worker_tasks"
    VIEW_TASK = "view_worker_task_"
    STOP_TASK = "stop_worker_task_"
    STOP_ALL_TASKS = "stop_all_worker_tasks"
    FORCE_CLOSE_TASK = "force_close_worker_task_"


async def show_workers_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню мониторинга ПВЗ"""
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    user = await db.get_user(user_id)

    if user is None or not user.is_parser_authorized:
        keyboard = ReplyKeyboardMarkup([
            [KeyboardButton(MenuButton.ACCOUNT)],
            [KeyboardButton(MenuButton.BACK)],
        ], resize_keyboard=True)

        text = (
            "👷 <b>Мониторинг ПВЗ</b>\n\n"
            "❌ Для работы необходимо авторизовать аккаунт парсера.\n\n"
            "Перейдите в «👤 Мой аккаунт» для авторизации."
        )

        if update.callback_query:
            await update.callback_query.answer()
            msg = update.callback_query.message
            if msg:
                await msg.reply_text(text=text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await update.message.reply_text(text=text, reply_markup=keyboard, parse_mode="HTML")
        return

    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton(WorkersBtn.START), KeyboardButton(WorkersBtn.MY_TASKS)],
        [KeyboardButton(MenuButton.BLACKLIST)],
        [KeyboardButton(MenuButton.BACK)],
    ], resize_keyboard=True)

    text = (
        "👷 <b>Мониторинг ПВЗ</b>\n\n"
        "Мониторинг чатов с вакансиями и резюме работников ПВЗ.\n\n"
        "Выберите действие:"
    )

    if update.callback_query:
        await update.callback_query.answer()
        msg = update.callback_query.message
        if msg:
            await msg.reply_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.message.reply_text(text=text, reply_markup=keyboard, parse_mode="HTML")


async def start_monitoring_select_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выбор режима: работники или работодатели"""
    # Проверка подписки
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    if not await _is_admin(user_id, db):
        sub_service: SubscriptionService = context.bot_data["subscription"]
        if not await sub_service.has_access(user_id):
            await update.message.reply_text(
                "🔒 <b>Требуется подписка</b>\n\n"
                "Для запуска мониторинга ПВЗ необходима активная подписка.\n"
                "Перейдите в «💳 Подписка» для оформления.",
                parse_mode="HTML",
            )
            return ConversationHandler.END

    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton(WorkersBtn.MODE_WORKER), KeyboardButton(WorkersBtn.MODE_EMPLOYER)],
        [KeyboardButton(MenuButton.CANCEL)],
    ], resize_keyboard=True)

    await update.message.reply_text(
        "🚀 <b>Запуск мониторинга ПВЗ</b>\n\n"
        "Выберите режим:",
        reply_markup=keyboard,
        parse_mode="HTML",
    )

    return WorkersState.SELECT_MODE


async def receive_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен режим - переходим к выбору города"""
    text = update.message.text.strip()
    mode = "worker" if text == WorkersBtn.MODE_WORKER else "employer"
    context.user_data["workers_mode"] = mode

    mode_name = "Работники" if mode == "worker" else "Работодатели"

    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton(WorkersBtn.CITY_MSK), KeyboardButton(WorkersBtn.CITY_SPB)],
        [KeyboardButton(WorkersBtn.CITY_ALL)],
        [KeyboardButton(MenuButton.CANCEL)],
    ], resize_keyboard=True)

    await update.message.reply_text(
        f"👷 <b>Режим: {mode_name}</b>\n\n"
        "🏙 <b>Выберите город для мониторинга:</b>\n\n"
        "⚠️ При выборе конкретного города поиск происходит только в чатах, "
        "где указан этот город в названии топика. "
        "Общие чаты без указания города исключаются из поиска.",
        reply_markup=keyboard,
        parse_mode="HTML",
    )

    return WorkersState.INPUT_CITY


async def receive_city(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен город - переходим к датам"""
    text = update.message.text.strip()

    city_map = {
        WorkersBtn.CITY_MSK: "МСК",
        WorkersBtn.CITY_SPB: "СПБ",
        WorkersBtn.CITY_ALL: "ALL",
    }

    if text not in city_map:
        await update.message.reply_text("❌ Пожалуйста, выберите город из предложенных вариантов.")
        return WorkersState.INPUT_CITY

    context.user_data["workers_city"] = city_map[text]

    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton(MenuButton.CANCEL)],
    ], resize_keyboard=True)

    await update.message.reply_text(
        "📅 Фильтр по датам\n\n"
        "Фильтр по датам нужен для определения предполагаемой даты выхода сотрудника на работу\n\n"
        "Введите дату начала (формат: ДД.ММ.ГГГГ):\n"
        "<code>31.12.2026</code>",
        reply_markup=keyboard,
        parse_mode="HTML",
    )

    return WorkersState.INPUT_DATE_FROM


async def receive_date_from(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получена дата начала"""
    date_str = update.message.text.strip()

    # Валидация даты
    valid, dt, error = Validators.validate_date(date_str)
    if not valid:
        await update.message.reply_text(error)
        return WorkersState.INPUT_DATE_FROM

    context.user_data["workers_date_from"] = dt.strftime("%Y-%m-%d")

    await update.message.reply_text(
        "📅 Введите дату окончания (формат: ДД.ММ.ГГГГ):",
    )

    return WorkersState.INPUT_DATE_TO


async def receive_date_to(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получена дата окончания - переходим к ценам"""
    date_str = update.message.text.strip()
    date_from = context.user_data.get("workers_date_from")

    # Валидация даты
    valid, dt, error = Validators.validate_date(date_str)
    if not valid:
        await update.message.reply_text(error)
        return WorkersState.INPUT_DATE_TO

    # Валидация диапазона (date_from хранится в ISO YYYY-MM-DD)
    if date_from:
        dt_from = datetime.strptime(date_from, "%Y-%m-%d")
        if dt_from > dt:
            await update.message.reply_text("❌ Дата начала не может быть позже даты окончания")
            return WorkersState.INPUT_DATE_TO
        if (dt - dt_from).days > 365:
            await update.message.reply_text("❌ Диапазон дат слишком большой (максимум 365 дней)")
            return WorkersState.INPUT_DATE_TO

    context.user_data["workers_date_to"] = dt.strftime("%Y-%m-%d")

    return await ask_prices(update, context)





async def ask_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запросить фильтр по ценам"""
    text = (
        "💰 Фильтр по ставке\n\n"
        "Введите минимальную ставку (руб/смена):\n"
        "<code>2000</code>"
    )

    await update.message.reply_text(text=text, parse_mode="HTML")

    return WorkersState.INPUT_MIN_PRICE


async def receive_min_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получена минимальная цена"""
    price_str = update.message.text.strip()

    # Валидация цены
    valid, price, error = Validators.validate_price(price_str, allow_zero=True)
    if not valid:
        await update.message.reply_text(error)
        return WorkersState.INPUT_MIN_PRICE

    context.user_data["workers_min_price"] = price

    await update.message.reply_text(
        "💰 Введите максимальную ставку (руб/смена):",
    )

    return WorkersState.INPUT_MAX_PRICE


async def receive_max_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получена максимальная цена - показываем подтверждение"""
    price_str = update.message.text.strip()
    min_price = context.user_data.get("workers_min_price", 0)

    # Валидация цены
    valid, price, error = Validators.validate_price(price_str, allow_zero=True)
    if not valid:
        await update.message.reply_text(error)
        return WorkersState.INPUT_MAX_PRICE

    # Валидация диапазона
    valid_range, warning = Validators.validate_price_range(min_price, price)
    if not valid_range:
        await update.message.reply_text(warning)
        return WorkersState.INPUT_MAX_PRICE

    context.user_data["workers_max_price"] = price

    # Показать предупреждение если есть
    if warning and warning.startswith("⚠️"):
        await update.message.reply_text(warning)

    return await show_confirmation(update, context)





async def show_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать подтверждение запуска"""
    mode = context.user_data.get("workers_mode")
    date_from = context.user_data.get("workers_date_from")
    date_to = context.user_data.get("workers_date_to")
    min_price = context.user_data.get("workers_min_price")
    max_price = context.user_data.get("workers_max_price")
    city = context.user_data.get("workers_city", "ALL")

    mode_name = "Работники" if mode == "worker" else "Работодатели"

    city_labels = {"МСК": "🏙 Москва", "СПБ": "🌊 Санкт-Петербург", "ALL": "🌍 Все источники"}
    city_str = city_labels.get(city, "🌍 Все источники")

    filters_text = []
    filters_text.append(f"🏙 Город: {city_str}")
    if date_from and date_to:
        from datetime import datetime as _dt
        df_display = _dt.strptime(date_from, "%Y-%m-%d").strftime("%d.%m.%Y")
        dt_display = _dt.strptime(date_to, "%Y-%m-%d").strftime("%d.%m.%Y")
        filters_text.append(f"📅 Даты: {df_display} — {dt_display}")
    if min_price or max_price:
        price_range = f"{min_price or 0} — {max_price or '∞'}"
        filters_text.append(f"💰 Цена: {price_range}")

    filters_str = "\n".join(filters_text) if filters_text else "Без фильтров"

    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton(WorkersBtn.CONFIRM), KeyboardButton(MenuButton.CANCEL)],
    ], resize_keyboard=True)

    text = (
        "📋 <b>Подтверждение запуска</b>\n\n"
        f"<b>Режим:</b> {mode_name}\n\n"
        f"<b>Фильтры:</b>\n{filters_str}\n\n"
        "Запустить мониторинг?"
    )

    await update.message.reply_text(
        text=text, reply_markup=keyboard, parse_mode="HTML"
    )

    return WorkersState.CONFIRM


async def confirm_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Подтверждение - запуск мониторинга"""
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]
    workers_api: WorkersAPI = context.bot_data["workers_api"]

    # Проверка: у пользователя уже есть запущенная задача мониторинга ПВЗ?
    workers_tasks = await db.get_user_tasks(user_id, service="workers")
    running = [t for t in workers_tasks if t.status == "running"]
    if running:
        task = running[0]
        await update.message.reply_text(
            "⚠️ <b>Нельзя запустить</b>\n\n"
            f"У вас уже запущена задача: <b>мониторинг ПВЗ</b>\n"
            f"Task ID: <code>{task.task_id[:8]}...</code>\n\n"
            "Остановите текущую задачу перед запуском новой.",
            parse_mode="HTML",
        )
        await show_main_menu(update, context)
        return ConversationHandler.END

    # Получить пути к сессиям (в контексте workers-service контейнера)
    # PurserHub: shared_sessions -> /app/data/sessions
    # Workers:   shared_sessions -> /app/sessions
    session_path = f"/app/sessions/{user_id}_parser"
    blacklist_session_path = f"/app/sessions/{user_id}_blacklist"

    # Получить глобальные чаты из БД
    chats = await db.get_global_chats('pvz_monitoring_chats')
    if not chats:
        await update.message.reply_text(
            "❌ Чаты для мониторинга не настроены.\n\n"
            "Обратитесь к администратору для настройки глобальных чатов ПВЗ."
        )
        return ConversationHandler.END

    # Подготовить параметры
    mode = context.user_data.get("workers_mode")
    date_from = context.user_data.get("workers_date_from")
    date_to = context.user_data.get("workers_date_to")
    min_price = context.user_data.get("workers_min_price")
    max_price = context.user_data.get("workers_max_price")
    city = context.user_data.get("workers_city", "ALL")

    monitoring_filters = {
        "date_from": date_from,
        "date_to": date_to,
        "shk_filter": "любое",
        "city_filter": city,
    }
    if (v := context.user_data.get("workers_min_price")) is not None:
        monitoring_filters["min_price"] = v
    if (v := context.user_data.get("workers_max_price")) is not None:
        monitoring_filters["max_price"] = v

    try:
        # Запустить мониторинг (уведомления через основной PurserHub бот)
        result = await workers_api.start_monitoring(
            user_id=user_id,
            mode=mode,
            chats=chats,
            filters=monitoring_filters,
            session_path=session_path,
            blacklist_session_path=blacklist_session_path,
            notification_chat_id=user_id,
            parse_history_days=3,
        )

        task_id = result["task_id"]

        # Сохранить задачу в БД
        task = ActiveTask(
            user_id=user_id,
            task_id=task_id,
            service="workers",
            task_type="monitoring",
            status="running",
            created_at=datetime.now(timezone.utc),
        )
        await db.add_task(task)

        await update.message.reply_text(
            f"✅ <b>Мониторинг ПВЗ запущен!</b>\n\n"
            f"Task ID: <code>{task_id}</code>\n\n"
            f"Уведомления о подходящих вакансиях будут приходить в этот чат от бота PurserHub.",
            parse_mode="HTML",
        )
        await show_main_menu(update, context)

        logger.info(f"Мониторинг запущен: user={user_id}, task={task_id}")

    except HTTPStatusError as e:
        detail = e.response.json().get("detail", "").lower()
        is_auth_error = any(kw in detail for kw in ["authkeyinvalid", "unauthorized", "not authorized"])
        if is_auth_error:
            logger.warning(f"Обнаружен обрыв авторизации для user {user_id}")
            await db.update_auth_status(user_id, "parser", False)
            context.user_data.clear()
            await update.message.reply_text(
                "⚠️ <b>Авторизация оборвана</b>\n\n"
                "Произошла ошибка со стороны Telegram.\n"
                "Пожалуйста, авторизуйтесь заново через меню \"👤 Мой аккаунт\".",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(f"❌ Ошибка запуска мониторинга:\n\n{str(e)}")
        await show_main_menu(update, context)
    except Exception as e:
        logger.exception(f"Ошибка запуска мониторинга для user {user_id}")
        is_auth_error = any(kw in str(e).lower() for kw in ["authkeyinvalid", "unauthorized"])
        if is_auth_error:
            logger.warning(f"Обнаружен обрыв авторизации для user {user_id}")
            await db.update_auth_status(user_id, "parser", False)
            context.user_data.clear()
            await update.message.reply_text(
                "⚠️ <b>Авторизация оборвана</b>\n\n"
                "Произошла ошибка со стороны Telegram.\n"
                "Пожалуйста, авторизуйтесь заново через меню \"👤 Мой аккаунт\".",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(f"❌ Ошибка запуска мониторинга:\n\n{str(e)}")
        await show_main_menu(update, context)

    return ConversationHandler.END


async def show_my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать активные задачи пользователя"""
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    tasks = await db.get_user_tasks(user_id, service="workers")

    if not tasks:
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=WorkersCB.WORKERS_MENU)]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = (
            "📋 <b>Мои задачи поиска сотрудников</b>\n\n"
            "У вас нет активных задач."
        )

        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await update.message.reply_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
        return

    keyboard = []
    for task in tasks[:10]:  # Показываем последние 10
        status_emoji = "🟢" if task.status == "running" else "⭕"
        keyboard.append([
            InlineKeyboardButton(
                f"{status_emoji} {task.task_id[:8]}...",
                callback_data=f"{WorkersCB.VIEW_TASK}{task.task_id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("⛔ Завершить активную задачу", callback_data=WorkersCB.STOP_ALL_TASKS)])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=WorkersCB.WORKERS_MENU)])
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"📋 <b>Мои задачи поиска сотрудников</b> ({len(tasks)})\n\n"
        "Выберите задачу для просмотра:"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text=text, reply_markup=reply_markup, parse_mode="HTML")


async def view_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просмотр детальной информации о задаче"""
    query = update.callback_query
    task_id = query.data.replace(WorkersCB.VIEW_TASK, "")

    workers_api: WorkersAPI = context.bot_data["workers_api"]

    await query.answer()  # всегда сначала — убирает индикатор загрузки кнопки

    try:
        status = await workers_api.get_status(task_id)

        stats = status.get("stats", {})
        total_scanned = stats.get("total_messages_scanned", 0)
        found = stats.get("items_found", 0)
        sent = stats.get("notifications_sent", 0)

        keyboard = [
            [InlineKeyboardButton("🔄 Обновить", callback_data=f"{WorkersCB.VIEW_TASK}{task_id}")],
            [InlineKeyboardButton("⛔ Остановить", callback_data=f"{WorkersCB.STOP_TASK}{task_id}")],
            [InlineKeyboardButton("🔙 Назад", callback_data=WorkersCB.MY_TASKS)],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        STATUS_MAP = {
            "running":    "✅ Активный мониторинг",
            "stopped":    "⏹ Остановлен",
            "paused":     "⏸ Приостановлен",
            "auth_error": "🔴 Сессия аннулирована — авторизуйтесь заново",
            "failed":     "❌ Ошибка сервиса",
        }
        raw_status = status.get("status", "unknown")
        display_status = STATUS_MAP.get(raw_status, f"❓ {raw_status}")

        await query.edit_message_text(
            f"📊 <b>Статус задачи</b>\n\n"
            f"<b>Task ID:</b> <code>{task_id}</code>\n"
            f"<b>Статус:</b> {display_status}\n"
            f"<b>Режим:</b> {status['mode']}\n\n"
            f"<b>Статистика:</b>\n"
            f"• Просканировано сообщений: {total_scanned}\n"
            f"• Найдено объявлений: {found}\n"
            f"• Отправлено уведомлений: {sent}",
            reply_markup=reply_markup,
            parse_mode="HTML",
        )

    except BadRequest as e:
        if "Message is not modified" not in str(e):
            logger.exception(f"Ошибка редактирования сообщения для задачи {task_id}")

    except Exception as e:
        logger.exception(f"Ошибка получения статуса задачи {task_id}")

        keyboard = [
            [InlineKeyboardButton("🗑 Принудительно завершить", callback_data=f"{WorkersCB.FORCE_CLOSE_TASK}{task_id}")],
            [InlineKeyboardButton("🔙 Назад", callback_data=WorkersCB.MY_TASKS)],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            f"❌ <b>Ошибка получения статуса</b>\n\n"
            f"<b>Task ID:</b> <code>{task_id}</code>\n\n"
            f"Задача недоступна на сервере (возможно, сервис был перезапущен).\n"
            f"Вы можете принудительно завершить задачу.",
            reply_markup=reply_markup,
            parse_mode="HTML",
        )


async def stop_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Остановка задачи"""
    query = update.callback_query
    task_id = query.data.replace(WorkersCB.STOP_TASK, "")

    workers_api: WorkersAPI = context.bot_data["workers_api"]
    db: DatabaseService = context.bot_data["db"]

    try:
        await workers_api.stop_monitoring(task_id)
        await db.delete_task(task_id)

        await query.answer("✅ Задача остановлена")
        await show_my_tasks(update, context)

    except Exception as e:
        logger.exception("Ошибка остановки задачи")
        # НЕ удаляем из bot.db при ошибке — задача может продолжать работать в сервисе
        # Для принудительной очистки используется force_close_task
        await query.answer("⚠️ Не удалось остановить задачу в сервисе. Попробуйте ещё раз.")
        await show_my_tasks(update, context)


async def force_close_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принудительное завершение задачи (только в локальной БД)"""
    query = update.callback_query
    task_id = query.data.replace(WorkersCB.FORCE_CLOSE_TASK, "")

    db: DatabaseService = context.bot_data["db"]
    workers_api: WorkersAPI = context.bot_data["workers_api"]

    # Пробуем остановить на сервере (игнорируем ошибки)
    try:
        await workers_api.stop_monitoring(task_id)
    except Exception:
        logger.warning(f"Не удалось остановить задачу {task_id} на сервере (игнорируется)")

    # Завершаем в локальной БД
    await db.delete_task(task_id)

    await query.answer("✅ Задача принудительно завершена")
    await show_my_tasks(update, context)


async def stop_all_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершить все задачи пользователя"""
    query = update.callback_query
    user_id = update.effective_user.id

    db: DatabaseService = context.bot_data["db"]
    workers_api: WorkersAPI = context.bot_data["workers_api"]

    tasks = await db.get_user_tasks(user_id, service="workers")

    stopped_count = 0
    for task in tasks:
        if task.status == "running":
            # Пробуем остановить на сервере (игнорируем ошибки)
            try:
                await workers_api.stop_monitoring(task.task_id)
            except Exception:
                logger.warning(f"Не удалось остановить задачу {task.task_id} на сервере (игнорируется)")

            # Завершаем в локальной БД
            await db.delete_task(task.task_id)
            stopped_count += 1

    await query.answer(f"✅ Завершено задач: {stopped_count}")
    await show_my_tasks(update, context)


async def _notification_blacklist_task(
    bot: Bot,
    chat_id: int,
    user_id: int,
    item_id: int,
    workers_api: WorkersAPI,
    bot_data: dict,
):
    """Фоновая задача проверки в ЧС из уведомления — выполняется без блокировки бота"""
    try:
        result = await workers_api.check_blacklist_by_item(item_id)

        check_result = result.get("result", {})
        if check_result.get("found"):
            parts = ["⚠️ НАЙДЕН В ЧЕРНОМ СПИСКЕ!", ""]
            extracted = check_result.get("extracted_info", {})
            if check_result.get("chat"):
                parts.append(f"💬 Чат: {check_result['chat']}")
            if extracted.get("full_name"):
                parts.append(f"📝 ФИО: {extracted['full_name']}")
            if extracted.get("username"):
                parts.append(f"🔗 Ник: {extracted['username']}")
            if extracted.get("phone"):
                parts.append(f"📞 Тел: {extracted['phone']}")
            parts.append("")
            parts.append("🔗 Сообщение в ЧС:")
            parts.append(check_result.get("message_link", ""))
            await bot.send_message(chat_id=chat_id, text="\n".join(parts), disable_web_page_preview=False)
        else:
            await bot.send_message(chat_id=chat_id, text="✅ В черном списке НЕ найден")

    except HTTPStatusError as e:
        logger.exception(f"Ошибка фоновой проверки ЧС из уведомления для item {item_id}")
        await bot.send_message(chat_id=chat_id, text=f"❌ Ошибка проверки: {e}")
    except Exception as e:
        logger.exception(f"Ошибка фоновой проверки ЧС из уведомления для item {item_id}")
        await bot.send_message(chat_id=chat_id, text=f"❌ Ошибка проверки: {e}")
    finally:
        bot_data.get("blacklist_searching", set()).discard(user_id)


async def handle_notification_blacklist_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопки 'Проверить в ЧС' из уведомления workers-service"""
    query = update.callback_query
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id
    db: DatabaseService = context.bot_data["db"]

    user = await db.get_user(user_id)
    if not user or not user.is_blacklist_authorized:
        await query.answer("Аккаунт ЧС не авторизован", show_alert=True)
        await query.message.reply_text(
            "⚫ <b>Черный список</b>\n\n"
            "❌ Для работы необходимо авторизовать аккаунт черного списка.\n\n"
            "Перейдите в «👤 Мой аккаунт» для авторизации.",
            parse_mode="HTML",
        )
        return

    # Проверяем, не идёт ли уже поиск для этого пользователя
    searching: set = context.bot_data.setdefault("blacklist_searching", set())
    if user_id in searching:
        await query.answer()
        await query.message.reply_text(
            "⏳ <b>Проверка уже выполняется</b>\n\n"
            "Одновременно по чёрному списку может проверяться только один пользователь.\n"
            "Пожалуйста, подождите завершения поиска и попробуйте снова.",
            parse_mode="HTML",
        )
        return

    item_id = int(query.data.split(":")[1])
    workers_api: WorkersAPI = context.bot_data["workers_api"]

    await query.answer("Поиск в черном списке запущен")
    searching.add(user_id)

    # Убираем кнопки с уведомления, чтобы нельзя было нажать повторно
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await query.message.reply_text(
        "🔍 <b>Поиск в черном списке запущен</b>\n\n"
        "⏳ <i>Результат придёт автоматически — можете пользоваться ботом.</i>",
        parse_mode="HTML",
    )

    asyncio.create_task(_notification_blacklist_task(
        bot=context.bot,
        chat_id=chat_id,
        user_id=user_id,
        item_id=item_id,
        workers_api=workers_api,
        bot_data=context.bot_data,
    ))


async def handle_notification_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопки 'Игнорировать' из уведомления workers-service"""
    query = update.callback_query
    await query.answer("Объявление проигнорировано")

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        logger.exception("Ошибка обработки ignore")



def register_workers_handlers(app):
    """Регистрация обработчиков мониторинга ПВЗ"""
    # Inline callback: возврат в меню из списка задач
    app.add_handler(
        CallbackQueryHandler(show_workers_menu, pattern=f"^{WorkersCB.WORKERS_MENU}$|^workers$")
    )

    # Reply-кнопка "📋 Задачи мониторинга"
    app.add_handler(MessageHandler(
        filters.Regex(f"^{WorkersBtn.MY_TASKS}$"), show_my_tasks
    ))

    # Inline callback: список задач (кнопка "Назад" из просмотра задачи)
    app.add_handler(CallbackQueryHandler(show_my_tasks, pattern=f"^{WorkersCB.MY_TASKS}$"))

    # Inline callback: просмотр и управление задачами (остаются inline)
    app.add_handler(CallbackQueryHandler(view_task, pattern=f"^{WorkersCB.VIEW_TASK}"))
    app.add_handler(CallbackQueryHandler(stop_task, pattern=f"^{WorkersCB.STOP_TASK}"))
    app.add_handler(CallbackQueryHandler(force_close_task, pattern=f"^{WorkersCB.FORCE_CLOSE_TASK}"))
    app.add_handler(CallbackQueryHandler(stop_all_tasks, pattern=f"^{WorkersCB.STOP_ALL_TASKS}$"))

    # Обработка callback-кнопок из уведомлений workers-service
    app.add_handler(CallbackQueryHandler(handle_notification_blacklist_check, pattern=r"^check_blacklist:\d+$"))
    app.add_handler(CallbackQueryHandler(handle_notification_ignore, pattern=r"^ignore:\d+$"))

    # ConversationHandler для запуска мониторинга (Reply-кнопки)
    monitoring_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(f"^{WorkersBtn.START}$"), start_monitoring_select_mode),
        ],
        states={
            WorkersState.SELECT_MODE: [
                MessageHandler(
                    filters.Regex(f"^({WorkersBtn.MODE_WORKER}|{WorkersBtn.MODE_EMPLOYER})$"),
                    receive_mode
                ),
            ],
            WorkersState.INPUT_CITY: [
                MessageHandler(
                    filters.Regex(
                        f"^({WorkersBtn.CITY_MSK}|{WorkersBtn.CITY_SPB}|{WorkersBtn.CITY_ALL})$"
                    ),
                    receive_city
                ),
            ],
            WorkersState.INPUT_DATE_FROM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, receive_date_from),
            ],
            WorkersState.INPUT_DATE_TO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, receive_date_to),
            ],
            WorkersState.INPUT_MIN_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, receive_min_price),
            ],
            WorkersState.INPUT_MAX_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, receive_max_price),
            ],
            WorkersState.CONFIRM: [
                MessageHandler(filters.Regex(f"^{WorkersBtn.CONFIRM}$"), confirm_start),
            ],
        },
        fallbacks=[
            CommandHandler("start", cancel_and_return_to_menu),
            MessageHandler(MAIN_MENU_FILTER, cancel_and_return_to_menu),
        ],
        conversation_timeout=300,
        allow_reentry=True,
    )
    app.add_handler(monitoring_conv)
