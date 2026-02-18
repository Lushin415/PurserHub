"""Обработчики подписок и платежей"""
from datetime import datetime
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
)
from telegram.ext import (
    ContextTypes,
    CallbackQueryHandler,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)
from loguru import logger

from parserhub.config import config
from parserhub.db_service import DatabaseService
from parserhub.services.subscription_service import SubscriptionService


# Callback data
class SubCB:
    MENU = "subscription_menu"
    BUY_DAY = "buy_day"
    BUY_MONTH = "buy_month"
    BUY_QUARTER = "buy_quarter"
    BACK = "main_menu"


async def subscription_keyboard(service: SubscriptionService):
    """Клавиатура выбора тарифа (цены из БД)"""
    plans = await service.get_plans()
    buttons = []
    for key in ["day", "month", "quarter"]:
        plan = plans[key]
        price_rub = plan["price"] // 100
        buttons.append([InlineKeyboardButton(
            f"{plan['label']} — {price_rub} RUB",
            callback_data=f"buy_{key}"
        )])
    buttons.append([InlineKeyboardButton("❌ Отмена", callback_data=SubCB.BACK)])
    return InlineKeyboardMarkup(buttons)


async def subscription_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Меню подписки — показывает статус и тарифы"""
    user_id = update.effective_user.id
    service: SubscriptionService = context.bot_data["subscription"]

    info = await service.get_info(user_id)
    trial = await service.get_trial_info(user_id)

    if info:
        active_until = datetime.fromisoformat(info["active_until"])
        if active_until > datetime.utcnow():
            remaining = active_until - datetime.utcnow()
            days_left = remaining.days
            hours_left = remaining.seconds // 3600

            status_text = (
                f"<b>Статус:</b> ✅ Активна\n"
                f"<b>Тариф:</b> {info['plan']}\n"
                f"<b>Действует до:</b> {active_until.strftime('%d.%m.%Y %H:%M')} UTC\n"
                f"<b>Осталось:</b> {days_left} дн. {hours_left} ч.\n\n"
                "Вы можете продлить подписку:"
            )
        else:
            status_text = (
                "<b>Статус:</b> Истекла\n\n"
                "Выберите тариф для активации:"
            )
    elif trial and trial["is_active"]:
        trial_until = datetime.fromisoformat(trial["trial_until"])
        remaining = trial_until - datetime.utcnow()
        days_left = remaining.days
        hours_left = remaining.seconds // 3600
        status_text = (
            f"<b>Статус:</b> 🎁 Пробный период\n"
            f"<b>Действует до:</b> {trial_until.strftime('%d.%m.%Y %H:%M')} UTC\n"
            f"<b>Осталось:</b> {days_left} дн. {hours_left} ч.\n\n"
            "После окончания пробного периода для продолжения работы оформите подписку:"
        )
    elif trial and not trial["is_active"]:
        status_text = (
            "<b>Статус:</b> Пробный период истёк\n\n"
            "Выберите тариф для активации:"
        )
    else:
        status_text = (
            "<b>Статус:</b> Не активна\n\n"
            "Выберите тариф для активации:"
        )

    text = f"💳 <b>Подписка</b>\n\n{status_text}"

    keyboard = await subscription_keyboard(service)

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, reply_markup=keyboard, parse_mode="HTML")
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="HTML")


async def buy_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Выставление инвойса на оплату"""
    query = update.callback_query
    await query.answer()

    plan = query.data.replace("buy_", "")

    service: SubscriptionService = context.bot_data["subscription"]
    plans = await service.get_plans()

    if plan not in plans:
        await query.edit_message_text("❌ Неизвестный тариф.")
        return

    plan_info = plans[plan]
    title = plan_info["label"]
    price = plan_info["price"]

    try:
        await context.bot.send_invoice(
            chat_id=query.message.chat.id,
            title="Подписка ParserHub",
            description=title,
            payload=f"sub_{plan}",
            provider_token=config.PROVIDER_TOKEN,
            currency="RUB",
            prices=[LabeledPrice(title, price)],
        )
    except Exception as e:
        logger.error(f"send_invoice error: plan={plan}, price={price}, error={e}")
        await context.bot.send_message(
            chat_id=query.message.chat.id,
            text=f"❌ Ошибка создания платежа: {e}",
        )


async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка pre_checkout_query — ОБЯЗАТЕЛЬНО для Telegram Payments"""
    query = update.pre_checkout_query
    payload = query.invoice_payload

    # Проверяем что payload валидный
    if not payload.startswith("sub_"):
        await query.answer(ok=False, error_message="Ошибка: неизвестный платёж.")
        return

    plan = payload.replace("sub_", "")
    if plan not in SubscriptionService.DEFAULT_PLANS:
        await query.answer(ok=False, error_message="Ошибка: неизвестный тариф.")
        return

    # Всё ок — подтверждаем
    await query.answer(ok=True)


async def successful_payment_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка успешной оплаты"""
    payment = update.message.successful_payment
    payload = payment.invoice_payload
    user_id = update.effective_user.id

    plan = payload.replace("sub_", "")

    service: SubscriptionService = context.bot_data["subscription"]

    try:
        await service.activate(user_id, plan)

        # Записать платёж для статистики доходов
        db: DatabaseService = context.bot_data["db"]
        await db.log_payment(user_id, plan, payment.total_amount, payment.currency)

        plans = await service.get_plans()
        plan_info = plans.get(plan, {})
        label = plan_info.get("label", plan)

        keyboard = [[InlineKeyboardButton("🏠 Главное меню", callback_data="main_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            f"✅ <b>Подписка активирована!</b>\n\n"
            f"Тариф: {label}\n"
            f"Сумма: {payment.total_amount / 100:.0f} {payment.currency}",
            reply_markup=reply_markup,
            parse_mode="HTML",
        )

        logger.info(
            f"Payment successful: user={user_id}, plan={plan}, "
            f"amount={payment.total_amount / 100} {payment.currency}"
        )

    except Exception as e:
        logger.error(f"Error activating subscription after payment: user={user_id}, error={e}")
        await update.message.reply_text(
            "❌ Оплата прошла, но произошла ошибка при активации подписки.\n"
            "Обратитесь в поддержку с данным сообщением.",
        )


def register_subscription_handlers(app):
    """Регистрация обработчиков подписки"""
    # Меню подписки
    app.add_handler(CallbackQueryHandler(subscription_menu, pattern=f"^{SubCB.MENU}$"))

    # Покупка
    app.add_handler(CallbackQueryHandler(buy_subscription, pattern="^buy_(day|month|quarter)$"))

    # Pre-checkout (обязательно для Telegram Payments)
    app.add_handler(PreCheckoutQueryHandler(pre_checkout_handler))

    # Успешная оплата
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_callback))
