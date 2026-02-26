"""Обработчик команды /start и главного меню"""
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ConversationHandler
from loguru import logger

from parserhub.db_service import DatabaseService


# Текст кнопок главного меню
class MenuButton:
    """Константы для текста кнопок"""
    ACCOUNT = "👤 Мой аккаунт"
    WORKERS = "👷 ПВЗ"
    REALTY = "🏠 Поиск объявлений"
    BLACKLIST = "⚫ Черный список"
    SUBSCRIPTION = "💳 Подписка"
    SETTINGS = "❓ Помощь"
    ADMIN = "🔧 Админ-панель"
    BACK = "🔙 Назад"
    CANCEL = "❌ Отмена"


# Фильтр для кнопок главного меню (используется в fallbacks всех ConversationHandler)
MAIN_MENU_FILTER = filters.Regex(
    f"^({MenuButton.ACCOUNT}|{MenuButton.WORKERS}|{MenuButton.REALTY}|"
    f"{MenuButton.BLACKLIST}|{MenuButton.SUBSCRIPTION}|{MenuButton.SETTINGS}|"
    f"{MenuButton.ADMIN}|{MenuButton.BACK}|{MenuButton.CANCEL})$"
)


WELCOME_TEXT = (
    "Добро пожаловать в <b>CommerceBot</b> 🚀\n\n"
    "Неважно кто ты —\n"
    "риэлтор, инвестор, предприниматель, перекуп или просто ищешь выгодный вариант для себя.\n\n"
    "<i>В любой нише выигрывает тот, кто узнаёт первым</i>\n\n"
    "🚀 <b>Главное преимущество:</b>\n"
    "Ты получаешь релевантное объявление сразу после его публикации на Avito и ЦИАН — по заданным фильтрам.\n\n"
    "📱 Без ручного мониторинга.\n"
    "🔁 Без постоянного обновления страниц.\n"
    "⏰ Без потери времени.\n\n"
    "<i>Пока другие только заходят на сайт — ты уже связываешься с продавцом.</i>\n\n"
    "<b>В конкурентных нишах скорость = деньги</b>\n\n"
    "🔎 <b>Бот ищет НЕ только недвижимость</b>\n\n"
    "CommerceBot может находить что угодно, если это появляется на площадках:\n\n"
    " • 🏢 Коммерческая недвижимость\n"
    " • 🚗 Автомобили\n"
    " • 📱 Техника\n"
    " • 🏠 Квартиры\n"
    " • 🛠 Оборудование\n"
    " • 💼 Любые товары и предложения\n\n"
    "Ты задаёшь фильтры — бот автоматически мониторит площадки и присылает только то, что подходит именно тебе.\n\n"
    "👥 <b>Для владельцев ПВЗ:</b>\n\n"
    "Если у тебя пункт выдачи, ты знаешь, сколько времени уходит на поиск сотрудников.\n\n"
    "Больше не нужно:\n\n"
    " • Сидеть в десятках Telegram-чатов\n"
    " • Листать сообщения вручную\n"
    " • Проверять каждого сотрудника самостоятельно\n\n"
    "Бот сам:\n\n"
    "✔️ Мониторит все нужные чаты по заменам\n"
    "✔️ Находит сотрудников по твоим фильтрам\n"
    "✔️ Присылает только релевантные предложения\n"
    "✔️ Проверяет сотрудников по чёрным спискам\n\n"
    "Ты экономишь часы времени и снижаешь риски.\n\n"
    "💎 <b>В чём сила CommerceBot?</b>\n\n"
    "✅ Скорость.\n"
    "✅ Автоматизация.\n"
    "✅ Контроль.\n\n"
    "Настрой фильтры и начни получать лучшие предложения первым 🚀"
)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    user = update.effective_user
    db: DatabaseService = context.bot_data["db"]

    is_new_user = await db.get_user(user.id) is None

    # Регистрация/обновление пользователя
    await db.create_or_update_user(
        user_id=user.id,
        username=user.username,
        full_name=user.full_name,
    )

    logger.info(f"Пользователь {user.id} (@{user.username}) запустил бота")

    if is_new_user:
        await update.message.reply_text(text=WELCOME_TEXT, parse_mode="HTML")

    await show_main_menu(update, context)


async def show_main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать главное меню с Reply кнопками"""
    from parserhub.handlers.admin import _is_admin

    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    is_admin = await _is_admin(user_id, db)

    keyboard = [
        [KeyboardButton(MenuButton.ACCOUNT)],
        [KeyboardButton(MenuButton.REALTY)],
        [KeyboardButton(MenuButton.WORKERS)],
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

    text = "Вы в главном меню. Выберите нужный раздел:"

    # Отправляем новое сообщение с Reply клавиатурой
    if update.message:
        await update.message.reply_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )
    elif update.callback_query:
        # Если пришло из callback (для совместимости)
        await update.callback_query.answer()
        msg = update.callback_query.message
        if msg:
            await msg.reply_text(text=text, reply_markup=reply_markup, parse_mode="HTML")


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
