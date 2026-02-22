"""Административная панель"""
import re
from datetime import datetime, timezone
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
)
from loguru import logger

from parserhub.config import config
from parserhub.db_service import DatabaseService
from parserhub.services.subscription_service import SubscriptionService
from parserhub.handlers.start import MAIN_MENU_FILTER


class AdminCB:
    MENU = "admin_menu"
    SUBSCRIPTIONS = "admin_subs"
    GRANT_SUB = "admin_grant"
    GRANT_PLAN = "admin_gplan_"  # + plan
    REVENUE = "admin_revenue"
    ADMINS_LIST = "admin_list"
    ADD_ADMIN = "admin_add"
    REMOVE_ADMIN = "admin_rm_"  # + user_id
    PVZ_CHATS = "admin_pvz_chats"  # Управление чатами ПВЗ
    PVZ_CHATS_EDIT = "admin_pvz_edit"
    PVZ_CHATS_CLEAR = "admin_pvz_clear"
    PVZ_CHATS_CLEAR_OK = "admin_pvz_clear_ok"
    BLACKLIST_CHATS = "admin_bl_chats"  # Управление чатами ЧС
    BL_CHATS_EDIT = "admin_bl_edit"
    BL_CHATS_CLEAR = "admin_bl_clear"
    BL_CHATS_CLEAR_OK = "admin_bl_clear_ok"
    MANAGE_PRICES = "admin_prices"
    EDIT_PRICE = "admin_edit_price_"  # + plan
    PROXY_SETTINGS = "admin_proxy"  # Настройки прокси парсера
    PROXY_CHANGE = "admin_proxy_change"
    PROXY_DELETE = "admin_proxy_delete"
    PROXY_DELETE_CONFIRM = "admin_proxy_delete_confirm"
    PROXY_RESTART = "admin_proxy_restart"
    PROXY_RESTART_CONFIRM = "admin_proxy_restart_confirm"
    REVOKE_SUB = "admin_revoke"
    SUBS_PAGE = "admin_subs_p_"  # + page number
    NOOP = "admin_noop"
    CLOSE = "admin_close"


class AdminState:
    INPUT_USER_FOR_SUB = 1
    SELECT_PLAN = 2
    INPUT_USER_FOR_ADMIN = 3
    INPUT_PVZ_CHATS = 4
    INPUT_BLACKLIST_CHATS = 5
    INPUT_NEW_PRICE = 6
    INPUT_PROXY_STRING = 7
    INPUT_PROXY_CHANGE_URL = 8
    PROXY_CONFIRM_DELETE = 9
    PROXY_CONFIRM_RESTART = 10
    PVZ_CHATS_MENU = 11
    CONFIRM_CLEAR_PVZ = 12
    BL_CHATS_MENU = 13
    CONFIRM_CLEAR_BL = 14
    INPUT_USER_FOR_REVOKE = 15
    CONFIRM_REVOKE = 16


async def _is_admin(user_id: int, db: DatabaseService) -> bool:
    """Проверка: мастер-админ (из .env) или добавленный админ (из БД)"""
    if user_id == config.ADMIN_ID:
        return True
    return await db.is_admin(user_id)


# ===== Главное меню админки =====

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /admin"""
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    if not await _is_admin(user_id, db):
        await update.message.reply_text("Нет доступа.")
        return

    await _show_admin_menu(update, context)


async def admin_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Возврат в меню админки по кнопке"""
    user_id = update.effective_user.id
    db: DatabaseService = context.bot_data["db"]

    if not await _is_admin(user_id, db):
        await update.callback_query.answer("Нет доступа.")
        return

    await _show_admin_menu(update, context)


async def _show_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать меню админки"""
    keyboard = [
        [InlineKeyboardButton("📋 Подписки", callback_data=AdminCB.SUBSCRIPTIONS)],
        [InlineKeyboardButton("🎁 Выдать подписку", callback_data=AdminCB.GRANT_SUB)],
        [InlineKeyboardButton("❌ Аннулировать подписку", callback_data=AdminCB.REVOKE_SUB)],
        [InlineKeyboardButton("💰 Цены подписок", callback_data=AdminCB.MANAGE_PRICES)],
        [InlineKeyboardButton("💰 Доходы", callback_data=AdminCB.REVENUE)],
        [InlineKeyboardButton("👥 Администраторы", callback_data=AdminCB.ADMINS_LIST)],
        [InlineKeyboardButton("📝 Чаты ПВЗ", callback_data=AdminCB.PVZ_CHATS)],
        [InlineKeyboardButton("📝 Чаты ЧС", callback_data=AdminCB.BLACKLIST_CHATS)],
        [InlineKeyboardButton("🌐 Настройки прокси", callback_data=AdminCB.PROXY_SETTINGS)],
        [InlineKeyboardButton("✖ Закрыть", callback_data=AdminCB.CLOSE)],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    text = "🔧 <b>Панель администратора</b>\n\nВыберите раздел:"

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )
    else:
        await update.message.reply_text(
            text=text, reply_markup=reply_markup, parse_mode="HTML"
        )


# ===== Список подписок =====

_SUBS_PAGE_SIZE = 20


def _build_subs_page(
    subs: list, trials: list, page: int
) -> tuple[str, InlineKeyboardMarkup]:
    """Построить одну страницу списка активных пользователей."""
    # Объединяем: сначала платные, затем пробные
    all_entries = [{**s, "type": "sub"} for s in subs] + \
                  [{**t, "type": "trial"} for t in trials]

    total = len(all_entries)
    total_pages = max(1, (total + _SUBS_PAGE_SIZE - 1) // _SUBS_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    now = datetime.now(timezone.utc)
    page_entries = all_entries[page * _SUBS_PAGE_SIZE: (page + 1) * _SUBS_PAGE_SIZE]

    header = (
        f"📋 <b>Все активные пользователи</b> ({total})\n"
        f"💳 Платные: {len(subs)}   🎁 Пробный: {len(trials)}\n"
    )
    lines = []
    for e in page_entries:
        name = e.get("username") or e.get("full_name") or "?"
        if e["type"] == "sub":
            until = datetime.fromisoformat(e["active_until"]).replace(tzinfo=timezone.utc)
            remaining = until - now
            lines.append(
                f"• 💳 <code>{e['user_id']}</code> @{name} — "
                f"{e['plan']} (ост. {remaining.days}д {remaining.seconds // 3600}ч)"
            )
        else:
            until = datetime.fromisoformat(e["trial_until"]).replace(tzinfo=timezone.utc)
            remaining = until - now
            lines.append(
                f"• 🎁 <code>{e['user_id']}</code> @{name} "
                f"(ост. {remaining.days}д {remaining.seconds // 3600}ч)"
            )

    if not lines:
        lines.append("Нет активных пользователей.")

    text = header + "\n" + "\n".join(lines)

    # Навигация
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀", callback_data=f"{AdminCB.SUBS_PAGE}{page - 1}"))
    nav.append(InlineKeyboardButton(f"{page + 1}/{total_pages}", callback_data=AdminCB.NOOP))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶", callback_data=f"{AdminCB.SUBS_PAGE}{page + 1}"))

    keyboard = []
    if total_pages > 1:
        keyboard.append(nav)
    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=AdminCB.MENU)])

    return text, InlineKeyboardMarkup(keyboard)


async def show_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список всех активных подписок и пробных периодов (страница 0)"""
    query = update.callback_query
    await query.answer()

    service: SubscriptionService = context.bot_data["subscription"]
    subs = await service.get_all_active()
    trials = await service.get_all_trial_active()

    text, markup = _build_subs_page(subs, trials, page=0)
    await query.edit_message_text(text=text, reply_markup=markup, parse_mode="HTML")


async def show_subscriptions_page(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Переход на страницу N списка подписок"""
    query = update.callback_query
    await query.answer()

    page = int(query.data.replace(AdminCB.SUBS_PAGE, ""))

    service: SubscriptionService = context.bot_data["subscription"]
    subs = await service.get_all_active()
    trials = await service.get_all_trial_active()

    text, markup = _build_subs_page(subs, trials, page=page)
    await query.edit_message_text(text=text, reply_markup=markup, parse_mode="HTML")


# ===== Выдать подписку =====

async def grant_sub_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало: запрос user_id"""
    query = update.callback_query
    await query.answer()

    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="admin_conv_cancel")]]
    await query.edit_message_text(
        "🎁 <b>Выдать подписку</b>\n\n"
        "Введите Telegram ID пользователя:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return AdminState.INPUT_USER_FOR_SUB


async def grant_sub_receive_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен user_id — показать выбор тарифа"""
    text = update.message.text.strip()

    try:
        user_id = int(text)
    except ValueError:
        await update.message.reply_text("Введите числовой Telegram ID.")
        return AdminState.INPUT_USER_FOR_SUB

    context.user_data["admin_grant_user_id"] = user_id

    keyboard = [
        [InlineKeyboardButton("1 день", callback_data=f"{AdminCB.GRANT_PLAN}day")],
        [InlineKeyboardButton("7 дней", callback_data=f"{AdminCB.GRANT_PLAN}week")],
        [InlineKeyboardButton("30 дней", callback_data=f"{AdminCB.GRANT_PLAN}month")],
        [InlineKeyboardButton("❌ Отмена", callback_data="admin_conv_cancel")],
    ]

    await update.message.reply_text(
        f"Пользователь: <code>{user_id}</code>\n\nВыберите тариф:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return AdminState.SELECT_PLAN


async def grant_sub_select_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выбран тариф — активировать подписку"""
    query = update.callback_query
    await query.answer()

    plan = query.data.replace(AdminCB.GRANT_PLAN, "")
    user_id = context.user_data.get("admin_grant_user_id")

    service: SubscriptionService = context.bot_data["subscription"]

    try:
        await service.activate(user_id, plan)
        plans = await service.get_plans()
        plan_label = plans.get(plan, {}).get("label", plan)

        await query.edit_message_text(
            f"✅ Подписка выдана!\n\n"
            f"Пользователь: <code>{user_id}</code>\n"
            f"Тариф: {plan_label}",
            parse_mode="HTML",
        )
        logger.info(f"Admin granted subscription: user={user_id}, plan={plan}")
    except Exception as e:
        logger.exception("Ошибка выдачи подписки")
        await query.edit_message_text(f"❌ Ошибка: {e}")

    return ConversationHandler.END


# ===== Аннулировать подписку =====

async def revoke_sub_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало: запрос user_id для аннулирования"""
    query = update.callback_query
    await query.answer()

    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="admin_conv_cancel")]]
    await query.edit_message_text(
        "❌ <b>Аннулировать подписку</b>\n\n"
        "Введите Telegram ID пользователя:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return AdminState.INPUT_USER_FOR_REVOKE


async def revoke_sub_receive_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен user_id — показать текущий статус и запросить подтверждение"""
    text = update.message.text.strip()

    try:
        user_id = int(text)
    except ValueError:
        await update.message.reply_text("Введите числовой Telegram ID.")
        return AdminState.INPUT_USER_FOR_REVOKE

    db: DatabaseService = context.bot_data["db"]
    if not await db.get_user(user_id):
        await update.message.reply_text(
            f"❌ Пользователь <code>{user_id}</code> не найден в базе данных.",
            parse_mode="HTML",
        )
        return AdminState.INPUT_USER_FOR_REVOKE

    context.user_data["admin_revoke_user_id"] = user_id

    service: SubscriptionService = context.bot_data["subscription"]
    sub = await service.get_info(user_id)
    trial = await service.get_trial_info(user_id)

    now = datetime.now(timezone.utc)
    status_lines = []
    if sub:
        until = datetime.fromisoformat(sub["active_until"]).replace(tzinfo=timezone.utc)
        if until > now:
            remaining = until - now
            status_lines.append(f"💳 Платная подписка: {sub['plan']} (ост. {remaining.days}д {remaining.seconds // 3600}ч)")
        else:
            status_lines.append("💳 Платная подписка: истекла")
    if trial and trial["is_active"]:
        trial_until = datetime.fromisoformat(trial["trial_until"]).replace(tzinfo=timezone.utc)
        remaining = trial_until - now
        status_lines.append(f"🎁 Пробный период: ост. {remaining.days}д {remaining.seconds // 3600}ч")

    if not status_lines:
        status_lines.append("нет активного доступа")

    status_text = "\n".join(status_lines)

    keyboard = [
        [InlineKeyboardButton("✅ Да, аннулировать", callback_data="admin_revoke_ok")],
        [InlineKeyboardButton("❌ Отмена", callback_data="admin_conv_cancel")],
    ]
    await update.message.reply_text(
        f"Пользователь: <code>{user_id}</code>\n"
        f"Текущий статус:\n{status_text}\n\n"
        "Аннулировать подписку и пробный период?",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return AdminState.CONFIRM_REVOKE


async def revoke_sub_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Подтверждение — аннулировать"""
    query = update.callback_query
    await query.answer()

    user_id = context.user_data.get("admin_revoke_user_id")
    if user_id is None:
        await query.edit_message_text(
            "❌ Ошибка: ID пользователя утерян (возможно, бот перезапускался). "
            "Начните заново через меню администратора."
        )
        return ConversationHandler.END

    service: SubscriptionService = context.bot_data["subscription"]

    try:
        had_access = await service.revoke(user_id)
        if had_access:
            await query.edit_message_text(
                f"✅ Подписка аннулирована!\n\n"
                f"Пользователь <code>{user_id}</code> лишён доступа.",
                parse_mode="HTML",
            )
            logger.info(f"Admin revoked subscription: user={user_id}")
        else:
            await query.edit_message_text(
                f"ℹ️ У пользователя <code>{user_id}</code> не было активной подписки или пробного периода.",
                parse_mode="HTML",
            )
            logger.info(f"Admin revoke: user={user_id} had no active access")
    except Exception as e:
        logger.exception("Ошибка аннулирования подписки")
        await query.edit_message_text(f"❌ Ошибка: {e}")

    return ConversationHandler.END


# ===== Доходы =====

async def show_revenue(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика доходов"""
    query = update.callback_query
    await query.answer()

    db: DatabaseService = context.bot_data["db"]
    stats = await db.get_revenue_stats()
    service: SubscriptionService = context.bot_data["subscription"]
    subs = await service.get_all_active()
    trials = await service.get_all_trial_active()

    text = (
        "💰 <b>Доходы</b>\n\n"
        f"<b>Сегодня:</b> {stats['today_amount'] / 100:.0f} RUB ({stats['today_count']} оплат)\n"
        f"<b>Этот месяц:</b> {stats['month_amount'] / 100:.0f} RUB ({stats['month_count']} оплат)\n"
        f"<b>Всего:</b> {stats['total_amount'] / 100:.0f} RUB ({stats['total_count']} оплат)\n\n"
        f"<b>Активных подписок:</b> {len(subs)}\n"
        f"<b>На пробном периоде:</b> {len(trials)}\n"
        f"<b>Итого пользователей:</b> {len(subs) + len(trials)}"
    )

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=AdminCB.MENU)]]
    await query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


# ===== Администраторы =====

async def show_admins(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список администраторов"""
    query = update.callback_query
    await query.answer()

    db: DatabaseService = context.bot_data["db"]
    admins = await db.get_admins()

    lines = [f"• <code>{config.ADMIN_ID}</code> (владелец)"]
    keyboard_rows = []

    for a in admins:
        username_str = f" @{a['username']}" if a.get('username') else ""
        lines.append(f"• <code>{a['user_id']}</code>{username_str}")
        btn_label = f"❌ Удалить @{a['username']}" if a.get('username') else f"❌ Удалить {a['user_id']}"
        keyboard_rows.append([
            InlineKeyboardButton(
                btn_label,
                callback_data=f"{AdminCB.REMOVE_ADMIN}{a['user_id']}"
            )
        ])

    text = "👥 <b>Администраторы</b>\n\n" + "\n".join(lines)

    keyboard_rows.append([InlineKeyboardButton("➕ Добавить", callback_data=AdminCB.ADD_ADMIN)])
    keyboard_rows.append([InlineKeyboardButton("🔙 Назад", callback_data=AdminCB.MENU)])

    await query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard_rows),
        parse_mode="HTML",
    )


async def remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Удалить администратора"""
    query = update.callback_query
    admin_id = int(query.data.replace(AdminCB.REMOVE_ADMIN, ""))

    db: DatabaseService = context.bot_data["db"]
    await db.remove_admin(admin_id)

    await query.answer(f"Админ {admin_id} удалён")
    logger.info(f"Admin removed: {admin_id}")
    await show_admins(update, context)


# ===== Добавить администратора =====

async def add_admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало: запрос user_id нового админа"""
    query = update.callback_query
    await query.answer()

    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="admin_conv_cancel")]]
    await query.edit_message_text(
        "➕ <b>Добавить администратора</b>\n\n"
        "Введите @username или Telegram ID:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return AdminState.INPUT_USER_FOR_ADMIN


async def add_admin_receive_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен @username или user_id — добавить админа"""
    text = update.message.text.strip()
    db: DatabaseService = context.bot_data["db"]

    if text.startswith("@"):
        # Ввод по username — ищем в таблице users
        username = text.lstrip("@")
        user = await db.get_user_by_username(username)
        if not user:
            await update.message.reply_text(
                f"❌ Пользователь @{username} не найден.\n\n"
                "Попросите его написать /start боту.",
                parse_mode="HTML",
            )
            return ConversationHandler.END
        new_admin_id = user.user_id
    else:
        try:
            new_admin_id = int(text)
        except ValueError:
            await update.message.reply_text("Введите @username или числовой Telegram ID.")
            return AdminState.INPUT_USER_FOR_ADMIN

    # Проверка: владелец бота?
    if new_admin_id == config.ADMIN_ID:
        await update.message.reply_text(
            "ℹ️ Этот пользователь — владелец бота, уже имеет полный доступ."
        )
        return ConversationHandler.END

    # Проверка: уже админ?
    if await db.is_admin(new_admin_id):
        await update.message.reply_text(
            "⚠️ Этот пользователь уже является администратором."
        )
        return ConversationHandler.END

    await db.add_admin(new_admin_id, added_by=update.effective_user.id)

    await update.message.reply_text(
        f"✅ Администратор <code>{new_admin_id}</code> добавлен.",
        parse_mode="HTML",
    )
    logger.info(f"Admin added: {new_admin_id} by {update.effective_user.id}")
    return ConversationHandler.END


# ===== Закрыть / Отмена =====

async def close_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Закрыть панель"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("Панель администратора закрыта.")


async def cancel_admin_conv(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Отмена внутри ConversationHandler (поддерживает как CallbackQuery, так и Message)"""
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Отменено.")
    else:
        await update.message.reply_text("❌ Операция отменена.")
    return ConversationHandler.END


# ===== Управление чатами ПВЗ =====

async def _manage_chats_menu(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    db_key: str,
    title: str,
    edit_cb: str,
    clear_cb: str,
    return_state: int,
) -> int:
    """Универсальное меню управления списком чатов"""
    query = update.callback_query
    await query.answer()

    db: DatabaseService = context.bot_data["db"]
    current_chats = await db.get_global_chats(db_key)
    chats_text = "\n".join([f"• {chat}" for chat in current_chats]) if current_chats else "Нет настроенных чатов"

    keyboard = [
        [InlineKeyboardButton("✏️ Изменить список", callback_data=edit_cb)],
        [InlineKeyboardButton("🗑 Очистить список", callback_data=clear_cb)],
        [InlineKeyboardButton("❌ Закрыть", callback_data="admin_conv_cancel")],
    ]
    await query.edit_message_text(
        text=f"📝 <b>{title}</b>\n\n<b>Текущие чаты:</b>\n{chats_text}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return return_state


async def manage_pvz_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Меню управления чатами ПВЗ"""
    return await _manage_chats_menu(
        update, context,
        db_key="pvz_monitoring_chats",
        title="Чаты для мониторинга ПВЗ",
        edit_cb=AdminCB.PVZ_CHATS_EDIT,
        clear_cb=AdminCB.PVZ_CHATS_CLEAR,
        return_state=AdminState.PVZ_CHATS_MENU,
    )


async def pvz_chats_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запросить новый список чатов ПВЗ"""
    query = update.callback_query
    await query.answer()

    db: DatabaseService = context.bot_data["db"]
    current_chats = await db.get_global_chats('pvz_monitoring_chats')
    chats_text = "\n".join([f"• {chat}" for chat in current_chats]) if current_chats else "Нет настроенных чатов"

    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="admin_conv_cancel")]]
    await query.edit_message_text(
        "✏️ <b>Изменить чаты ПВЗ</b>\n\n"
        f"<b>Текущие:</b>\n{chats_text}\n\n"
        "Введите новый список (по одному в строке).\n"
        "Для топика используйте формат <code>@chat/topic_id</code>:\n"
        "<code>@pvz_zamena\n@pvz_jobs\n@pvz_zamena/912</code>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    return AdminState.INPUT_PVZ_CHATS


async def _chats_clear_confirm(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    title: str,
    description: str,
    confirm_cb: str,
    return_state: int,
) -> int:
    """Универсальный диалог подтверждения очистки чатов"""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("✅ Да, очистить", callback_data=confirm_cb)],
        [InlineKeyboardButton("❌ Отмена", callback_data="admin_conv_cancel")],
    ]
    await query.edit_message_text(
        f"🗑 <b>{title}</b>\n\n{description}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return return_state


async def pvz_chats_clear_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запросить подтверждение очистки чатов ПВЗ"""
    return await _chats_clear_confirm(
        update, context,
        title="Очистить чаты ПВЗ",
        description="Вы уверены? Список чатов для мониторинга ПВЗ будет полностью очищен.",
        confirm_cb=AdminCB.PVZ_CHATS_CLEAR_OK,
        return_state=AdminState.CONFIRM_CLEAR_PVZ,
    )


async def pvz_chats_clear_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Очистить список чатов ПВЗ"""
    query = update.callback_query
    await query.answer()

    db: DatabaseService = context.bot_data["db"]
    await db.set_global_chats('pvz_monitoring_chats', [])

    await query.edit_message_text(
        "✅ <b>Список чатов ПВЗ очищен.</b>",
        parse_mode="HTML"
    )
    logger.info(f"Admin {query.from_user.id} cleared PVZ monitoring chats")
    return ConversationHandler.END


async def receive_pvz_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен список чатов ПВЗ"""
    chats_text = update.message.text.strip()
    chats = [line.strip() for line in chats_text.split("\n") if line.strip()]

    # Нормализация и дедупликация чатов
    seen = {}
    for chat in chats:
        chat = chat.strip()
        if not chat.startswith("@"):
            chat = f"@{chat}"
        seen[chat.lower()] = chat  # ключ — нижний регистр, значение — оригинал
    normalized_chats = list(seen.values())

    db: DatabaseService = context.bot_data["db"]
    await db.set_global_chats('pvz_monitoring_chats', normalized_chats)

    chats_list = "\n".join([f"• {chat}" for chat in normalized_chats])

    await update.message.reply_text(
        f"✅ <b>Чаты ПВЗ обновлены!</b>\n\n"
        f"Сохранено {len(normalized_chats)} чатов:\n{chats_list}",
        parse_mode="HTML"
    )

    logger.info(f"Admin {update.effective_user.id} updated PVZ chats: {normalized_chats}")
    return ConversationHandler.END


# ===== Управление чатами ЧС =====

async def manage_blacklist_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Меню управления чатами ЧС"""
    return await _manage_chats_menu(
        update, context,
        db_key="blacklist_chats",
        title="Чаты для черного списка",
        edit_cb=AdminCB.BL_CHATS_EDIT,
        clear_cb=AdminCB.BL_CHATS_CLEAR,
        return_state=AdminState.BL_CHATS_MENU,
    )


async def bl_chats_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запросить новый список чатов ЧС"""
    query = update.callback_query
    await query.answer()

    db: DatabaseService = context.bot_data["db"]
    current_chats = await db.get_global_chats('blacklist_chats')
    chats_text = "\n".join([f"• {chat}" for chat in current_chats]) if current_chats else "Нет настроенных чатов"

    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="admin_conv_cancel")]]
    await query.edit_message_text(
        "✏️ <b>Изменить чаты ЧС</b>\n\n"
        f"<b>Текущие:</b>\n{chats_text}\n\n"
        "Введите новый список (по одному в строке):\n"
        "<code>@blacklist_pvz\n@scam_reports</code>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML"
    )
    return AdminState.INPUT_BLACKLIST_CHATS


async def bl_chats_clear_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запросить подтверждение очистки чатов ЧС"""
    return await _chats_clear_confirm(
        update, context,
        title="Очистить чаты ЧС",
        description="Вы уверены? Список чатов черного списка будет полностью очищен.",
        confirm_cb=AdminCB.BL_CHATS_CLEAR_OK,
        return_state=AdminState.CONFIRM_CLEAR_BL,
    )


async def bl_chats_clear_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Очистить список чатов ЧС"""
    query = update.callback_query
    await query.answer()

    db: DatabaseService = context.bot_data["db"]
    await db.set_global_chats('blacklist_chats', [])

    # Синхронизируем очистку с workers_service
    from parserhub.api_client import WorkersAPI
    workers_api: WorkersAPI = context.bot_data["workers_api"]
    try:
        await workers_api.sync_blacklist_chats([])
    except Exception as e:
        logger.exception("Ошибка синхронизации очистки чатов ЧС с workers_service")

    await query.edit_message_text(
        "✅ <b>Список чатов ЧС очищен.</b>",
        parse_mode="HTML"
    )
    logger.info(f"Admin {query.from_user.id} cleared blacklist chats")
    return ConversationHandler.END


async def receive_blacklist_chats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен список чатов ЧС"""
    chats_text = update.message.text.strip()
    chats = [line.strip() for line in chats_text.split("\n") if line.strip()]

    # Нормализация и дедупликация чатов
    seen = {}
    for chat in chats:
        chat = chat.strip()
        if not chat.startswith("@"):
            chat = f"@{chat}"
        seen[chat.lower()] = chat
    normalized_chats = list(seen.values())

    db: DatabaseService = context.bot_data["db"]
    await db.set_global_chats('blacklist_chats', normalized_chats)

    # Синхронизируем с workers_service
    # Парсим "@chat/topic_id" → отдельные поля для корректного хранения в БД
    from parserhub.api_client import WorkersAPI
    workers_api: WorkersAPI = context.bot_data["workers_api"]
    sync_chats = []
    for chat in normalized_chats:
        if '/' in chat:
            parts = chat.rsplit('/', 1)
            try:
                sync_chats.append({"chat_username": parts[0], "topic_id": int(parts[1])})
            except ValueError:
                sync_chats.append({"chat_username": chat})
        else:
            sync_chats.append({"chat_username": chat})
    try:
        await workers_api.sync_blacklist_chats(sync_chats)
    except Exception as e:
        logger.exception("Ошибка синхронизации чатов ЧС с workers_service")

    chats_list = "\n".join([f"• {chat}" for chat in normalized_chats])

    await update.message.reply_text(
        f"✅ <b>Чаты ЧС обновлены!</b>\n\n"
        f"Сохранено {len(normalized_chats)} чатов:\n{chats_list}",
        parse_mode="HTML"
    )

    logger.info(f"Admin {update.effective_user.id} updated blacklist chats: {normalized_chats}")
    return ConversationHandler.END


# ===== Настройки прокси =====

# Поддерживаемые форматы прокси
_PROXY_FORMATS_HELP = (
    "Поддерживаемые форматы <code>proxy_string</code>:\n"
    "• <code>user:pass@host:port</code>\n"
    "• <code>host:port@user:pass</code>\n"
    "• <code>user:pass:host:port</code>\n"
    "• <code>host:port:user:pass</code>\n\n"
    "Пример: <code>NAr4CY:mypass@proxy.example.com:1234</code>\n\n"
    "<code>proxy_change_url</code> — URL для ротации IP (оставьте пустым если не нужен):\n"
    "Пример: <code>https://changeip.mobileproxy.space/?proxy_key=abc123</code>"
)

_PROXY_RE = re.compile(
    r"^(?:https?://)?"               # опциональный протокол
    r"("
    r"[\w\-\.]+:[\w\-\.]+@[\w\-\.]+:\d+"  # user:pass@host:port
    r"|[\w\-\.]+:\d+@[\w\-\.]+:[\w\-\.]+"  # host:port@user:pass
    r"|[\w\-\.]+:[\w\-\.]+:[\w\-\.]+:\d+"  # user:pass:host:port
    r"|[\w\-\.]+:\d+:[\w\-\.]+:[\w\-\.]+"  # host:port:user:pass
    r")$"
)


async def proxy_settings_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Меню настроек прокси — показать текущие настройки и варианты действий"""
    query = update.callback_query
    await query.answer()

    from parserhub.api_client import RealtyAPI
    realty_api: RealtyAPI = context.bot_data["realty_api"]

    try:
        current = await realty_api.get_proxy()
        current_proxy = current.get("proxy_string", "") or "не задан"
        current_url = current.get("proxy_change_url", "") or "не задан"
    except Exception as e:
        logger.warning(f"Сервис недвижимости недоступен при запросе прокси: {e}")
        current_proxy = "⚠️ сервис недоступен"
        current_url = "⚠️ сервис недоступен"

    masked = re.sub(r"(:\S+?)(@)", r":***\2", current_proxy) if current_proxy not in ("не задан", "⚠️ сервис недоступен") else current_proxy

    text = (
        "🌐 <b>Настройки прокси парсера недвижимости</b>\n\n"
        f"<b>Proxy:</b> <code>{masked}</code>\n"
        f"<b>Смена IP:</b> <code>{current_url}</code>"
    )

    keyboard = [
        [InlineKeyboardButton("✏️ Изменить прокси", callback_data=AdminCB.PROXY_CHANGE)],
        [InlineKeyboardButton("🗑 Удалить прокси", callback_data=AdminCB.PROXY_DELETE)],
        [InlineKeyboardButton("🔄 Перезапуск сервиса недвижимости", callback_data=AdminCB.PROXY_RESTART)],
        [InlineKeyboardButton("❌ Закрыть", callback_data="admin_conv_cancel")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="HTML")
    return AdminState.PROXY_CONFIRM_RESTART  # Ждём нажатия одной из кнопок меню


async def proxy_change_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начать ввод нового proxy_string"""
    query = update.callback_query
    await query.answer()

    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="admin_conv_cancel")]]
    await query.edit_message_text(
        "✏️ <b>Изменение прокси</b>\n\n"
        f"{_PROXY_FORMATS_HELP}\n\n"
        "Введите новый <b>proxy_string</b>:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return AdminState.INPUT_PROXY_STRING


async def proxy_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запросить подтверждение удаления прокси"""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("✅ Да, удалить", callback_data=AdminCB.PROXY_DELETE_CONFIRM)],
        [InlineKeyboardButton("❌ Отмена", callback_data="admin_conv_cancel")],
    ]
    await query.edit_message_text(
        "🗑 <b>Удаление прокси</b>\n\n"
        "Вы уверены? Прокси будет очищен в config.toml.\n\n"
        "<i>Чтобы изменения вступили в силу — перезапустите сервис недвижимости.</i>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return AdminState.PROXY_CONFIRM_DELETE


async def proxy_delete_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выполнить удаление прокси — записать пустые строки"""
    query = update.callback_query
    await query.answer()

    from parserhub.api_client import RealtyAPI
    realty_api: RealtyAPI = context.bot_data["realty_api"]
    try:
        await realty_api.update_proxy("", "")
        await query.edit_message_text(
            "✅ <b>Прокси удалён.</b>\n\n"
            "<i>Чтобы изменения вступили в силу — перезапустите сервис недвижимости.</i>",
            parse_mode="HTML",
        )
        logger.info(f"Admin {update.effective_user.id} удалил прокси")
    except Exception as e:
        logger.exception("Ошибка удаления прокси")
        await query.edit_message_text(f"❌ Ошибка удаления прокси:\n{e}")
    return ConversationHandler.END


async def proxy_restart_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Запросить подтверждение перезапуска сервиса"""
    query = update.callback_query
    await query.answer()

    keyboard = [
        [InlineKeyboardButton("✅ Да, перезапустить", callback_data=AdminCB.PROXY_RESTART_CONFIRM)],
        [InlineKeyboardButton("❌ Отмена", callback_data="admin_conv_cancel")],
    ]
    await query.edit_message_text(
        "🔄 <b>Перезапуск сервиса недвижимости</b>\n\n"
        "Вы уверены?\n\n"
        "<i>При перезапуске будут применены новые настройки прокси, "
        "если установлены, либо очищены старые, если требуется удаление. "
        "Сервис будет недоступен несколько секунд.</i>",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return AdminState.PROXY_CONFIRM_RESTART


async def proxy_restart_execute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Выполнить перезапуск сервиса недвижимости"""
    query = update.callback_query
    await query.answer()

    from parserhub.api_client import RealtyAPI
    realty_api: RealtyAPI = context.bot_data["realty_api"]
    try:
        await realty_api.restart_service()
        await query.edit_message_text(
            "🔄 <b>Сервис недвижимости перезапускается...</b>\n\n"
            "Docker поднимет его автоматически через несколько секунд.",
            parse_mode="HTML",
        )
        logger.info(f"Admin {update.effective_user.id} инициировал перезапуск realty-monitor")
    except Exception as e:
        logger.exception("Ошибка перезапуска сервиса недвижимости")
        await query.edit_message_text(f"❌ Ошибка перезапуска:\n{e}")
    return ConversationHandler.END


async def receive_proxy_string(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен proxy_string — валидация и запрос proxy_change_url"""
    proxy_string = update.message.text.strip()

    if not _PROXY_RE.match(proxy_string):
        await update.message.reply_text(
            "❌ <b>Неверный формат прокси.</b>\n\n"
            f"{_PROXY_FORMATS_HELP}",
            parse_mode="HTML",
        )
        return ConversationHandler.END

    context.user_data["admin_proxy_string"] = proxy_string

    keyboard = [
        [InlineKeyboardButton("⏭ Пропустить (оставить текущий)", callback_data="admin_proxy_skip_url")],
        [InlineKeyboardButton("❌ Отмена", callback_data="admin_conv_cancel")],
    ]
    await update.message.reply_text(
        "✅ Формат верный.\n\n"
        "Теперь введите <b>proxy_change_url</b> (URL для ротации IP)\n"
        "или нажмите <b>Пропустить</b> чтобы оставить текущий:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return AdminState.INPUT_PROXY_CHANGE_URL


async def receive_proxy_change_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен proxy_change_url — сохранить настройки"""
    proxy_change_url = update.message.text.strip()
    return await _save_proxy(update, context, proxy_change_url)


async def proxy_skip_change_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Пропустить ввод proxy_change_url — получить текущий и сохранить"""
    query = update.callback_query
    await query.answer()

    from parserhub.api_client import RealtyAPI
    realty_api: RealtyAPI = context.bot_data["realty_api"]
    try:
        current = await realty_api.get_proxy()
        proxy_change_url = current.get("proxy_change_url", "")
    except Exception:
        proxy_change_url = ""

    # Перенаправляем в сохранение — нужен update.message, но у нас callback
    proxy_string = context.user_data.pop("admin_proxy_string", "")

    from parserhub.api_client import RealtyAPI as _API
    realty_api2: _API = context.bot_data["realty_api"]
    try:
        await realty_api2.update_proxy(proxy_string, proxy_change_url)
        await query.edit_message_text(
            "✅ <b>Прокси обновлён!</b>\n\n"
            f"<code>{proxy_string[:30]}...</code>",
            parse_mode="HTML",
        )
        logger.info(f"Admin {query.from_user.id} updated proxy: {proxy_string[:20]}...")
    except Exception as e:
        logger.exception("Ошибка сохранения прокси (proxy_skip_change_url)")
        await query.edit_message_text(f"❌ Ошибка сохранения прокси:\n{e}")
    return ConversationHandler.END


async def _save_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE, proxy_change_url: str) -> int:
    """Сохранить прокси настройки через RealtyAPI"""
    proxy_string = context.user_data.pop("admin_proxy_string", "")

    from parserhub.api_client import RealtyAPI
    realty_api: RealtyAPI = context.bot_data["realty_api"]
    try:
        await realty_api.update_proxy(proxy_string, proxy_change_url)
        await update.message.reply_text(
            "✅ <b>Прокси успешно обновлён!</b>\n\n"
            f"<b>proxy_string:</b> <code>{proxy_string[:30]}...</code>\n"
            f"<b>proxy_change_url:</b> <code>{proxy_change_url[:40] if proxy_change_url else 'не задан'}</code>",
            parse_mode="HTML",
        )
        logger.info(f"Admin {update.effective_user.id} updated proxy: {proxy_string[:20]}...")
    except Exception as e:
        logger.exception("Ошибка сохранения прокси (_save_proxy)")
        await update.message.reply_text(f"❌ Ошибка сохранения прокси:\n{e}")
    return ConversationHandler.END


# ===== Управление ценами подписок =====

async def manage_prices(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать текущие цены подписок"""
    query = update.callback_query
    await query.answer()

    service: SubscriptionService = context.bot_data["subscription"]
    plans = await service.get_plans()

    lines = ["💰 <b>Цены подписок</b>\n"]
    keyboard = []
    for key in ["day", "week", "month"]:
        plan = plans[key]
        price_rub = plan["price"] // 100
        lines.append(f"• {plan['label']}: <b>{price_rub} RUB</b>")
        keyboard.append([
            InlineKeyboardButton(f"✏️ {plan['label']}", callback_data=f"{AdminCB.EDIT_PRICE}{key}")
        ])

    keyboard.append([InlineKeyboardButton("🔙 Назад", callback_data=AdminCB.MENU)])

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


async def edit_price_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Начало редактирования цены тарифа"""
    query = update.callback_query
    await query.answer()

    plan = query.data.replace(AdminCB.EDIT_PRICE, "")
    context.user_data["admin_edit_plan"] = plan

    service: SubscriptionService = context.bot_data["subscription"]
    plans = await service.get_plans()
    plan_info = plans.get(plan, {})
    current_price = plan_info.get("price", 0) // 100

    keyboard = [[InlineKeyboardButton("❌ Отмена", callback_data="admin_conv_cancel")]]
    await query.edit_message_text(
        f"✏️ <b>Изменить цену: {plan_info.get('label', plan)}</b>\n\n"
        f"Текущая цена: <b>{current_price} RUB</b>\n\n"
        "Введите новую цену в рублях:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return AdminState.INPUT_NEW_PRICE


async def receive_new_price(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получена новая цена — обновить"""
    text = update.message.text.strip()

    try:
        price_rub = int(text)
    except ValueError:
        await update.message.reply_text("Введите числовое значение цены в рублях.")
        return AdminState.INPUT_NEW_PRICE

    if price_rub <= 0:
        await update.message.reply_text("Цена должна быть больше 0.")
        return AdminState.INPUT_NEW_PRICE

    plan = context.user_data.get("admin_edit_plan")
    price_kopecks = price_rub * 100

    service: SubscriptionService = context.bot_data["subscription"]
    await service.update_plan_price(plan, price_kopecks)

    plans = await service.get_plans()
    plan_label = plans[plan]["label"]

    await update.message.reply_text(
        f"✅ Цена обновлена!\n\n"
        f"Тариф: {plan_label}\n"
        f"Новая цена: <b>{price_rub} RUB</b>",
        parse_mode="HTML",
    )
    logger.info(f"Admin {update.effective_user.id} updated price: {plan}={price_rub} RUB")
    return ConversationHandler.END


def _build_chats_conv(
    entry_cb: str,
    entry_handler,
    menu_state: int,
    edit_cb: str,
    edit_handler,
    clear_cb: str,
    clear_confirm_handler,
    input_state: int,
    input_handler,
    confirm_state: int,
    confirm_cb: str,
    execute_handler,
) -> ConversationHandler:
    """Фабрика ConversationHandler для управления списком чатов"""
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(entry_handler, pattern=f"^{entry_cb}$")
        ],
        states={
            menu_state: [
                CallbackQueryHandler(edit_handler, pattern=f"^{edit_cb}$"),
                CallbackQueryHandler(clear_confirm_handler, pattern=f"^{clear_cb}$"),
            ],
            input_state: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, input_handler)
            ],
            confirm_state: [
                CallbackQueryHandler(execute_handler, pattern=f"^{confirm_cb}$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_admin_conv, pattern="^admin_conv_cancel$|^admin_menu$"),
            CommandHandler("start", cancel_admin_conv),
            MessageHandler(MAIN_MENU_FILTER, cancel_admin_conv),
        ],
        conversation_timeout=300,
        allow_reentry=True,
    )


async def _noop_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Заглушка для кнопки с номером страницы — просто отвечает на callback."""
    await update.callback_query.answer()


def register_admin_handlers(app):
    """Регистрация обработчиков админки"""
    # Команда /admin
    app.add_handler(CommandHandler("admin", admin_command))

    # Возврат в меню
    app.add_handler(CallbackQueryHandler(admin_menu_callback, pattern=f"^{AdminCB.MENU}$"))

    # Список подписок + пагинация
    app.add_handler(CallbackQueryHandler(show_subscriptions, pattern=f"^{AdminCB.SUBSCRIPTIONS}$"))
    app.add_handler(CallbackQueryHandler(show_subscriptions_page, pattern=f"^{AdminCB.SUBS_PAGE}\\d+$"))
    app.add_handler(CallbackQueryHandler(_noop_callback, pattern=f"^{AdminCB.NOOP}$"))

    # Доходы
    app.add_handler(CallbackQueryHandler(show_revenue, pattern=f"^{AdminCB.REVENUE}$"))

    # Список админов + удаление
    app.add_handler(CallbackQueryHandler(show_admins, pattern=f"^{AdminCB.ADMINS_LIST}$"))
    app.add_handler(CallbackQueryHandler(remove_admin, pattern=f"^{AdminCB.REMOVE_ADMIN}"))

    # Закрыть
    app.add_handler(CallbackQueryHandler(close_admin, pattern=f"^{AdminCB.CLOSE}$"))

    # ConversationHandler: выдать подписку
    grant_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(grant_sub_start, pattern=f"^{AdminCB.GRANT_SUB}$")
        ],
        states={
            AdminState.INPUT_USER_FOR_SUB: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, grant_sub_receive_user)
            ],
            AdminState.SELECT_PLAN: [
                CallbackQueryHandler(grant_sub_select_plan, pattern=f"^{AdminCB.GRANT_PLAN}")
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_admin_conv, pattern="^admin_conv_cancel$|^admin_menu$"),
            CommandHandler("start", cancel_admin_conv),
            MessageHandler(MAIN_MENU_FILTER, cancel_admin_conv),
        ],
        conversation_timeout=300,
        allow_reentry=True,
    )
    app.add_handler(grant_conv)

    # ConversationHandler: аннулировать подписку
    revoke_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(revoke_sub_start, pattern=f"^{AdminCB.REVOKE_SUB}$")
        ],
        states={
            AdminState.INPUT_USER_FOR_REVOKE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, revoke_sub_receive_user)
            ],
            AdminState.CONFIRM_REVOKE: [
                CallbackQueryHandler(revoke_sub_confirm, pattern="^admin_revoke_ok$")
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_admin_conv, pattern="^admin_conv_cancel$|^admin_menu$"),
            CommandHandler("start", cancel_admin_conv),
            MessageHandler(MAIN_MENU_FILTER, cancel_admin_conv),
        ],
        conversation_timeout=300,
        allow_reentry=True,
    )
    app.add_handler(revoke_conv)

    # ConversationHandler: добавить админа
    add_admin_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_admin_start, pattern=f"^{AdminCB.ADD_ADMIN}$")
        ],
        states={
            AdminState.INPUT_USER_FOR_ADMIN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, add_admin_receive_user)
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_admin_conv, pattern="^admin_conv_cancel$|^admin_menu$"),
            CommandHandler("start", cancel_admin_conv),
            MessageHandler(MAIN_MENU_FILTER, cancel_admin_conv),
        ],
        conversation_timeout=300,
        allow_reentry=True,
    )
    app.add_handler(add_admin_conv)

    # ConversationHandler: управление чатами ПВЗ
    app.add_handler(_build_chats_conv(
        entry_cb=AdminCB.PVZ_CHATS,
        entry_handler=manage_pvz_chats,
        menu_state=AdminState.PVZ_CHATS_MENU,
        edit_cb=AdminCB.PVZ_CHATS_EDIT,
        edit_handler=pvz_chats_edit_start,
        clear_cb=AdminCB.PVZ_CHATS_CLEAR,
        clear_confirm_handler=pvz_chats_clear_confirm,
        input_state=AdminState.INPUT_PVZ_CHATS,
        input_handler=receive_pvz_chats,
        confirm_state=AdminState.CONFIRM_CLEAR_PVZ,
        confirm_cb=AdminCB.PVZ_CHATS_CLEAR_OK,
        execute_handler=pvz_chats_clear_execute,
    ))

    # ConversationHandler: управление чатами ЧС
    app.add_handler(_build_chats_conv(
        entry_cb=AdminCB.BLACKLIST_CHATS,
        entry_handler=manage_blacklist_chats,
        menu_state=AdminState.BL_CHATS_MENU,
        edit_cb=AdminCB.BL_CHATS_EDIT,
        edit_handler=bl_chats_edit_start,
        clear_cb=AdminCB.BL_CHATS_CLEAR,
        clear_confirm_handler=bl_chats_clear_confirm,
        input_state=AdminState.INPUT_BLACKLIST_CHATS,
        input_handler=receive_blacklist_chats,
        confirm_state=AdminState.CONFIRM_CLEAR_BL,
        confirm_cb=AdminCB.BL_CHATS_CLEAR_OK,
        execute_handler=bl_chats_clear_execute,
    ))

    # Управление ценами подписок
    app.add_handler(CallbackQueryHandler(manage_prices, pattern=f"^{AdminCB.MANAGE_PRICES}$"))

    # ConversationHandler: настройки прокси
    proxy_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(proxy_settings_start, pattern=f"^{AdminCB.PROXY_SETTINGS}$")
        ],
        states={
            AdminState.PROXY_CONFIRM_RESTART: [
                CallbackQueryHandler(proxy_change_start, pattern=f"^{AdminCB.PROXY_CHANGE}$"),
                CallbackQueryHandler(proxy_delete_confirm, pattern=f"^{AdminCB.PROXY_DELETE}$"),
                CallbackQueryHandler(proxy_restart_confirm, pattern=f"^{AdminCB.PROXY_RESTART}$"),
                CallbackQueryHandler(proxy_restart_execute, pattern=f"^{AdminCB.PROXY_RESTART_CONFIRM}$"),
            ],
            AdminState.INPUT_PROXY_STRING: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, receive_proxy_string)
            ],
            AdminState.INPUT_PROXY_CHANGE_URL: [
                CallbackQueryHandler(proxy_skip_change_url, pattern="^admin_proxy_skip_url$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, receive_proxy_change_url),
            ],
            AdminState.PROXY_CONFIRM_DELETE: [
                CallbackQueryHandler(proxy_delete_execute, pattern=f"^{AdminCB.PROXY_DELETE_CONFIRM}$"),
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_admin_conv, pattern="^admin_conv_cancel$|^admin_menu$"),
            CommandHandler("start", cancel_admin_conv),
            MessageHandler(MAIN_MENU_FILTER, cancel_admin_conv),
        ],
        conversation_timeout=300,
        allow_reentry=True,
    )
    app.add_handler(proxy_conv)

    # ConversationHandler: редактирование цены
    edit_price_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_price_start, pattern=f"^{AdminCB.EDIT_PRICE}")
        ],
        states={
            AdminState.INPUT_NEW_PRICE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND & ~MAIN_MENU_FILTER, receive_new_price)
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_admin_conv, pattern="^admin_conv_cancel$|^admin_menu$"),
            CommandHandler("start", cancel_admin_conv),
            MessageHandler(MAIN_MENU_FILTER, cancel_admin_conv),
        ],
        conversation_timeout=300,
        allow_reentry=True,
    )
    app.add_handler(edit_price_conv)
