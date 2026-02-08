"""Административная панель"""
from datetime import datetime
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


class AdminCB:
    MENU = "admin_menu"
    SUBSCRIPTIONS = "admin_subs"
    GRANT_SUB = "admin_grant"
    GRANT_PLAN = "admin_gplan_"  # + plan
    REVENUE = "admin_revenue"
    ADMINS_LIST = "admin_list"
    ADD_ADMIN = "admin_add"
    REMOVE_ADMIN = "admin_rm_"  # + user_id
    CLOSE = "admin_close"


class AdminState:
    INPUT_USER_FOR_SUB = 1
    SELECT_PLAN = 2
    INPUT_USER_FOR_ADMIN = 3


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
        [InlineKeyboardButton("💰 Доходы", callback_data=AdminCB.REVENUE)],
        [InlineKeyboardButton("👥 Администраторы", callback_data=AdminCB.ADMINS_LIST)],
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

async def show_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список всех активных подписок"""
    query = update.callback_query
    await query.answer()

    service: SubscriptionService = context.bot_data["subscription"]
    subs = await service.get_all_active()

    if not subs:
        text = "📋 <b>Активные подписки</b>\n\nНет активных подписок."
    else:
        lines = []
        for s in subs[:30]:
            until = datetime.fromisoformat(s["active_until"])
            remaining = until - datetime.utcnow()
            name = s.get("username") or s.get("full_name") or "?"
            lines.append(
                f"• <code>{s['user_id']}</code> @{name} — "
                f"{s['plan']} (ост. {remaining.days}д)"
            )
        text = f"📋 <b>Активные подписки</b> ({len(subs)})\n\n" + "\n".join(lines)

    keyboard = [[InlineKeyboardButton("🔙 Назад", callback_data=AdminCB.MENU)]]
    await query.edit_message_text(
        text=text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )


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
        [InlineKeyboardButton("30 дней", callback_data=f"{AdminCB.GRANT_PLAN}month")],
        [InlineKeyboardButton("90 дней", callback_data=f"{AdminCB.GRANT_PLAN}quarter")],
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
        plan_label = SubscriptionService.PLANS[plan]["label"]

        await query.edit_message_text(
            f"✅ Подписка выдана!\n\n"
            f"Пользователь: <code>{user_id}</code>\n"
            f"Тариф: {plan_label}",
            parse_mode="HTML",
        )
        logger.info(f"Admin granted subscription: user={user_id}, plan={plan}")
    except Exception as e:
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

    text = (
        "💰 <b>Доходы</b>\n\n"
        f"<b>Сегодня:</b> {stats['today_amount'] / 100:.0f} RUB ({stats['today_count']} оплат)\n"
        f"<b>Этот месяц:</b> {stats['month_amount'] / 100:.0f} RUB ({stats['month_count']} оплат)\n"
        f"<b>Всего:</b> {stats['total_amount'] / 100:.0f} RUB ({stats['total_count']} оплат)\n\n"
        f"<b>Активных подписок:</b> {len(subs)}"
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
        lines.append(f"• <code>{a['user_id']}</code>")
        keyboard_rows.append([
            InlineKeyboardButton(
                f"❌ Удалить {a['user_id']}",
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
        "Введите Telegram ID:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="HTML",
    )
    return AdminState.INPUT_USER_FOR_ADMIN


async def add_admin_receive_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получен user_id — добавить админа"""
    text = update.message.text.strip()

    try:
        new_admin_id = int(text)
    except ValueError:
        await update.message.reply_text("Введите числовой Telegram ID.")
        return AdminState.INPUT_USER_FOR_ADMIN

    db: DatabaseService = context.bot_data["db"]
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
    """Отмена внутри ConversationHandler"""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Отменено.")
    return ConversationHandler.END


def register_admin_handlers(app):
    """Регистрация обработчиков админки"""
    # Команда /admin
    app.add_handler(CommandHandler("admin", admin_command))

    # Возврат в меню
    app.add_handler(CallbackQueryHandler(admin_menu_callback, pattern=f"^{AdminCB.MENU}$"))

    # Список подписок
    app.add_handler(CallbackQueryHandler(show_subscriptions, pattern=f"^{AdminCB.SUBSCRIPTIONS}$"))

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
                MessageHandler(filters.TEXT & ~filters.COMMAND, grant_sub_receive_user)
            ],
            AdminState.SELECT_PLAN: [
                CallbackQueryHandler(grant_sub_select_plan, pattern=f"^{AdminCB.GRANT_PLAN}")
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_admin_conv, pattern="^admin_conv_cancel$"),
            CommandHandler("start", cancel_admin_conv),
        ],
    )
    app.add_handler(grant_conv)

    # ConversationHandler: добавить админа
    add_admin_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(add_admin_start, pattern=f"^{AdminCB.ADD_ADMIN}$")
        ],
        states={
            AdminState.INPUT_USER_FOR_ADMIN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, add_admin_receive_user)
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel_admin_conv, pattern="^admin_conv_cancel$"),
            CommandHandler("start", cancel_admin_conv),
        ],
    )
    app.add_handler(add_admin_conv)
