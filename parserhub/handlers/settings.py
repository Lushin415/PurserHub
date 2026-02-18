"""Обработчики настроек пользователя"""
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
)


# Callback data
class SettingsCB:
    SETTINGS_MENU = "settings_menu"


async def show_settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню настроек (уведомления централизованы через основной бот)"""
    keyboard = [
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "💬 <b>Обратная связь и справка</b>\n\n"
        "Все уведомления о поиске объявлений приходят "
        "в этот чат от бота.\n\n"
        "📝 <b>По всем вопросам</b>\n"
        "обращайтесь к администратору."
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text=text, reply_markup=reply_markup, parse_mode="HTML")


def register_settings_handlers(app):
    """Регистрация обработчиков настроек (упрощено - уведомления централизованы)"""
    # Главное меню настроек
    app.add_handler(
        CallbackQueryHandler(show_settings_menu, pattern=f"^{SettingsCB.SETTINGS_MENU}$|^settings$")
    )
