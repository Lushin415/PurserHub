"""Обработчики мониторинга ПВЗ"""
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)
from loguru import logger

from parserhub.db_service import DatabaseService
from parserhub.session_manager import SessionManager
from parserhub.api_client import WorkersAPI
from parserhub.models import ActiveTask, WorkersFilters
from parserhub.validators import Validators, AntiSpam
from parserhub.services.subscription_service import SubscriptionService
from parserhub.handlers.admin import _is_admin
from parserhub.handlers.start import cancel_and_return_to_menu, MAIN_MENU_FILTER


# Состояния для ConversationHandler
class WorkersState:
    SELECT_MODE = 1
    INPUT_DATE_FROM = 3
    INPUT_DATE_TO = 4
    INPUT_MIN_PRICE = 5
    INPUT_MAX_PRICE = 6
    INPUT_SHK_FILTER = 7
    CONFIRM = 8


# Callback data
class WorkersCB:
    WORKERS_MENU = "workers_menu"
    START_MONITORING = "start_monitoring"
    MY_TASKS = "workers_my_tasks"
    MODE_WORKER = "mode_worker"
    MODE_EMPLOYER = "mode_employer"
    SKIP_DATES = "skip_dates"
    SKIP_PRICES = "skip_prices"
    CONFIRM_START = "confirm_start"
    VIEW_TASK = "view_worker_task_"
    STOP_TASK = "stop_worker_task_"
    STOP_ALL_TASKS = "stop_all_worker_tasks"
    FORCE_CLOSE_TASK = "force_close_worker_task_"


async def show_workers_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню мониторинга ПВЗ"""
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    user = await db.get_user(user_id)

    if not user.is_parser_authorized:
        keyboard = [
            [InlineKeyboardButton("🔑 Авторизовать аккаунт", callback_data="auth_parser")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = (
            "👷 <b>Мониторинг ПВЗ</b>\n\n"
            "❌ Для работы необходимо авторизовать аккаунт парсера."
        )

        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await update.message.reply_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
        return

    keyboard = [
        [InlineKeyboardButton("🚀 Запустить мониторинг", callback_data=WorkersCB.START_MONITORING)],
        [InlineKeyboardButton("📋 Мои задачи", callback_data=WorkersCB.MY_TASKS)],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "👷 <b>Мониторинг ПВЗ</b>\n\n"
        "Мониторинг чатов с вакансиями и резюме работников ПВЗ.\n\n"
        "Выберите действие:"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text=text, reply_markup=reply_markup, parse_mode="HTML")


async def start_monitoring_select_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выбор режима: работники или работодатели"""
    query = update.callback_query
    await query.answer()

    # Проверка подписки
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    if not await _is_admin(user_id, db):
        sub_service: SubscriptionService = context.bot_data["subscription"]
        if not await sub_service.has_active(user_id):
            from parserhub.handlers.subscription import subscription_keyboard
            await query.edit_message_text(
                "🔒 <b>Требуется подписка</b>\n\n"
                "Для запуска мониторинга ПВЗ необходима активная подписка.",
                reply_markup=subscription_keyboard(),
                parse_mode="HTML",
            )
            return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("👷 Работники", callback_data=WorkersCB.MODE_WORKER)],
        [InlineKeyboardButton("🏢 Работодатели", callback_data=WorkersCB.MODE_EMPLOYER)],
        [InlineKeyboardButton("❌ Отмена", callback_data="workers_cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "🚀 <b>Запуск мониторинга ПВЗ</b>\n\n"
        "Выберите режим:",
        reply_markup=reply_markup,
        parse_mode="HTML",
    )

    return WorkersState.SELECT_MODE


async def receive_mode(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен режим - сразу переходим к датам (чаты берутся из глобальных настроек)"""
    query = update.callback_query
    await query.answer()

    mode = "worker" if query.data == WorkersCB.MODE_WORKER else "employer"
    context.user_data["workers_mode"] = mode

    mode_name = "Работники" if mode == "worker" else "Работодатели"

    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="workers_cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"👷 <b>Режим: {mode_name}</b>\n\n"
        "📅 Фильтр по датам\n\n"
        "Введите дату начала (формат: YYYY-MM-DD):\n"
        "<code>2026-12-31</code>",
        reply_markup=reply_markup,
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

    context.user_data["workers_date_from"] = date_str

    # Убрали кнопку пропуска
    keyboard = [
        [InlineKeyboardButton("❌ Отмена", callback_data="workers_cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "📅 Введите дату окончания (формат: YYYY-MM-DD):",
        reply_markup=reply_markup,
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

    # Валидация диапазона
    if date_from:
        valid_range, error_range = Validators.validate_date_range(date_from, date_str)
        if not valid_range:
            await update.message.reply_text(error_range)
            return WorkersState.INPUT_DATE_TO

    context.user_data["workers_date_to"] = date_str

    return await ask_prices(update, context)





async def ask_prices(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запросить фильтр по ценам"""
    # Убрали кнопку пропуска
    keyboard = [
        [InlineKeyboardButton("❌ Отмена", callback_data="workers_cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "💰 Фильтр по ставке\n\n"
        "Введите минимальную ставку (руб/смена):\n"
        "<code>2000</code>"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )

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

    # Убрали кнопку пропуска
    keyboard = [
        [InlineKeyboardButton("❌ Отмена", callback_data="workers_cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        "💰 Введите максимальную ставку (руб/смена):",
        reply_markup=reply_markup,
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

    # Чаты берутся из глобальных настроек (задаются администратором)
    db: DatabaseService = context.bot_data["db"]
    chats = await db.get_global_chats('pvz_monitoring_chats')

    mode_name = "Работники" if mode == "worker" else "Работодатели"
    chats_str = "\n".join([f"• {chat}" for chat in chats]) if chats else "Настраиваются администратором"

    filters_text = []
    if date_from and date_to:
        filters_text.append(f"📅 Даты: {date_from} — {date_to}")
    if min_price or max_price:
        price_range = f"{min_price or 0} — {max_price or '∞'}"
        filters_text.append(f"💰 Цена: {price_range}")

    filters_str = "\n".join(filters_text) if filters_text else "Без фильтров"

    keyboard = [
        [InlineKeyboardButton("✅ Запустить", callback_data=WorkersCB.CONFIRM_START)],
        [InlineKeyboardButton("❌ Отмена", callback_data="workers_cancel")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "📋 <b>Подтверждение запуска</b>\n\n"
        f"<b>Режим:</b> {mode_name}\n\n"
        f"<b>Чаты:</b>\n{chats_str}\n\n"
        f"<b>Фильтры:</b>\n{filters_str}\n\n"
        "Запустить мониторинг?"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )

    return WorkersState.CONFIRM


async def confirm_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Подтверждение - запуск мониторинга"""
    query = update.callback_query
    await query.answer()

    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]
    session_mgr: SessionManager = context.bot_data["session_manager"]
    workers_api: WorkersAPI = context.bot_data["workers_api"]

    # Проверка: у пользователя уже есть запущенная задача?
    all_tasks = await db.get_user_tasks(user_id)
    running = [t for t in all_tasks if t.status == "running"]
    if running:
        task = running[0]
        service_name = "мониторинг ПВЗ" if task.service == "workers" else "парсинг недвижимости"
        await query.edit_message_text(
            "⚠️ <b>Нельзя запустить</b>\n\n"
            f"У вас уже запущена задача: <b>{service_name}</b>\n"
            f"Task ID: <code>{task.task_id[:8]}...</code>\n\n"
            "Остановите текущую задачу перед запуском новой.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    # Получить пути к сессиям (в контексте workers-service контейнера)
    # PurserHub: shared_sessions -> /app/data/sessions
    # Workers:   shared_sessions -> /app/sessions
    session_path = f"/app/sessions/{user_id}_parser"
    blacklist_session_path = f"/app/sessions/{user_id}_blacklist"

    # Получить глобальные чаты из БД
    chats = await db.get_global_chats('pvz_monitoring_chats')
    if not chats:
        await query.edit_message_text(
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

    filters = {
        "date_from": date_from,
        "date_to": date_to,
        "min_price": min_price,
        "max_price": max_price,
        "shk_filter": "любое",
    }

    try:
        # Запустить мониторинг (уведомления через основной PurserHub бот)
        result = await workers_api.start_monitoring(
            user_id=user_id,
            mode=mode,
            chats=chats,
            filters=filters,
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
            created_at=datetime.utcnow(),
        )
        await db.add_task(task)

        await query.edit_message_text(
            f"✅ <b>Мониторинг ПВЗ запущен!</b>\n\n"
            f"Task ID: <code>{task_id}</code>\n\n"
            f"Уведомления о подходящих вакансиях будут приходить в этот чат от бота PurserHub.",
            parse_mode="HTML",
        )

        logger.info(f"Мониторинг запущен: user={user_id}, task={task_id}")

    except Exception as e:
        error_msg = str(e).lower()
        logger.error(f"Ошибка запуска мониторинга: {e}")

        # Проверка на обрыв авторизации
        if any(keyword in error_msg for keyword in ["authkeyinvalid", "auth", "session", "unauthorized", "сессия"]):
            logger.warning(f"Обнаружен обрыв авторизации для user {user_id}")

            # Сброситьстатус авторизации в БД
            await db.update_auth_status(user_id, "parser", False)

            # Очистить данные пользователя
            context.user_data.clear()

            await query.edit_message_text(
                "⚠️ <b>Авторизация оборвана</b>\n\n"
                "Произошла ошибка со стороны Telegram.\n"
                "Пожалуйста, авторизуйтесь заново через меню \"👤 Мой аккаунт\".",
                parse_mode="HTML"
            )
        else:
            await query.edit_message_text(
                f"❌ Ошибка запуска мониторинга:\n\n{str(e)}"
            )

    return ConversationHandler.END


async def show_my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать активные задачи пользователя"""
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    tasks = await db.get_user_tasks(user_id, service="workers")

    if not tasks:
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=WorkersCB.WORKERS_MENU)]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "📋 <b>Мои задачи</b>\n\n"
            "У вас нет активных задач.",
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
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

    keyboard.append([InlineKeyboardButton("⛔ Завершить все задачи", callback_data=WorkersCB.STOP_ALL_TASKS)])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=WorkersCB.WORKERS_MENU)])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        f"📋 <b>Мои задачи</b> ({len(tasks)})\n\n"
        "Выберите задачу для просмотра:",
        reply_markup=reply_markup,
        parse_mode="HTML",
    )


async def view_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просмотр детальной информации о задаче"""
    query = update.callback_query
    task_id = query.data.replace(WorkersCB.VIEW_TASK, "")

    workers_api: WorkersAPI = context.bot_data["workers_api"]

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

        await query.answer()
        await query.edit_message_text(
            f"📊 <b>Статус задачи</b>\n\n"
            f"<b>Task ID:</b> <code>{task_id}</code>\n"
            f"<b>Статус:</b> {status['status']}\n"
            f"<b>Режим:</b> {status['mode']}\n\n"
            f"<b>Статистика:</b>\n"
            f"• Просканировано сообщений: {total_scanned}\n"
            f"• Найдено объявлений: {found}\n"
            f"• Отправлено уведомлений: {sent}",
            reply_markup=reply_markup,
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"Ошибка получения статуса задачи {task_id}: {e}")

        keyboard = [
            [InlineKeyboardButton("🗑 Принудительно завершить", callback_data=f"{WorkersCB.FORCE_CLOSE_TASK}{task_id}")],
            [InlineKeyboardButton("🔙 Назад", callback_data=WorkersCB.MY_TASKS)],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.answer()
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
        result = await workers_api.stop_monitoring(task_id)
        await db.delete_task(task_id)

        await query.answer("✅ Задача остановлена")
        await show_my_tasks(update, context)

    except Exception as e:
        logger.error(f"Ошибка остановки задачи: {e}")
        # Если сервис недоступен — принудительно завершаем в локальной БД
        await db.delete_task(task_id)
        await query.answer("⚠️ Задача завершена локально (сервис недоступен)")
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
        pass  # Сервис мог перезапуститься, задача уже не существует

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
                pass  # Сервис мог перезапуститься

            # Завершаем в локальной БД
            await db.delete_task(task.task_id)
            stopped_count += 1

    await query.answer(f"✅ Завершено задач: {stopped_count}")
    await show_my_tasks(update, context)


async def handle_notification_blacklist_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопки 'Проверить в ЧС' из уведомления workers-service"""
    query = update.callback_query
    await query.answer("Поиск в черном списке...")

    try:
        item_id = int(query.data.split(":")[1])
        workers_api: WorkersAPI = context.bot_data["workers_api"]

        search_msg = await query.message.reply_text("🔍 Поиск в черном списке...")

        result = await workers_api.check_blacklist_by_item(item_id)

        await search_msg.delete()
        await query.edit_message_reply_markup(reply_markup=None)

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
            await query.message.reply_text("\n".join(parts), disable_web_page_preview=False)
        else:
            await query.message.reply_text("✅ В черном списке НЕ найден")

    except Exception as e:
        logger.error(f"Ошибка проверки ЧС из уведомления: {e}")
        await query.message.reply_text(f"❌ Ошибка проверки: {e}")


async def handle_notification_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка кнопки 'Игнорировать' из уведомления workers-service"""
    query = update.callback_query
    await query.answer("Объявление проигнорировано")

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        logger.error(f"Ошибка обработки ignore: {e}")


async def cancel_workers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена настройки мониторинга"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Настройка мониторинга отменена.")
    return ConversationHandler.END


def register_workers_handlers(app):
    """Регистрация обработчиков мониторинга ПВЗ"""
    # Меню
    app.add_handler(
        CallbackQueryHandler(show_workers_menu, pattern=f"^{WorkersCB.WORKERS_MENU}$|^workers$")
    )

    # Список задач
    app.add_handler(CallbackQueryHandler(show_my_tasks, pattern=f"^{WorkersCB.MY_TASKS}$"))

    # Просмотр и управление задачами
    app.add_handler(CallbackQueryHandler(view_task, pattern=f"^{WorkersCB.VIEW_TASK}"))
    app.add_handler(CallbackQueryHandler(stop_task, pattern=f"^{WorkersCB.STOP_TASK}"))
    app.add_handler(CallbackQueryHandler(force_close_task, pattern=f"^{WorkersCB.FORCE_CLOSE_TASK}"))
    app.add_handler(CallbackQueryHandler(stop_all_tasks, pattern=f"^{WorkersCB.STOP_ALL_TASKS}$"))

    # Обработка callback-кнопок из уведомлений workers-service
    app.add_handler(CallbackQueryHandler(handle_notification_blacklist_check, pattern=r"^check_blacklist:\d+$"))
    app.add_handler(CallbackQueryHandler(handle_notification_ignore, pattern=r"^ignore:\d+$"))

    # ConversationHandler для запуска мониторинга
    monitoring_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_monitoring_select_mode, pattern=f"^{WorkersCB.START_MONITORING}$")
        ],
        states={
            WorkersState.SELECT_MODE: [
                CallbackQueryHandler(receive_mode, pattern=f"^{WorkersCB.MODE_WORKER}$|^{WorkersCB.MODE_EMPLOYER}$")
            ],
            WorkersState.INPUT_DATE_FROM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_date_from),
                # Удален handler skip_dates
            ],
            WorkersState.INPUT_DATE_TO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_date_to),
                # Удален handler skip_dates
            ],
            WorkersState.INPUT_MIN_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_min_price),
                # Удален handler skip_prices
            ],
            WorkersState.INPUT_MAX_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_max_price),
                # Удален handler skip_prices
            ],
            WorkersState.CONFIRM: [
                CallbackQueryHandler(confirm_start, pattern=f"^{WorkersCB.CONFIRM_START}$")
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_workers, pattern="^workers_cancel$"),
            CommandHandler("start", cancel_and_return_to_menu),
            MessageHandler(MAIN_MENU_FILTER, cancel_and_return_to_menu),
        ],
    )
    app.add_handler(monitoring_conv)
