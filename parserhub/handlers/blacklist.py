"""Обработчики черного списка"""
import asyncio
import html as html_module
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, Bot
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
from parserhub.validators import Validators
from parserhub.handlers.start import cancel_and_return_to_menu, MAIN_MENU_FILTER, MenuButton


_TG_LIMIT = 4096       # Лимит Telegram на одно сообщение
_CHUNK_SIZE = 3800     # Размер куска текста с запасом на label и HTML-теги


def _split_text(text: str, chunk_size: int = _CHUNK_SIZE) -> list[str]:
    """Разбить длинный текст на части не длиннее chunk_size.
    Пытается разбить по переносу строки, чтобы не резать посередине предложения."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    while len(text) > chunk_size:
        split_at = text.rfind('\n', 0, chunk_size)
        if split_at == -1:  # нет переноса строки — режем жёстко
            split_at = chunk_size
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip('\n')

    if text:
        chunks.append(text)

    return chunks


# Состояния для ConversationHandler
class BlacklistState:
    WAITING_USERNAME = 1
    WAITING_FIO = 4
    WAITING_ADD_CHAT = 2
    WAITING_SELECT_TOPIC = 3


# Callback data
class BlacklistCB:
    BLACKLIST_MENU = "blacklist_menu"
    MANAGE_CHATS = "blacklist_manage_chats"
    ADD_CHAT = "blacklist_add_chat"
    REMOVE_CHAT = "blacklist_remove_chat_"
    SELECT_TOPIC = "bl_topic_"
    SELECT_ALL_TOPICS = "bl_topic_all"


# Reply-кнопки подменю
class BlacklistBtn:
    CHECK = "🔍 Проверить пользователя"
    SKIP_FIO = "⏩ Пропустить"
    FIO_ONLY = "👤 Только по ФИО"


async def show_blacklist_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню черного списка"""
    user_id = update.effective_user.id
    logger.info(f"[BLACKLIST] show_blacklist_menu вызван от user {user_id}")
    db: DatabaseService = context.bot_data["db"]

    user = await db.get_user(user_id)
    logger.info(f"[BLACKLIST] user.is_blacklist_authorized = {user.is_blacklist_authorized}")

    if not user.is_blacklist_authorized:
        keyboard = ReplyKeyboardMarkup([
            [KeyboardButton(MenuButton.ACCOUNT)],
            [KeyboardButton(MenuButton.BACK)],
        ], resize_keyboard=True)

        text = (
            "⚫ <b>Черный список</b>\n\n"
            "❌ Для работы необходимо авторизовать аккаунт черного списка.\n\n"
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
        [KeyboardButton(BlacklistBtn.CHECK)],
        [KeyboardButton(MenuButton.BACK)],
    ], resize_keyboard=True)

    text = (
        "⚫ <b>Черный список</b>\n\n"
        "Проверка пользователей по базе черного списка ПВЗ.\n\n"
        "Выберите действие:"
    )

    if update.callback_query:
        await update.callback_query.answer()
        msg = update.callback_query.message
        if msg:
            await msg.reply_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.message.reply_text(text=text, reply_markup=keyboard, parse_mode="HTML")


async def start_check_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало проверки пользователя"""
    logger.info(f"[BLACKLIST] start_check_user вызван от user {update.effective_user.id}")

    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton(BlacklistBtn.FIO_ONLY)],
        [KeyboardButton(MenuButton.CANCEL)],
    ], resize_keyboard=True)

    await update.message.reply_text(
        "🔍 <b>Проверка в чёрном списке</b>\n\n"
        "Введите username для проверки:\n"
        "<code>@username</code>\n\n"
        "Если аккаунта Telegram нет — нажмите «👤 Только по ФИО».\n\n"
        "⏳ <i>Поиск занимает несколько минут — бот пришлёт результат автоматически.</i>",
        reply_markup=keyboard,
        parse_mode="HTML",
    )

    return BlacklistState.WAITING_USERNAME


async def _blacklist_search_task(
    bot: Bot,
    chat_id: int,
    user_id: int,
    username: str | None,
    normalized_username: str | None,
    fio: str | None,
    workers_api: WorkersAPI,
    db: DatabaseService,
    blacklist_session_path: str,
    bot_data: dict,
):
    """Фоновая задача поиска в ЧС — выполняется без блокировки бота"""
    try:
        result = await workers_api.check_blacklist(normalized_username, blacklist_session_path, fio=fio)

        # Проверяем ошибку авторизации в теле ответа
        if not result.get("found") and result.get("error"):
            error_text = result["error"]
            if "AUTH_KEY_UNREGISTERED" in error_text or "AUTH_KEY_INVALID" in error_text:
                logger.warning(f"AUTH_KEY_UNREGISTERED в blacklist сессии для user {user_id}")
                await db.update_auth_status(user_id, "blacklist", False)
                await bot.send_message(
                    chat_id=chat_id,
                    text=(
                        "⚠️ <b>Сессия авторизации не найдена</b>\n\n"
                        "Telegram аннулировал сессию поиска в чёрном списке.\n"
                        "Пожалуйста, авторизуйтесь заново через меню \"👤 Мой аккаунт\"."
                    ),
                    parse_mode="HTML",
                )
                return

        if result["found"]:
            info = result.get("extracted_info", {})
            username_info = info.get("username", "—")
            phone = info.get("phone", "—")
            found_user_id = info.get("user_id", "—")
            raw_text = result.get("message_text", "") or ""

            match_type = result.get("match_type", "")
            match_labels = {
                "username": "по никнейму",
                "user_id": "по User ID (ник был сменён)",
                "fio": "по ФИО",
            }
            match_label = match_labels.get(match_type, "")

            header = (
                f"⚠️ <b>Пользователь найден в черном списке!</b>\n"
                f"<i>Найден {match_label}</i>\n\n"
                f"<b>Username:</b> {username_info}\n"
                f"<b>Телефон:</b> {phone}\n"
                f"<b>User ID:</b> {found_user_id}"
            )

            # Проверяем реальную длину финального сообщения (с HTML-тегами и экранированием)
            safe_text = html_module.escape(raw_text)
            single_msg = header + (f"\n\n<b>Текст записи:</b>\n<i>{safe_text}</i>" if safe_text else "")
            if len(single_msg) <= _TG_LIMIT:
                await bot.send_message(chat_id=chat_id, text=single_msg, parse_mode="HTML")
            else:
                await bot.send_message(chat_id=chat_id, text=header, parse_mode="HTML")
                if safe_text:
                    chunks = _split_text(safe_text)
                    total = len(chunks)
                    for i, chunk in enumerate(chunks):
                        label = f"<b>Текст записи [{i + 1}/{total}]:</b>\n" if total > 1 else "<b>Текст записи:</b>\n"
                        await bot.send_message(chat_id=chat_id, text=label + chunk, parse_mode="HTML")
                        if i < total - 1:
                            await asyncio.sleep(0.3)
        else:
            steps = result.get("steps_done", [])
            steps_text = ", ".join(steps) if steps else "—"
            if username:
                identity_line = f"<b>Username:</b> {username}\n"
            else:
                identity_line = f"<b>ФИО:</b> {fio}\n" if fio else ""
            text = (
                "✅ <b>Пользователь НЕ найден в черном списке</b>\n\n"
                f"{identity_line}"
                f"<b>Проверено:</b> {steps_text}\n"
                f"<b>Сообщений проверено:</b> {result.get('messages_checked', 0)}\n"
                f"<b>Чатов проверено:</b> {len(result.get('chats_checked', []))}"
            )
            await bot.send_message(chat_id=chat_id, text=text, parse_mode="HTML")

    except HTTPStatusError as e:
        detail = e.response.json().get("detail", "").lower()
        is_auth_error = any(kw in detail for kw in ["authkeyinvalid", "unauthorized", "not authorized"])
        if is_auth_error:
            await db.update_auth_status(user_id, "blacklist", False)
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "⚠️ <b>Авторизация оборвана</b>\n\n"
                    "Произошла ошибка со стороны Telegram.\n"
                    "Пожалуйста, авторизуйтесь заново через меню \"👤 Мой аккаунт\"."
                ),
                parse_mode="HTML",
            )
        else:
            await bot.send_message(chat_id=chat_id, text=f"❌ Ошибка проверки:\n\n{str(e)}")
    except Exception as e:
        logger.exception(f"Ошибка фонового поиска в ЧС для user {user_id}")
        is_auth_error = any(kw in str(e).lower() for kw in ["authkeyinvalid", "unauthorized"])
        if is_auth_error:
            await db.update_auth_status(user_id, "blacklist", False)
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    "⚠️ <b>Авторизация оборвана</b>\n\n"
                    "Произошла ошибка со стороны Telegram.\n"
                    "Пожалуйста, авторизуйтесь заново через меню \"👤 Мой аккаунт\"."
                ),
                parse_mode="HTML",
            )
        else:
            await bot.send_message(chat_id=chat_id, text=f"❌ Ошибка проверки:\n\n{str(e)}")
    finally:
        bot_data.get("blacklist_searching", set()).discard(user_id)


async def receive_username(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен username или кнопка FIO_ONLY — переходим к шагу ФИО"""
    text = update.message.text.strip()

    # Режим «только по ФИО» — нет Telegram аккаунта
    if text == BlacklistBtn.FIO_ONLY:
        context.user_data["bl_username"] = ""  # sentinel: FIO-only режим

        keyboard = ReplyKeyboardMarkup([
            [KeyboardButton(MenuButton.CANCEL)],
        ], resize_keyboard=True)

        await update.message.reply_text(
            "👤 <b>Поиск только по ФИО</b>\n\n"
            "Введите ФИО:\n"
            "<i>Иванов Иван Иванович</i>\n"
            "или <i>Иванов Иван</i>, или только <i>Иванов</i>",
            reply_markup=keyboard,
            parse_mode="HTML",
        )
        return BlacklistState.WAITING_FIO

    # Обычный режим — валидируем username
    valid, normalized_username, error = Validators.validate_username(text)
    if not valid:
        await update.message.reply_text(
            f"{error}\n\n"
            "Попробуйте ещё раз. Пример:\n"
            "<code>@username</code>",
            parse_mode="HTML"
        )
        return BlacklistState.WAITING_USERNAME

    context.user_data["bl_username"] = normalized_username

    keyboard = ReplyKeyboardMarkup([
        [KeyboardButton(BlacklistBtn.SKIP_FIO)],
        [KeyboardButton(MenuButton.CANCEL)],
    ], resize_keyboard=True)

    await update.message.reply_text(
        f"✅ Никнейм: <b>{normalized_username}</b>\n\n"
        "Введите ФИО для поиска:\n"
        "<i>Иванов Иван Иванович</i>\n"
        "или <i>Иванов Иван</i>, или только <i>Иванов</i>\n\n"
        "Если ФИО неизвестно — нажмите «⏩ Пропустить»",
        reply_markup=keyboard,
        parse_mode="HTML",
    )

    return BlacklistState.WAITING_FIO


async def receive_fio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен ФИО (или пропуск) — валидируем и запускаем поиск в ЧС в фоне"""
    text = update.message.text.strip()
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    # Проверяем, что сессия диалога существует (bl_username должен быть задан на шаге username)
    if "bl_username" not in context.user_data:
        await update.message.reply_text("❌ Ошибка сессии. Начните заново.")
        return ConversationHandler.END

    # Читаем без pop — pop сделаем только когда все проверки пройдены
    normalized_username = context.user_data["bl_username"]
    fio_only = not normalized_username  # "" → FIO-only режим

    # Обработка кнопки «Пропустить»
    if text == BlacklistBtn.SKIP_FIO:
        if fio_only:
            # В FIO-only режиме пользователь не должен видеть эту кнопку,
            # но защищаемся на случай ручного ввода текста
            await update.message.reply_text(
                "❌ В режиме поиска по ФИО необходимо ввести хотя бы фамилию.\n\n"
                "Введите ФИО:\n"
                "<i>Иванов Иван Иванович</i>",
                parse_mode="HTML",
            )
            return BlacklistState.WAITING_FIO  # bl_username остаётся в context
        fio = None
    else:
        # Валидируем ФИО в обоих режимах
        valid, normalized_fio, error = Validators.validate_fio(text)
        if not valid:
            skip_hint = "" if fio_only else "\n\nЕсли ФИО неизвестно — нажмите «⏩ Пропустить»"
            await update.message.reply_text(
                f"{error}{skip_hint}",
                parse_mode="HTML",
            )
            return BlacklistState.WAITING_FIO  # bl_username остаётся в context
        fio = normalized_fio

    # Все проверки пройдены — извлекаем из context
    context.user_data.pop("bl_username")

    workers_api: WorkersAPI = context.bot_data["workers_api"]
    db: DatabaseService = context.bot_data["db"]
    blacklist_session_path = f"/app/sessions/{user_id}_blacklist"

    # Проверяем, не идёт ли уже поиск для этого пользователя
    searching: set = context.bot_data.setdefault("blacklist_searching", set())
    if user_id in searching:
        await update.message.reply_text(
            "⏳ <b>Проверка уже выполняется</b>\n\n"
            "Одновременно по чёрному списку может проверяться только один пользователь.\n"
            "Пожалуйста, подождите завершения поиска и введите запрос повторно.",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    searching.add(user_id)

    back_keyboard = ReplyKeyboardMarkup([
        [KeyboardButton(BlacklistBtn.CHECK)],
        [KeyboardButton(MenuButton.BACK)],
    ], resize_keyboard=True)

    # Формируем сообщение о запуске в зависимости от режима
    if fio_only:
        search_info = f"<b>ФИО:</b> {fio}"
    else:
        fio_line = f"\n<b>ФИО:</b> {fio}" if fio else ""
        search_info = f"<b>Никнейм:</b> {normalized_username}{fio_line}"

    await update.message.reply_text(
        f"🔍 Поиск запущен:\n"
        f"{search_info}\n\n"
        "⏳ <i>Результат придёт автоматически — можете пользоваться ботом.</i>",
        reply_markup=back_keyboard,
        parse_mode="HTML",
    )

    # Для API: пустая строка → None
    api_username = normalized_username or None

    asyncio.create_task(_blacklist_search_task(
        bot=context.bot,
        chat_id=chat_id,
        user_id=user_id,
        username=api_username,
        normalized_username=api_username,
        fio=fio,
        workers_api=workers_api,
        db=db,
        blacklist_session_path=blacklist_session_path,
        bot_data=context.bot_data,
    ))

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
        logger.exception("Ошибка получения чатов ЧС")
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

    # Путь к blacklist-сессии в контексте workers-service контейнера
    blacklist_session_path = f"/app/sessions/{user_id}_blacklist"

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
            await workers_api.add_blacklist_chat(normalized_username, chat_title=chat_title)
            await status_msg.edit_text(
                f"✅ Чат {normalized_username} добавлен в черный список!"
            )
            return ConversationHandler.END

    except HTTPStatusError as e:
        detail = e.response.json().get("detail", "").lower()
        is_auth_error = any(kw in detail for kw in ["authkeyinvalid", "unauthorized", "not authorized"])
        if is_auth_error:
            logger.warning(f"Обнаружен обрыв авторизации blacklist для user {user_id}")
            db: DatabaseService = context.bot_data["db"]
            await db.update_auth_status(user_id, "blacklist", False)
            context.user_data.clear()
            await status_msg.edit_text(
                "⚠️ <b>Авторизация оборвана</b>\n\n"
                "Произошла ошибка со стороны Telegram.\n"
                "Пожалуйста, авторизуйтесь заново через меню \"👤 Мой аккаунт\".",
                parse_mode="HTML"
            )
        else:
            await status_msg.edit_text(f"❌ Ошибка:\n\n{str(e)}")
        return ConversationHandler.END
    except Exception as e:
        logger.exception(f"Ошибка добавления чата в ЧС для user {user_id}")
        is_auth_error = any(kw in str(e).lower() for kw in ["authkeyinvalid", "unauthorized"])
        if is_auth_error:
            logger.warning(f"Обнаружен обрыв авторизации blacklist для user {user_id}")
            db: DatabaseService = context.bot_data["db"]
            await db.update_auth_status(user_id, "blacklist", False)
            context.user_data.clear()
            await status_msg.edit_text(
                "⚠️ <b>Авторизация оборвана</b>\n\n"
                "Произошла ошибка со стороны Telegram.\n"
                "Пожалуйста, авторизуйтесь заново через меню \"👤 Мой аккаунт\".",
                parse_mode="HTML"
            )
        else:
            await status_msg.edit_text(f"❌ Ошибка:\n\n{str(e)}")
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
            await workers_api.add_blacklist_chat(chat_username, chat_title=chat_title)
            await query.edit_message_text(
                f"✅ Чат {chat_username} добавлен в черный список!\n"
                f"(все топики)"
            )
        except Exception as e:
            logger.exception("Ошибка добавления чата в ЧС")
            await query.edit_message_text(f"❌ Ошибка:\n\n{str(e)}")

    elif data.startswith(BlacklistCB.SELECT_TOPIC):
        # Пользователь выбрал конкретный топик
        topic_id = int(data[len(BlacklistCB.SELECT_TOPIC):])
        topics_map = context.user_data.get("bl_chat_topics", {})
        topic_name = topics_map.get(topic_id, f"Topic {topic_id}")

        try:
            await workers_api.add_blacklist_chat(
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
            logger.exception("Ошибка добавления чата в ЧС")
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
        await workers_api.remove_blacklist_chat(chat_username, topic_id=topic_id)
        await query.answer(f"✅ Чат {chat_username} удалён")
        await show_manage_chats(update, context)

    except Exception as e:
        logger.exception("Ошибка удаления чата из ЧС")
        await query.answer("❌ Ошибка удаления", show_alert=True)


async def cancel_blacklist(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """ операции"""
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Операция отменена.")
    else:
        await update.message.reply_text("❌ Операция отменена.")
    # Очищаем user_data
    context.user_data.pop("bl_add_chat_username", None)
    context.user_data.pop("bl_add_chat_title", None)
    return ConversationHandler.END


def register_blacklist_handlers(app):
    """Регистрация обработчиков черного списка"""
    # Inline callback: возврат в меню из callback_query
    app.add_handler(
        CallbackQueryHandler(show_blacklist_menu, pattern=f"^{BlacklistCB.BLACKLIST_MENU}$|^blacklist$")
    )

    # ConversationHandler для проверки пользователя (Reply-кнопки)
    check_user_conv = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex(f"^{BlacklistBtn.CHECK}$"), start_check_user),
        ],
        states={
            BlacklistState.WAITING_USERNAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, receive_username)
            ],
            BlacklistState.WAITING_FIO: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, receive_fio)
            ],
        },
        fallbacks=[
            CommandHandler("start", cancel_and_return_to_menu),
            MessageHandler(MAIN_MENU_FILTER, cancel_and_return_to_menu),
        ],
        conversation_timeout=300,
        allow_reentry=True,
    )
    app.add_handler(check_user_conv)
