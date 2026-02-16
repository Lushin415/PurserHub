"""Обработчик команды /start и главного меню"""
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ConversationHandler
from loguru import logger

from parserhub.db_service import DatabaseService


# Текст кнопок главного меню
class MenuButton:
    """Константы для текста кнопок"""
    ACCOUNT = "👤 Мой аккаунт"
    WORKERS = "👷 Мониторинг ПВЗ"
    REALTY = "🏠 Недвижимость"
    BLACKLIST = "⚫ Черный список"
    SUBSCRIPTION = "💳 Подписка"
    SETTINGS = "⚙️ Настройки"
    ADMIN = "🔧 Админ-панель"
    BACK = "🔙 Назад"
    CANCEL = "❌ Отмена"


# Фильтр для кнопок главного меню (используется в fallbacks всех ConversationHandler)
MAIN_MENU_FILTER = filters.Regex(
    f"^({MenuButton.ACCOUNT}|{MenuButton.WORKERS}|{MenuButton.REALTY}|"
    f"{MenuButton.BLACKLIST}|{MenuButton.SUBSCRIPTION}|{MenuButton.SETTINGS}|"
    f"{MenuButton.ADMIN}|{MenuButton.BACK}|{MenuButton.CANCEL})$"
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    db: DatabaseService = context.bot_data["db"]

    # Регистрация/обновление пользователя
    await db.create_or_update_user(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    logger.info(f"Пользователь {user.id} (@{user.username}) запустил бота")

    await show_main_menu(update, context)


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать главное меню с Reply кнопками"""
    from parserhub.handlers.admin import _is_admin

    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    is_admin = await _is_admin(user_id, db)

    keyboard = [
        [KeyboardButton(MenuButton.ACCOUNT)],
        [KeyboardButton(MenuButton.WORKERS)],
        [KeyboardButton(MenuButton.REALTY)],
        [KeyboardButton(MenuButton.BLACKLIST)],
    ]

    if not is_admin:
        keyboard.append([KeyboardButton(MenuButton.SUBSCRIPTION)])

    keyboard.append([KeyboardButton(MenuButton.SETTINGS)])

    if is_admin:
        keyboard.append([KeyboardButton(MenuButton.ADMIN)])
    reply_markup = ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        one_time_keyboard=False
    )

    text = (
        "🤖 <b>ParserHub</b> — Оркестровый бот для управления парсерами\n\n"
        "Выберите нужный раздел:"
    )

    # Отправляем новое сообщение с Reply клавиатурой
    if update.message:
        await update.message.reply_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )
    elif update.callback_query:
        # Если пришло из callback (для совместимости)
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )


async def menu_button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик нажатий на кнопки главного меню"""
    text = update.message.text

    # Импорты других обработчиков (чтобы избежать циклических импортов)
    from parserhub.handlers.auth import show_account_menu
    from parserhub.handlers.workers import show_workers_menu
    from parserhub.handlers.realty import show_realty_menu
    from parserhub.handlers.blacklist import show_blacklist_menu
    from parserhub.handlers.subscription import subscription_menu
    from parserhub.handlers.settings import show_settings_menu

    # Маршрутизация по тексту кнопки
    if text == MenuButton.ACCOUNT:
        await show_account_menu(update, context)
    elif text == MenuButton.WORKERS:
        await show_workers_menu(update, context)
    elif text == MenuButton.REALTY:
        await show_realty_menu(update, context)
    elif text == MenuButton.BLACKLIST:
        await show_blacklist_menu(update, context)
    elif text == MenuButton.SUBSCRIPTION:
        await subscription_menu(update, context)
    elif text == MenuButton.SETTINGS:
        await show_settings_menu(update, context)
    elif text == MenuButton.ADMIN:
        from parserhub.handlers.admin import admin_command
        await admin_command(update, context)
    elif text == MenuButton.BACK or text == MenuButton.CANCEL:
        await show_main_menu(update, context)


async def cancel_and_return_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Отмена любой операции и возврат в главное меню.
    Используется как fallback в ConversationHandler для выхода из состояний.
    """
    logger.info(f"Пользователь {update.effective_user.id} отменил операцию")

    # Очистить данные пользователя из context
    context.user_data.clear()

    # Если есть сообщение - отправляем уведомление об отмене
    if update.message:
        await update.message.reply_text("❌ Операция отменена.")

    # Показываем главное меню
    await show_main_menu(update, context)

    return ConversationHandler.END


def register_start_handlers(app):
    """Регистрация обработчиков команды /start и главного меню"""
    app.add_handler(CommandHandler("start", start_command))
    # Обработчик для кнопок главного меню (Reply кнопки)
    app.add_handler(MessageHandler(MAIN_MENU_FILTER, menu_button_handler))
    # Обработчик для Inline-кнопки "Назад" → главное меню
    app.add_handler(CallbackQueryHandler(show_main_menu, pattern="^main_menu$"))
