"""Обработчики авторизации Telegram аккаунтов (2 сессии)"""
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
from parserhub.validators import Validators
from parserhub.handlers.start import cancel_and_return_to_menu, MAIN_MENU_FILTER


# Состояния для ConversationHandler
class AuthState:
    WAITING_PHONE = 1
    WAITING_CODE = 2
    WAITING_2FA = 3


# Callback data
class AuthCB:
    ACCOUNT_MENU = "account_menu"
    AUTH_PARSER = "auth_parser"
    AUTH_BLACKLIST = "auth_blacklist"
    DISCONNECT_PARSER = "disconnect_parser"
    DISCONNECT_BLACKLIST = "disconnect_blacklist"
    DISCONNECT_ALL = "disconnect_all"


async def show_account_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню аккаунта"""
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]
    session_mgr: SessionManager = context.bot_data["session_manager"]

    user = await db.get_user(user_id)

    # Проверяем статус сессий
    parser_exists = session_mgr.session_exists(user_id, "parser")
    blacklist_exists = session_mgr.session_exists(user_id, "blacklist")

    parser_status = "✅ Подключён" if parser_exists else "❌ Не подключён"
    blacklist_status = "✅ Подключён" if blacklist_exists else "❌ Не подключён"

    keyboard = []

    # Авторизация парсера ПВЗ
    if not parser_exists:
        keyboard.append([
            InlineKeyboardButton("🔑 Авторизация парсера ПВЗ", callback_data=AuthCB.AUTH_PARSER)
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("🔓 Отключить поисковик ПВЗ", callback_data=AuthCB.DISCONNECT_PARSER)
        ])

    # Авторизация Черного списка
    if not blacklist_exists:
        keyboard.append([
            InlineKeyboardButton("🔑 Авторизация Черного списка", callback_data=AuthCB.AUTH_BLACKLIST)
        ])
    else:
        keyboard.append([
            InlineKeyboardButton("🔓 Отключить Черный список", callback_data=AuthCB.DISCONNECT_BLACKLIST)
        ])

    # Отключить все аккаунты
    if parser_exists or blacklist_exists:
        keyboard.append([
            InlineKeyboardButton("⛔ Отключить все аккаунты", callback_data=AuthCB.DISCONNECT_ALL)
        ])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data="main_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "👤 <b>Мой аккаунт</b>\n\n"
        f"<b>Статус подключений:</b>\n"
        f"👷 Поиск работников и работодателей ПВЗ: {parser_status}\n"
        f"⚫ Черный список: {blacklist_status}\n\n"
        "Для работы с поиском и черным списком необходимо авторизовать Telegram аккаунт."
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text=text, reply_markup=reply_markup, parse_mode="HTML")


async def start_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало авторизации - запрос номера телефона"""
    query = update.callback_query
    await query.answer()

    # Определяем тип сессии
    if query.data == AuthCB.AUTH_PARSER:
        session_type = "parser"
        session_name = "Парсер ПВЗ"
    else:
        session_type = "blacklist"
        session_name = "Черный список"

    # Сохраняем в контекст
    context.user_data["auth_session_type"] = session_type

    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="auth_cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        f"🔑 <b>Авторизация: {session_name}</b>\n\n"
        f"Введите номер телефона в формате:\n"
        f"<code>+79991234567</code>\n\n"
        f"<b>❗️ВАЖНО:</b> После успешной авторизации Telegram пришлёт уведомление о новом входе — это нормально, вы сами разрешили боту доступ. Нажмите <b>«Да, это я»</b>.",
        reply_markup=reply_markup,
        parse_mode="HTML",
    )

    return AuthState.WAITING_PHONE


async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен номер телефона - отправляем код"""
    phone = update.message.text.strip()
    user_id = update.effective_user.id
    session_type = context.user_data.get("auth_session_type")

    session_mgr: SessionManager = context.bot_data["session_manager"]

    # Удаляем сообщение с номером (безопасность)
    try:
        await update.message.delete()
    except:
        pass

    # Валидация номера телефона
    valid, normalized_phone, error = Validators.validate_phone_number(phone)
    if not valid:
        keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="auth_cancel")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"{error}\n\n"
            "Попробуйте ещё раз. Правильный формат:\n"
            "<code>+79991234567</code>",
            reply_markup=reply_markup,
            parse_mode="HTML",
        )
        return AuthState.WAITING_PHONE

    try:
        result = await session_mgr.start_auth(user_id, session_type, normalized_phone)

        if result == "code_sent":
            keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="auth_cancel")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                "📱 Код отправлен в Telegram!\n\n"
                "Введите код ЧЕРЕЗ ПРОБЕЛЫ:\n"
                "Например: 1 2 3 4 5 или 1 2 345",
                reply_markup=reply_markup,
            )
            return AuthState.WAITING_CODE

    except Exception as e:
        logger.error(f"Ошибка отправки кода: {e}")
        await update.message.reply_text(
            f"❌ Ошибка отправки кода:\n{str(e)}\n\n"
            "Попробуйте снова позже.",
        )
        return ConversationHandler.END


async def receive_code(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен код подтверждения"""
    # Убираем пробелы из кода (пользователь вводит "1 2 3 4 5" → "12345")
    code = update.message.text.strip().replace(" ", "")
    user_id = update.effective_user.id
    session_type = context.user_data.get("auth_session_type")

    logger.info(f"[HANDLER] receive_code вызван: user_id={user_id}, session_type={session_type}, code={code}")

    session_mgr: SessionManager = context.bot_data["session_manager"]
    db: DatabaseService = context.bot_data["db"]

    # Удаляем сообщение с кодом
    try:
        await update.message.delete()
        logger.debug(f"[HANDLER] Сообщение с кодом удалено")
    except Exception as e:
        logger.warning(f"[HANDLER] Не удалось удалить сообщение: {e}")

    try:
        logger.info(f"[HANDLER] Вызов session_mgr.confirm_code()...")
        result = await session_mgr.confirm_code(user_id, code)

        if result == "success":
            # Успех!
            tg_user = update.effective_user
            await db.create_or_update_user(user_id, tg_user.username, tg_user.full_name)
            await db.update_auth_status(user_id, session_type, True)

            session_name = "Парсер ПВЗ" if session_type == "parser" else "Черный список"
            await update.message.reply_text(
                f"✅ <b>Аккаунт подключён!</b>\n\n"
                f"{session_name} успешно авторизован.",
                parse_mode="HTML",
            )
            return ConversationHandler.END

        elif result == "need_2fa":
            keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="auth_cancel")]]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                "🔐 Введите пароль двухфакторной аутентификации:",
                reply_markup=reply_markup,
            )
            return AuthState.WAITING_2FA

        else:
            await update.message.reply_text(
                "❌ Неверный или истёкший код.\n\n"
                "Попробуйте начать авторизацию заново."
            )
            return ConversationHandler.END

    except Exception as e:
        logger.error(f"Ошибка подтверждения кода: {e}")
        await update.message.reply_text(
            f"❌ Ошибка:\n{str(e)}\n\n"
            "Попробуйте снова позже.",
        )
        return ConversationHandler.END


async def receive_2fa(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен пароль 2FA"""
    password = update.message.text.strip()
    user_id = update.effective_user.id
    session_type = context.user_data.get("auth_session_type")

    session_mgr: SessionManager = context.bot_data["session_manager"]
    db: DatabaseService = context.bot_data["db"]

    # Удаляем сообщение с паролем
    try:
        await update.message.delete()
    except:
        pass

    try:
        success = await session_mgr.confirm_2fa(user_id, password)

        if success:
            tg_user = update.effective_user
            await db.create_or_update_user(user_id, tg_user.username, tg_user.full_name)
            await db.update_auth_status(user_id, session_type, True)

            session_name = "Парсер ПВЗ" if session_type == "parser" else "Черный список"
            await update.message.reply_text(
                f"✅ <b>Аккаунт подключён!</b>\n\n"
                f"{session_name} успешно авторизован.",
                parse_mode="HTML",
            )
            return ConversationHandler.END
        else:
            await update.message.reply_text(
                "❌ Неверный пароль.\n\n"
                "Попробуйте начать авторизацию заново."
            )
            return ConversationHandler.END

    except Exception as e:
        logger.error(f"Ошибка 2FA: {e}")
        await update.message.reply_text(
            f"❌ Ошибка:\n{str(e)}\n\n"
            "Попробуйте снова позже.",
        )
        return ConversationHandler.END


async def cancel_auth(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена авторизации"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Авторизация отменена.")
    return ConversationHandler.END


async def disconnect_parser(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отключить парсер ПВЗ"""
    user_id = update.effective_user.id
    session_mgr: SessionManager = context.bot_data["session_manager"]
    db: DatabaseService = context.bot_data["db"]

    await session_mgr.delete_session(user_id, "parser")
    await db.update_auth_status(user_id, "parser", False)

    await update.callback_query.answer("Поисковик ПВЗ отключён")
    await show_account_menu(update, context)


async def disconnect_blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отключить черный список"""
    user_id = update.effective_user.id
    session_mgr: SessionManager = context.bot_data["session_manager"]
    db: DatabaseService = context.bot_data["db"]

    await session_mgr.delete_session(user_id, "blacklist")
    await db.update_auth_status(user_id, "blacklist", False)

    await update.callback_query.answer("Черный список отключён")
    await show_account_menu(update, context)


async def disconnect_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Отключить все аккаунты"""
    user_id = update.effective_user.id
    session_mgr: SessionManager = context.bot_data["session_manager"]
    db: DatabaseService = context.bot_data["db"]

    await session_mgr.delete_session(user_id, "parser")
    await session_mgr.delete_session(user_id, "blacklist")
    await db.update_auth_status(user_id, "parser", False)
    await db.update_auth_status(user_id, "blacklist", False)

    await update.callback_query.answer("Все аккаунты отключены")
    await show_account_menu(update, context)


def register_auth_handlers(app):
    """Регистрация обработчиков авторизации"""
    # Меню аккаунта
    app.add_handler(
        CallbackQueryHandler(show_account_menu, pattern=f"^{AuthCB.ACCOUNT_MENU}$|^account$")
    )

    # ConversationHandler для авторизации
    auth_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_auth, pattern=f"^{AuthCB.AUTH_PARSER}$"),
            CallbackQueryHandler(start_auth, pattern=f"^{AuthCB.AUTH_BLACKLIST}$"),
        ],
        states={
            AuthState.WAITING_PHONE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, receive_phone)
            ],
            AuthState.WAITING_CODE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, receive_code)
            ],
            AuthState.WAITING_2FA: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, receive_2fa)
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_auth, pattern="^auth_cancel$"),
            CommandHandler("start", cancel_and_return_to_menu),
            MessageHandler(MAIN_MENU_FILTER, cancel_and_return_to_menu),
        ],
        conversation_timeout=300,
        allow_reentry=True,
    )
    app.add_handler(auth_conv)

    # Отключение
    app.add_handler(
        CallbackQueryHandler(disconnect_parser, pattern=f"^{AuthCB.DISCONNECT_PARSER}$")
    )
    app.add_handler(
        CallbackQueryHandler(disconnect_blacklist, pattern=f"^{AuthCB.DISCONNECT_BLACKLIST}$")
    )
    app.add_handler(
        CallbackQueryHandler(disconnect_all, pattern=f"^{AuthCB.DISCONNECT_ALL}$")
    )
