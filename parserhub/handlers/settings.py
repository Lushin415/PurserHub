"""Обработчики настроек пользователя"""
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
from parserhub.models import UserSettings
from parserhub.validators import Validators
from parserhub.handlers.start import cancel_and_return_to_menu


# Состояния для ConversationHandler
class SettingsState:
    WAITING_WORKERS_CHAT_ID = 1
    WAITING_REALTY_TOKEN = 2
    WAITING_REALTY_CHAT_ID = 3


# Callback data
class SettingsCB:
    SETTINGS_MENU = "settings_menu"
    WORKERS_BOT = "settings_workers_bot"
    REALTY_BOT = "settings_realty_bot"
    CHANGE_WORKERS_CHAT = "change_workers_chat"
    CHANGE_REALTY_TOKEN = "change_realty_token"
    CHANGE_REALTY_CHAT = "change_realty_chat"


async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать главное меню настроек"""
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    settings = await db.get_settings(user_id)

    keyboard = [
        [InlineKeyboardButton("👷 Бот для ПВЗ", callback_data=SettingsCB.WORKERS_BOT)],
        [InlineKeyboardButton("🏠 Бот для недвижимости", callback_data=SettingsCB.REALTY_BOT)],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "⚙️ <b>Настройки</b>\n\n"
        "Настройте ботов для получения уведомлений."
    )

    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        text=text, reply_markup=reply_markup, parse_mode="HTML"
    )


async def show_workers_bot_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройки уведомлений о ПВЗ"""
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    settings = await db.get_settings(user_id)

    chat_id_status = settings.workers_chat_id if settings.workers_chat_id else "❌ не задан"

    keyboard = [
        [InlineKeyboardButton("💬 Изменить Chat ID", callback_data=SettingsCB.CHANGE_WORKERS_CHAT)],
        [InlineKeyboardButton("🔙 Назад", callback_data=SettingsCB.SETTINGS_MENU)],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "👷 <b>Уведомления о ПВЗ</b>\n\n"
        f"<b>Chat ID:</b> {chat_id_status}\n\n"
        "<i>Укажите Chat ID куда отправлять уведомления о найденных работниках/работодателях.\n"
        "Уведомления приходят от общего бота ParserHub.</i>"
    )

    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        text=text, reply_markup=reply_markup, parse_mode="HTML"
    )


async def show_realty_bot_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Настройки бота для недвижимости"""
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    settings = await db.get_settings(user_id)

    token_status = "✅ задан" if settings.realty_bot_token else "❌ не задан"
    chat_id_status = settings.realty_chat_id if settings.realty_chat_id else "❌ не задан"

    keyboard = [
        [InlineKeyboardButton("🔑 Изменить токен бота", callback_data=SettingsCB.CHANGE_REALTY_TOKEN)],
        [InlineKeyboardButton("💬 Изменить Chat ID", callback_data=SettingsCB.CHANGE_REALTY_CHAT)],
        [InlineKeyboardButton("🔙 Назад", callback_data=SettingsCB.SETTINGS_MENU)],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "🏠 <b>Бот для уведомлений о недвижимости</b>\n\n"
        f"<b>Токен бота:</b> {token_status}\n"
        f"<b>Chat ID:</b> {chat_id_status}\n\n"
        "<i>Этот бот будет отправлять уведомления о найденных объявлениях Avito/Cian.</i>"
    )

    await update.callback_query.answer()
    await update.callback_query.edit_message_text(
        text=text, reply_markup=reply_markup, parse_mode="HTML"
    )


# ===== Изменение токена Workers бота =====

async def start_change_workers_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало изменения Chat ID Workers бота"""
    query = update.callback_query
    await query.answer()

    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="settings_cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "💬 <b>Chat ID для уведомлений о ПВЗ</b>\n\n"
        "Введите Chat ID (обычно ваш Telegram User ID):\n"
        "<code>338908929</code>",
        reply_markup=reply_markup,
        parse_mode="HTML",
    )

    return SettingsState.WAITING_WORKERS_CHAT_ID


async def receive_workers_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен Chat ID Workers бота"""
    chat_id_str = update.message.text.strip()
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    # Валидация Chat ID
    valid, chat_id, error = Validators.validate_chat_id(chat_id_str)
    if not valid:
        await update.message.reply_text(
            f"{error}\n\n"
            "Попробуйте ещё раз."
        )
        return SettingsState.WAITING_WORKERS_CHAT_ID

    # Сохранить
    settings = await db.get_settings(user_id)
    settings.workers_chat_id = chat_id
    await db.update_settings(settings)

    await update.message.reply_text("✅ Chat ID для ПВЗ сохранён!")
    return ConversationHandler.END


# ===== Изменение токена Realty бота =====

async def start_change_realty_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало изменения токена Realty бота"""
    query = update.callback_query
    await query.answer()

    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="settings_cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "🔑 <b>Токен бота для недвижимости</b>\n\n"
        "Введите токен бота (получить у @BotFather):\n"
        "<code>123456:ABC-DEF1234...</code>",
        reply_markup=reply_markup,
        parse_mode="HTML",
    )

    return SettingsState.WAITING_REALTY_TOKEN


async def receive_realty_token(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен токен Realty бота"""
    token = update.message.text.strip()
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    # Валидация токена
    valid, error = Validators.validate_bot_token(token)
    if not valid:
        await update.message.reply_text(
            f"{error}\n\n"
            "Попробуйте ещё раз."
        )
        return SettingsState.WAITING_REALTY_TOKEN

    # Сохранить
    settings = await db.get_settings(user_id)
    settings.realty_bot_token = token
    await db.update_settings(settings)

    await update.message.reply_text("✅ Токен бота для недвижимости сохранён!")
    return ConversationHandler.END


async def start_change_realty_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало изменения Chat ID Realty бота"""
    query = update.callback_query
    await query.answer()

    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="settings_cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "💬 <b>Chat ID для уведомлений о недвижимости</b>\n\n"
        "Введите Chat ID (обычно ваш Telegram User ID):\n"
        "<code>338908929</code>",
        reply_markup=reply_markup,
        parse_mode="HTML",
    )

    return SettingsState.WAITING_REALTY_CHAT_ID


async def receive_realty_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен Chat ID Realty бота"""
    chat_id_str = update.message.text.strip()
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    # Валидация Chat ID
    valid, chat_id, error = Validators.validate_chat_id(chat_id_str)
    if not valid:
        await update.message.reply_text(
            f"{error}\n\n"
            "Попробуйте ещё раз."
        )
        return SettingsState.WAITING_REALTY_CHAT_ID

    # Сохранить
    settings = await db.get_settings(user_id)
    settings.realty_chat_id = chat_id
    await db.update_settings(settings)

    await update.message.reply_text("✅ Chat ID для недвижимости сохранён!")
    return ConversationHandler.END


async def cancel_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена изменения настроек"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Изменение отменено.")
    return ConversationHandler.END


def register_settings_handlers(app):
    """Регистрация обработчиков настроек"""
    # Главное меню настроек
    app.add_handler(
        CallbackQueryHandler(show_settings_menu, pattern=f"^{SettingsCB.SETTINGS_MENU}$|^settings$")
    )

    # Меню ботов
    app.add_handler(
        CallbackQueryHandler(show_workers_bot_settings, pattern=f"^{SettingsCB.WORKERS_BOT}$")
    )
    app.add_handler(
        CallbackQueryHandler(show_realty_bot_settings, pattern=f"^{SettingsCB.REALTY_BOT}$")
    )

    # ConversationHandler для Workers Chat ID
    workers_chat_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_change_workers_chat, pattern=f"^{SettingsCB.CHANGE_WORKERS_CHAT}$")
        ],
        states={
            SettingsState.WAITING_WORKERS_CHAT_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_workers_chat)
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_settings, pattern="^settings_cancel$"),
            CommandHandler("start", cancel_and_return_to_menu),
            CommandHandler("menu", cancel_and_return_to_menu),
        ],
    )
    app.add_handler(workers_chat_conv)

    # ConversationHandler для Realty токена
    realty_token_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_change_realty_token, pattern=f"^{SettingsCB.CHANGE_REALTY_TOKEN}$")
        ],
        states={
            SettingsState.WAITING_REALTY_TOKEN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_realty_token)
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_settings, pattern="^settings_cancel$"),
            CommandHandler("start", cancel_and_return_to_menu),
            CommandHandler("menu", cancel_and_return_to_menu),
        ],
    )
    app.add_handler(realty_token_conv)

    # ConversationHandler для Realty Chat ID
    realty_chat_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_change_realty_chat, pattern=f"^{SettingsCB.CHANGE_REALTY_CHAT}$")
        ],
        states={
            SettingsState.WAITING_REALTY_CHAT_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_realty_chat)
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_settings, pattern="^settings_cancel$"),
            CommandHandler("start", cancel_and_return_to_menu),
            CommandHandler("menu", cancel_and_return_to_menu),
        ],
    )
    app.add_handler(realty_chat_conv)
