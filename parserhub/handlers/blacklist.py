"""Обработчики черного списка"""
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
from parserhub.validators import Validators
from parserhub.handlers.start import cancel_and_return_to_menu, MAIN_MENU_FILTER


# Состояния для ConversationHandler
class BlacklistState:
    WAITING_USERNAME = 1
    WAITING_ADD_CHAT = 2
    WAITING_SELECT_TOPIC = 3


# Callback data
class BlacklistCB:
    BLACKLIST_MENU = "blacklist_menu"
    CHECK_USER = "blacklist_check_user"
    MANAGE_CHATS = "blacklist_manage_chats"
    ADD_CHAT = "blacklist_add_chat"
    REMOVE_CHAT = "blacklist_remove_chat_"
    SELECT_TOPIC = "bl_topic_"
    SELECT_ALL_TOPICS = "bl_topic_all"


async def show_blacklist_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню черного списка"""
    user_id = update.effective_user.id
    logger.info(f"[BLACKLIST] show_blacklist_menu вызван от user {user_id}")
    db: DatabaseService = context.bot_data["db"]

    user = await db.get_user(user_id)
    logger.info(f"[BLACKLIST] user.is_blacklist_authorized = {user.is_blacklist_authorized}")

    if not user.is_blacklist_authorized:
        keyboard = [
            [InlineKeyboardButton("🔑 Авторизовать аккаунт", callback_data="auth_blacklist")],
            [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = (
            "⚫ <b>Черный список</b>\n\n"
            "❌ Для работы необходимо авторизовать аккаунт черного списка."
        )

        if update.callback_query:
            await update.callback_query.answer()
            await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
        else:
            await update.message.reply_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
        return

    logger.info(f"[BLACKLIST] Показываем кнопки: CHECK_USER={BlacklistCB.CHECK_USER}")
    keyboard = [
        [InlineKeyboardButton("🔍 Проверить пользователя", callback_data=BlacklistCB.CHECK_USER)],
        [InlineKeyboardButton("🔙 Назад", callback_data="main_menu")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = (
        "⚫ <b>Черный список</b>\n\n"
        "Проверка пользователей по базе черного списка ПВЗ.\n\n"
        "Чаты для проверки настраиваются администратором.\n\n"
        "Выберите действие:"
    )

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode="HTML")
    else:
        await update.message.reply_text(text=text, reply_markup=reply_markup, parse_mode="HTML")


async def start_check_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало проверки пользователя"""
    logger.info(f"[BLACKLIST] start_check_user вызван от user {update.effective_user.id}")
    query = update.callback_query
    await query.answer()

    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="blacklist_cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "🔍 <b>Проверка в черном списке</b>\n\n"
        "Введите username для проверки:\n"
        "<code>@username</code>",
        reply_markup=reply_markup,
        parse_mode="HTML",
    )

    return BlacklistState.WAITING_USERNAME


async def receive_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен username - проверяем в ЧС"""
    username = update.message.text.strip()
    user_id = update.effective_user.id

    # Валидация username
    valid, normalized_username, error = Validators.validate_username(username)
    if not valid:
        await update.message.reply_text(
            f"{error}\n\n"
            "Попробуйте ещё раз. Пример:\n"
            "<code>@username</code>",
            parse_mode="HTML"
        )
        return BlacklistState.WAITING_USERNAME

    workers_api: WorkersAPI = context.bot_data["workers_api"]
    session_mgr: SessionManager = context.bot_data["session_manager"]

    # Получить путь к blacklist сессии
    blacklist_session_path = session_mgr.get_session_path(user_id, "blacklist")

    try:
        result = await workers_api.check_blacklist(normalized_username, blacklist_session_path)

        if result["found"]:
            # Найден в ЧС
            info = result.get("extracted_info", {})
            username_info = info.get("username", "—")
            phone = info.get("phone", "—")
            user_id = info.get("user_id", "—")

            message_link = result.get("message_link", "—")
            chat = result.get("chat", "—")

            text = (
                "⚠️ <b>Пользователь найден в черном списке!</b>\n\n"
                f"<b>Username:</b> {username_info}\n"
                f"<b>Телефон:</b> {phone}\n"
                f"<b>User ID:</b> {user_id}\n\n"
                f"<b>Чат ЧС:</b> {chat}\n"
                f"<b>Ссылка на сообщение:</b>\n{message_link}\n\n"
                f"<b>Текст записи:</b>\n<i>{result.get('message_text', '')[:200]}...</i>"
            )
        else:
            # Не найден
            text = (
                "✅ <b>Пользователь НЕ найден в черном списке</b>\n\n"
                f"<b>Username:</b> {username}\n"
                f"<b>Проверено сообщений:</b> {result.get('messages_checked', 0)}\n"
                f"<b>Проверено чатов:</b> {len(result.get('chats_checked', []))}"
            )

        await update.message.reply_text(text, parse_mode="HTML")

    except Exception as e:
        error_msg = str(e).lower()
        logger.error(f"Ошибка проверки ЧС: {e}")

        # Проверка на обрыв авторизации
        if any(keyword in error_msg for keyword in ["authkeyinvalid", "auth", "session", "unauthorized", "сессия"]):
            logger.warning(f"Обнаружен обрыв авторизации blacklist для user {update.effective_user.id}")

            # Сбросить статус авторизации в БД
            db: DatabaseService = context.bot_data["db"]
            await db.update_auth_status(update.effective_user.id, "blacklist", False)

            # Очистить данные пользователя
            context.user_data.clear()

            await update.message.reply_text(
                "⚠️ <b>Авторизация оборвана</b>\n\n"
                "Произошла ошибка со стороны Telegram.\n"
                "Пожалуйста, авторизуйтесь заново через меню \"👤 Мой аккаунт\".",
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                f"❌ Ошибка проверки:\n\n{str(e)}"
            )

    return ConversationHandler.END


async def show_manage_chats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать список чатов ЧС"""
    workers_api: WorkersAPI = context.bot_data["workers_api"]

    try:
        result = await workers_api.get_blacklist_chats()
        chats = result.get("chats", [])

        if not chats:
            keyboard = [
                [InlineKeyboardButton("➕ Добавить чат", callback_data=BlacklistCB.ADD_CHAT)],
                [InlineKeyboardButton("🔙 Назад", callback_data=BlacklistCB.BLACKLIST_MENU)],
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.callback_query.answer()
            await update.callback_query.edit_message_text(
                "📋 <b>Чаты черного списка</b>\n\n"
                "Список пуст.",
                reply_markup=reply_markup,
                parse_mode="HTML",
            )
            return

        text_lines = ["📋 <b>Чаты черного списка</b>\n"]

        keyboard = []
        for chat in chats:
            status = "✅" if chat["is_active"] else "❌"
            topic_name = chat.get("topic_name")
            topic_id = chat.get("topic_id")

            # Формируем отображение с топиком
            if topic_name:
                text_lines.append(
                    f"{status} <b>{chat.get('chat_title') or chat['chat_username']}</b>\n"
                    f"   {chat['chat_username']} / {topic_name}"
                )
            else:
                text_lines.append(
                    f"{status} <b>{chat.get('chat_title') or chat['chat_username']}</b>\n"
                    f"   {chat['chat_username']}"
                )

            # Формируем callback_data для удаления (включая topic_id)
            if topic_id is not None:
                remove_data = f"{BlacklistCB.REMOVE_CHAT}{chat['chat_username']}|{topic_id}"
            else:
                remove_data = f"{BlacklistCB.REMOVE_CHAT}{chat['chat_username']}"

            # Формируем текст кнопки
            btn_text = f"🗑️ {chat['chat_username']}"
            if topic_name:
                btn_text += f" / {topic_name}"

            keyboard.append([
                InlineKeyboardButton(btn_text, callback_data=remove_data)
            ])

        keyboard.append([InlineKeyboardButton("➕ Добавить чат", callback_data=BlacklistCB.ADD_CHAT)])
        keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=BlacklistCB.BLACKLIST_MENU)])

        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            "\n\n".join(text_lines),
            reply_markup=reply_markup,
            parse_mode="HTML",
        )

    except Exception as e:
        logger.error(f"Ошибка получения чатов ЧС: {e}")
        await update.callback_query.answer("❌ Ошибка получения списка", show_alert=True)


async def start_add_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало добавления чата в ЧС"""
    query = update.callback_query
    await query.answer()

    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="blacklist_cancel")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(
        "➕ <b>Добавить чат в черный список</b>\n\n"
        "Введите username чата:\n"
        "<code>@Blacklist_pvz</code>",
        reply_markup=reply_markup,
        parse_mode="HTML",
    )

    return BlacklistState.WAITING_ADD_CHAT


async def receive_add_chat(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен username чата для добавления — проверяем, является ли форумом"""
    chat_username = update.message.text.strip()
    user_id = update.effective_user.id

    # Валидация username
    valid, normalized_username, error = Validators.validate_username(chat_username)
    if not valid:
        await update.message.reply_text(
            f"{error}\n\n"
            "Попробуйте ещё раз. Пример:\n"
            "<code>@Blacklist_pvz</code>",
            parse_mode="HTML"
        )
        return BlacklistState.WAITING_ADD_CHAT

    workers_api: WorkersAPI = context.bot_data["workers_api"]
    session_mgr: SessionManager = context.bot_data["session_manager"]

    # Получаем путь к blacklist сессии для проверки топиков
    blacklist_session_path = session_mgr.get_session_path(user_id, "blacklist")

    # Показываем индикатор загрузки
    status_msg = await update.message.reply_text("🔍 Проверяю чат...")

    try:
        # Проверяем, является ли чат форумом
        topics_result = await workers_api.get_chat_topics(normalized_username, blacklist_session_path)

        if topics_result.get("is_forum") and topics_result.get("topics"):
            # Это форум — показываем выбор топиков
            chat_title = topics_result.get("chat_title", normalized_username)
            topics = topics_result["topics"]

            # Сохраняем данные в user_data для следующего шага
            context.user_data["bl_add_chat_username"] = normalized_username
            context.user_data["bl_add_chat_title"] = chat_title
            context.user_data["bl_chat_topics"] = {t["id"]: t["name"] for t in topics}

            keyboard = []
            for topic in topics:
                keyboard.append([
                    InlineKeyboardButton(
                        f"📌 {topic['name']}",
                        callback_data=f"{BlacklistCB.SELECT_TOPIC}{topic['id']}"
                    )
                ])

            # Кнопка "Весь чат"
            keyboard.append([
                InlineKeyboardButton(
                    "📂 Весь чат (все топики)",
                    callback_data=BlacklistCB.SELECT_ALL_TOPICS
                )
            ])
            keyboard.append([InlineKeyboardButton("❌ Отмена", callback_data="blacklist_cancel")])

            reply_markup = InlineKeyboardMarkup(keyboard)

            await status_msg.edit_text(
                f"📋 <b>Чат {normalized_username} — это форум</b>\n"
                f"<b>{chat_title}</b>\n\n"
                f"Найдено топиков: {len(topics)}\n\n"
                "Выберите конкретный топик для поиска в ЧС\n"
                "(это значительно ускорит проверку):",
                reply_markup=reply_markup,
                parse_mode="HTML",
            )

            return BlacklistState.WAITING_SELECT_TOPIC

        else:
            # Не форум — сохраняем сразу
            chat_title = topics_result.get("chat_title", "")
            result = await workers_api.add_blacklist_chat(normalized_username, chat_title=chat_title)
            await status_msg.edit_text(
                f"✅ Чат {normalized_username} добавлен в черный список!"
            )
            return ConversationHandler.END

    except Exception as e:
        error_msg = str(e).lower()
        logger.error(f"Ошибка добавления чата в ЧС: {e}")

        # Проверка на обрыв авторизации
        if any(keyword in error_msg for keyword in ["authkeyinvalid", "auth", "session", "unauthorized", "сессия"]):
            logger.warning(f"Обнаружен обрыв авторизации blacklist для user {user_id}")

            # Сбросить статус авторизации в БД
            db: DatabaseService = context.bot_data["db"]
            await db.update_auth_status(user_id, "blacklist", False)

            # Очистить данные пользователя
            context.user_data.clear()

            await status_msg.edit_text(
                "⚠️ <b>Авторизация оборвана</b>\n\n"
                "Произошла ошибка со стороны Telegram.\n"
                "Пожалуйста, авторизуйтесь заново через меню \"👤 Мой аккаунт\".",
                parse_mode="HTML"
            )
        else:
            await status_msg.edit_text(
                f"❌ Ошибка:\n\n{str(e)}"
            )
        return ConversationHandler.END


async def receive_topic_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обработка выбора топика из inline-кнопок"""
    query = update.callback_query
    await query.answer()

    workers_api: WorkersAPI = context.bot_data["workers_api"]
    chat_username = context.user_data.get("bl_add_chat_username", "")
    chat_title = context.user_data.get("bl_add_chat_title", "")

    data = query.data

    if data == BlacklistCB.SELECT_ALL_TOPICS:
        # Пользователь выбрал "Весь чат" — сохраняем без topic_id
        try:
            result = await workers_api.add_blacklist_chat(chat_username, chat_title=chat_title)
            await query.edit_message_text(
                f"✅ Чат {chat_username} добавлен в черный список!\n"
                f"(все топики)"
            )
        except Exception as e:
            logger.error(f"Ошибка добавления чата в ЧС: {e}")
            await query.edit_message_text(f"❌ Ошибка:\n\n{str(e)}")

    elif data.startswith(BlacklistCB.SELECT_TOPIC):
        # Пользователь выбрал конкретный топик
        topic_id = int(data[len(BlacklistCB.SELECT_TOPIC):])
        topics_map = context.user_data.get("bl_chat_topics", {})
        topic_name = topics_map.get(topic_id, f"Topic {topic_id}")

        try:
            result = await workers_api.add_blacklist_chat(
                chat_username,
                chat_title=chat_title,
                topic_id=topic_id,
                topic_name=topic_name,
            )
            await query.edit_message_text(
                f"✅ Чат {chat_username} добавлен в черный список!\n"
                f"Топик: <b>{topic_name}</b>",
                parse_mode="HTML",
            )
        except Exception as e:
            logger.error(f"Ошибка добавления чата в ЧС: {e}")
            await query.edit_message_text(f"❌ Ошибка:\n\n{str(e)}")

    # Очищаем user_data
    context.user_data.pop("bl_add_chat_username", None)
    context.user_data.pop("bl_add_chat_title", None)
    context.user_data.pop("bl_chat_topics", None)

    return ConversationHandler.END


async def remove_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удаление чата из ЧС"""
    query = update.callback_query
    raw_data = query.data.replace(BlacklistCB.REMOVE_CHAT, "")

    # Парсим chat_username и опциональный topic_id
    if "|" in raw_data:
        chat_username, topic_id_str = raw_data.split("|", 1)
        topic_id = int(topic_id_str)
    else:
        chat_username = raw_data
        topic_id = None

    workers_api: WorkersAPI = context.bot_data["workers_api"]

    try:
        result = await workers_api.remove_blacklist_chat(chat_username, topic_id=topic_id)
        await query.answer(f"✅ Чат {chat_username} удалён")
        await show_manage_chats(update, context)

    except Exception as e:
        logger.error(f"Ошибка удаления чата из ЧС: {e}")
        await query.answer("❌ Ошибка удаления", show_alert=True)


async def cancel_blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена операции"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Операция отменена.")
    # Очищаем user_data
    context.user_data.pop("bl_add_chat_username", None)
    context.user_data.pop("bl_add_chat_title", None)
    return ConversationHandler.END


def register_blacklist_handlers(app):
    """Регистрация обработчиков черного списка"""
    # Меню
    app.add_handler(
        CallbackQueryHandler(show_blacklist_menu, pattern=f"^{BlacklistCB.BLACKLIST_MENU}$|^blacklist$")
    )

    # ConversationHandler для проверки пользователя
    check_user_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(start_check_user, pattern=f"^{BlacklistCB.CHECK_USER}$")
        ],
        states={
            BlacklistState.WAITING_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, receive_username)
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_blacklist, pattern="^blacklist_cancel$"),
            CommandHandler("start", cancel_and_return_to_menu),
            MessageHandler(MAIN_MENU_FILTER, cancel_and_return_to_menu),
        ],
    )
    app.add_handler(check_user_conv)
