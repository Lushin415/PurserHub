"""Обработчики парсинга недвижимости (Avito/Cian)"""
import re
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
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
from parserhub.api_client import RealtyAPI
from parserhub.models import ActiveTask
from parserhub.validators import Validators, AntiSpam
from parserhub.services.subscription_service import SubscriptionService
from parserhub.handlers.admin import _is_admin
from parserhub.handlers.start import cancel_and_return_to_menu, MAIN_MENU_FILTER, MenuButton, show_main_menu
from parserhub.config import config


# Состояния для ConversationHandler
class RealtyState:
    SELECT_SOURCE = 1
    INPUT_URL = 2
    CONFIRM = 3


# Reply-кнопки подменю
class RealtyBtn:
    AVITO = "🟦 Avito"
    CIAN = "🟩 Cian"
    BOTH = "🔀 Avito + Cian"
    MY_TASKS = "📋 Задачи парсинга"
    CONFIRM = "✅ Запустить"


# Callback data (только для inline: задачи)
class RealtyCB:
    REALTY_MENU = "realty_menu"
    VIEW_TASK = "view_realty_task_"
    STOP_TASK = "stop_realty_task_"
    STOP_ALL_TASKS = "stop_all_realty_tasks"
    FORCE_CLOSE_TASK = "force_close_realty_task_"


async def show_realty_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню парсинга недвижимости"""
    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton(RealtyBtn.AVITO), KeyboardButton(RealtyBtn.CIAN)],
        [KeyboardButton(RealtyBtn.BOTH), KeyboardButton(RealtyBtn.MY_TASKS)],
        [KeyboardButton(MenuButton.BACK)],
    ], resize_keyboard=True)

    text = (
        "🏠 <b>Парсинг недвижимости</b>\n\n"
        "Выберите источник для парсинга:"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.message.reply_text(text=text, reply_markup=keyboard, parse_mode="HTML")


async def start_parsing_select_source(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выбор источника парсинга"""
    text_btn = update.message.text.strip()

    # Проверка подписки
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    if not await _is_admin(user_id, db):
        sub_service: SubscriptionService = context.bot_data["subscription"]
        if not await sub_service.has_active(user_id):
            await update.message.reply_text(
                "🔒 <b>Требуется подписка</b>\n\n"
                "Для запуска парсинга недвижимости необходима активная подписка.\n"
                "Перейдите в «💳 Подписка» для оформления.",
                parse_mode="HTML",
            )
            return ConversationHandler.END

    if text_btn == RealtyBtn.AVITO:
        context.user_data["realty_source"] = "avito"
        source_name = "Avito"
    elif text_btn == RealtyBtn.CIAN:
        context.user_data["realty_source"] = "cian"
        source_name = "Cian"
    else:
        context.user_data["realty_source"] = "both"
        source_name = "Avito и Cian"

    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton(MenuButton.CANCEL)],
    ], resize_keyboard=True)

    if context.user_data["realty_source"] == "both":
        text = (
            f"🏠 <b>Парсинг: {source_name}</b>\n\n"
            "⚠️ <b>ОБЯЗАТЕЛЬНО УСТАНОВИТЕ СОРТИРОВКУ ПО ДАТЕ</b>\n\n"
            "Введите ссылку на Avito:\n"
            "<code>https://www.avito.ru/moskva/...</code>"
        )
    else:
        text = (
            f"🏠 <b>Парсинг: {source_name}</b>\n\n"
            "⚠️ <b>ОБЯЗАТЕЛЬНО УСТАНОВИТЕ СОРТИРОВКУ ПО ДАТЕ</b>\n\n"
            "Введите ссылку для парсинга:\n"
            f"<code>https://{source_name.lower()}.ru/...</code>"
        )

    await update.message.reply_text(
        text=text, reply_markup=keyboard, parse_mode="HTML"
    )

    return RealtyState.INPUT_URL


async def receive_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получена URL - запрашиваем следующую или кол-во страниц"""
    url = update.message.text.strip()
    source = context.user_data.get("realty_source")

    # Валидация URL для Avito
    if source == "avito":
        valid, error = Validators.validate_url(url, ["avito.ru"])
        if not valid:
            await update.message.reply_text(error)
            return RealtyState.INPUT_URL
        context.user_data["realty_avito_url"] = url
        return await show_confirmation(update, context)

    # Валидация URL для Cian
    if source == "cian":
        valid, error = Validators.validate_url(url, ["cian.ru"])
        if not valid:
            await update.message.reply_text(error)
            return RealtyState.INPUT_URL
        context.user_data["realty_cian_url"] = url
        return await show_confirmation(update, context)

    # Обе ссылки (both)
    if source == "both":
        # Если обе ссылки - определяем какая это
        if "avito.ru" in url:
            valid, error = Validators.validate_url(url, ["avito.ru"])
            if not valid:
                await update.message.reply_text(error)
                return RealtyState.INPUT_URL
            context.user_data["realty_avito_url"] = url

            await update.message.reply_text(
                "✅ Avito ссылка сохранена.\n\n"
                "Теперь введите ссылку на Cian:\n"
                "<code>https://cian.ru/...</code>",
                parse_mode="HTML",
            )
            return RealtyState.INPUT_URL

        elif "cian.ru" in url:
            valid, error = Validators.validate_url(url, ["cian.ru"])
            if not valid:
                await update.message.reply_text(error)
                return RealtyState.INPUT_URL
            context.user_data["realty_cian_url"] = url
            # Переходим к подтверждению
            return await show_confirmation(update, context)
        else:
            await update.message.reply_text(
                "❌ Неверная ссылка. Должна быть с доменов: avito.ru, cian.ru"
            )
            return RealtyState.INPUT_URL


async def show_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Показать подтверждение запуска мониторинга"""
    source = context.user_data.get("realty_source")
    avito_url = context.user_data.get("realty_avito_url")
    cian_url = context.user_data.get("realty_cian_url")

    urls_text = []
    if avito_url:
        urls_text.append(f"🟦 Avito: {avito_url[:50]}...")
    if cian_url:
        urls_text.append(f"🟩 Cian: {cian_url[:50]}...")

    urls_str = "\n".join(urls_text)

    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton(RealtyBtn.CONFIRM), KeyboardButton(MenuButton.CANCEL)],
    ], resize_keyboard=True)

    text = (
        "📋 <b>Подтверждение запуска мониторинга</b>\n\n"
        f"<b>Ссылки:</b>\n{urls_str}\n\n"
        "Запустить мониторинг?"
    )

    await update.message.reply_text(
        text=text, reply_markup=keyboard, parse_mode="HTML"
    )

    return RealtyState.CONFIRM


async def confirm_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Подтверждение - запуск парсинга"""
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]
    realty_api: RealtyAPI = context.bot_data["realty_api"]

    # Проверка: у пользователя уже есть запущенная задача?
    all_tasks = await db.get_user_tasks(user_id)
    running = [t for t in all_tasks if t.status == "running"]
    if running:
        task = running[0]
        service_name = "мониторинг ПВЗ" if task.service == "workers" else "парсинг недвижимости"
        await update.message.reply_text(
            "⚠️ <b>Нельзя запустить</b>\n\n"
            f"У вас уже запущена задача: <b>{service_name}</b>\n"
            f"Task ID: <code>{task.task_id[:8]}...</code>\n\n"
            "Остановите текущую задачу перед запуском новой.",
            parse_mode="HTML",
        )
        await show_main_menu(update, context)
        return ConversationHandler.END

    # Получить параметры
    avito_url = context.user_data.get("realty_avito_url")
    cian_url = context.user_data.get("realty_cian_url")

    try:
        # Запустить мониторинг (уведомления через основной PurserHub бот)
        result = await realty_api.start_parsing(
            user_id=user_id,
            avito_url=avito_url,
            cian_url=cian_url,
            notification_bot_token=config.BOT_TOKEN,
            notification_chat_id=user_id,
        )

        task_id = result["task_id"]

        # Определить тип задачи
        if avito_url and cian_url:
            task_type = "avito_cian"
        elif avito_url:
            task_type = "avito"
        else:
            task_type = "cian"

        # Сохранить задачу в БД
        task = ActiveTask(
            user_id=user_id,
            task_id=task_id,
            service="realty",
            task_type=task_type,
            status="running",
            created_at=datetime.utcnow(),
        )
        await db.add_task(task)

        await update.message.reply_text(
            f"✅ <b>Мониторинг недвижимости запущен!</b>\n\n"
            f"Task ID: <code>{task_id}</code>\n\n"
            f"Уведомления о новых объявлениях будут приходить в этот чат от бота PurserHub.",
            parse_mode="HTML",
        )
        await show_main_menu(update, context)

        logger.info(f"Парсинг запущен: user={user_id}, task={task_id}, type={task_type}")

    except Exception as e:
        logger.error(f"Ошибка запуска парсинга: {e}")

        # Определяем: ошибка авторизации или другая?
        is_auth_error = False
        try:
            detail = e.response.json().get("detail", "").lower()
            is_auth_error = any(kw in detail for kw in ["authkeyinvalid", "unauthorized", "not authorized"])
        except Exception:
            is_auth_error = any(kw in str(e).lower() for kw in ["authkeyinvalid", "unauthorized"])

        if is_auth_error:
            logger.warning(f"Обнаружен обрыв авторизации для user {user_id}")
            context.user_data.clear()

            await update.message.reply_text(
                "⚠️ <b>Авторизация оборвана</b>\n\n"
                "Произошла ошибка со стороны Telegram.\n"
                "Пожалуйста, авторизуйтесь заново через меню \"👤 Мой аккаунт\".",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                f"❌ Ошибка запуска парсинга:\n\n{str(e)}"
            )
        await show_main_menu(update, context)

    return ConversationHandler.END


async def show_my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать активные задачи пользователя"""
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    tasks = await db.get_user_tasks(user_id, service="realty")

    if not tasks:
        keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=RealtyCB.REALTY_MENU)]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = (
            "📋 <b>Мои задачи</b>\n\n"
            "У вас нет активных задач парсинга."
        )

        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await update.message.reply_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
        return

    keyboard = []
    for task in tasks[:10]:
        type_emoji = {
            "avito": "🟦",
            "cian": "🟩",
            "avito_cian": "🔀",
        }.get(task.task_type, "📄")

        status_emoji = "🟢" if task.status == "running" else "⭕"

        keyboard.append([
            InlineKeyboardButton(
                f"{type_emoji} {status_emoji} {task.task_id[:8]}...",
                callback_data=f"{RealtyCB.VIEW_TASK}{task.task_id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("⛔ Завершить все задачи", callback_data=RealtyCB.STOP_ALL_TASKS)])
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=RealtyCB.REALTY_MENU)])
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        f"📋 <b>Мои задачи парсинга</b> ({len(tasks)})\n\n"
        "Выберите задачу для просмотра:"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text=text, reply_markup=reply_markup, parse_mode="HTML")


async def view_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Просмотр детальной информации о задаче парсинга"""
    query = update.callback_query
    task_id = query.data.replace(RealtyCB.VIEW_TASK, "")

    realty_api: RealtyAPI = context.bot_data["realty_api"]

    try:
        status = await realty_api.get_status(task_id)
        task_status = status.get("status", "unknown")

        keyboard = [
            [InlineKeyboardButton("🔄 Обновить", callback_data=f"{RealtyCB.VIEW_TASK}{task_id}")],
            [InlineKeyboardButton("⛔ Остановить", callback_data=f"{RealtyCB.STOP_TASK}{task_id}")],
            [InlineKeyboardButton("🔙 Назад", callback_data=RealtyCB.MY_TASKS)],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Проверка на режим мониторинга
        if task_status == "monitoring":
            progress = status.get("progress", {})
            found_ads = progress.get("found_ads", 0)
            filtered_ads = progress.get("filtered_ads", 0)
            last_check = progress.get("last_check", "Не выполнялась")

            await query.answer()
            await query.edit_message_text(
                f"📡 <b>Статус мониторинга</b>\n\n"
                f"<b>Task ID:</b> <code>{task_id}</code>\n"
                f"<b>Статус:</b> Активный мониторинг\n\n"
                f"<b>Статистика:</b>\n"
                f"• Найдено объявлений: {found_ads}\n"
                f"• Отфильтровано: {filtered_ads}\n"
                f"• Последняя проверка: {last_check}",
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
        else:
            # Обычный режим парсинга
            progress = status.get("progress", {})
            total_pages = progress.get("total_pages", 0)
            current_page = progress.get("current_page", 0)
            found_ads = progress.get("found_ads", 0)
            filtered_ads = progress.get("filtered_ads", 0)

            await query.answer()
            await query.edit_message_text(
                f"📊 <b>Статус парсинга</b>\n\n"
                f"<b>Task ID:</b> <code>{task_id}</code>\n"
                f"<b>Статус:</b> {task_status}\n\n"
                f"<b>Прогресс:</b>\n"
                f"• Страниц: {current_page}/{total_pages}\n"
                f"• Найдено объявлений: {found_ads}\n"
                f"• Отфильтровано: {filtered_ads}",
                reply_markup=reply_markup,
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error(f"Ошибка получения статуса парсинга {task_id}: {e}")

        keyboard = [
            [InlineKeyboardButton("🗑 Принудительно завершить", callback_data=f"{RealtyCB.FORCE_CLOSE_TASK}{task_id}")],
            [InlineKeyboardButton("🔙 Назад", callback_data=RealtyCB.MY_TASKS)],
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
    """Остановка задачи парсинга"""
    query = update.callback_query
    task_id = query.data.replace(RealtyCB.STOP_TASK, "")

    realty_api: RealtyAPI = context.bot_data["realty_api"]
    db: DatabaseService = context.bot_data["db"]

    try:
        result = await realty_api.stop_parsing(task_id)
        await db.delete_task(task_id)

        await query.answer("✅ Парсинг остановлен")
        await show_my_tasks(update, context)

    except Exception as e:
        logger.error(f"Ошибка остановки парсинга: {e}")
        await db.delete_task(task_id)
        await query.answer("⚠️ Задача завершена локально (сервис недоступен)")
        await show_my_tasks(update, context)


async def force_close_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Принудительное завершение задачи парсинга"""
    query = update.callback_query
    task_id = query.data.replace(RealtyCB.FORCE_CLOSE_TASK, "")

    db: DatabaseService = context.bot_data["db"]
    realty_api: RealtyAPI = context.bot_data["realty_api"]

    try:
        await realty_api.stop_parsing(task_id)
    except Exception:
        pass

    await db.delete_task(task_id)

    await query.answer("✅ Задача принудительно завершена")
    await show_my_tasks(update, context)


async def stop_all_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Завершить все задачи парсинга"""
    query = update.callback_query
    user_id = update.effective_user.id

    db: DatabaseService = context.bot_data["db"]
    realty_api: RealtyAPI = context.bot_data["realty_api"]

    tasks = await db.get_user_tasks(user_id, service="realty")

    stopped_count = 0
    for task in tasks:
        if task.status in ("running", "monitoring"):
            try:
                await realty_api.stop_parsing(task.task_id)
            except Exception:
                pass

            await db.delete_task(task.task_id)
            stopped_count += 1

    await query.answer(f"✅ Завершено задач: {stopped_count}")
    await show_my_tasks(update, context)


async def cancel_realty(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена настройки парсинга"""
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Настройка парсинга отменена.")
    else:
        await update.message.reply_text("❌ Настройка парсинга отменена.")
    return ConversationHandler.END


def register_realty_handlers(app):
    """Регистрация обработчиков парсинга недвижимости"""
    # Inline callback: возврат в меню из списка задач
    app.add_handler(
        CallbackQueryHandler(show_realty_menu, pattern=f"^{RealtyCB.REALTY_MENU}$|^realty$")
    )

    # Reply-кнопка "📋 Задачи парсинга"
    app.add_handler(MessageHandler(
        filters.Regex(f"^{re.escape(RealtyBtn.MY_TASKS)}$"), show_my_tasks
    ))

    # Inline callback: просмотр и управление задачами (остаются inline)
    app.add_handler(CallbackQueryHandler(view_task, pattern=f"^{RealtyCB.VIEW_TASK}"))
    app.add_handler(CallbackQueryHandler(stop_task, pattern=f"^{RealtyCB.STOP_TASK}"))
    app.add_handler(CallbackQueryHandler(force_close_task, pattern=f"^{RealtyCB.FORCE_CLOSE_TASK}"))
    app.add_handler(CallbackQueryHandler(stop_all_tasks, pattern=f"^{RealtyCB.STOP_ALL_TASKS}$"))

    # ConversationHandler для запуска парсинга (Reply-кнопки)
    parsing_conv = ConversationHandler(
        entry_points=[
            MessageHandler(
                filters.Regex(f"^{re.escape(RealtyBtn.AVITO)}$"), start_parsing_select_source,
            ),
            MessageHandler(
                filters.Regex(f"^{re.escape(RealtyBtn.CIAN)}$"), start_parsing_select_source,
            ),
            MessageHandler(
                filters.Regex(f"^{re.escape(RealtyBtn.BOTH)}$"), start_parsing_select_source,
            ),
        ],
        states={
            RealtyState.INPUT_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, receive_url)
            ],
            RealtyState.CONFIRM: [
                MessageHandler(filters.Regex(f"^{re.escape(RealtyBtn.CONFIRM)}$"), confirm_start),
            ],
        },
        fallbacks=[
            CommandHandler("start", cancel_and_return_to_menu),
            MessageHandler(MAIN_MENU_FILTER, cancel_and_return_to_menu),
        ],
        conversation_timeout=300,
    )
    app.add_handler(parsing_conv)
