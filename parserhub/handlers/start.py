"""Обработчик команды /start и главного меню"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, ConversationHandler
from loguru import logger

from parserhub.db_service import DatabaseService


# Callback data для кнопок
class CB:
    """Константы для callback_data"""
    MAIN_MENU = "main_menu"
    ACCOUNT = "account"
    WORKERS = "workers"
    REALTY = "realty"
    BLACKLIST = "blacklist"
    SUBSCRIPTION = "subscription_menu"
    SETTINGS = "settings"


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
    """Показать главное меню"""
    keyboard = [
        [InlineKeyboardButton("👤 Мой аккаунт", callback_data=CB.ACCOUNT)],
        [InlineKeyboardButton("👷 Мониторинг ПВЗ", callback_data=CB.WORKERS)],
        [InlineKeyboardButton("🏠 Парсинг недвижимости", callback_data=CB.REALTY)],
        [InlineKeyboardButton("⚫ Черный список", callback_data=CB.BLACKLIST)],
        # [InlineKeyboardButton("💳 Подписка", callback_data=CB.SUBSCRIPTION)],
        [InlineKeyboardButton("⚙️ Настройки", callback_data=CB.SETTINGS)],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "🤖 <b>ParserHub</b> — Оркестровый бот для управления парсерами\n\n"
        "Выберите нужный раздел:"
    )

    # Если это callback query - редактируем сообщение
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )
    else:
        # Если команда - отправляем новое сообщение
        await update.message.reply_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик возврата в главное меню"""
    await show_main_menu(update, context)


async def cancel_and_return_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """
    Отмена любой операции и возврат в главное меню.
    Используется как fallback в ConversationHandler для выхода из состояний.
    """
    logger.info(f"Пользователь {update.effective_user.id} отменил операцию")

    # Если есть сообщение - отправляем уведомление об отмене
    if update.message:
        await update.message.reply_text("❌ Операция отменена.")

    # Показываем главное меню
    await show_main_menu(update, context)

    return ConversationHandler.END


def register_start_handlers(app):
    """Регистрация обработчиков команды /start и главного меню"""
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("menu", start_command))  # /menu - алиас для /start
    app.add_handler(CallbackQueryHandler(main_menu_callback, pattern=f"^{CB.MAIN_MENU}$"))
