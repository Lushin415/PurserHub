"""Сервис управления подписками"""
import json
import aiosqlite
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger


class SubscriptionService:

    DEFAULT_PLANS = {
        "day": {"days": 1, "price": 10000, "label": "1 день"},
        "week": {"days": 7, "price": 19900, "label": "7 дней"},
        "month": {"days": 30, "price": 49900, "label": "30 дней"},
    }

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)

    async def init_table(self):
        """Создать таблицу подписок если не существует"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                CREATE TABLE IF NOT EXISTS subscriptions (
                    user_id INTEGER PRIMARY KEY,
                    plan TEXT NOT NULL,
                    active_until TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)
            await db.commit()
        logger.info("Таблица subscriptions инициализирована")

    async def get_plans(self) -> dict:
        """Получить актуальные тарифы из БД (с fallback на DEFAULT_PLANS)"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT value FROM global_config WHERE key = ?", ("subscription_plans",)
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0]:
                    try:
                        return json.loads(row[0])
                    except json.JSONDecodeError:
                        logger.error("Не удалось распарсить JSON тарифов подписки")
        return dict(self.DEFAULT_PLANS)

    async def update_plan_price(self, plan: str, price: int):
        """Обновить цену тарифа (price в копейках)"""
        plans = await self.get_plans()
        if plan not in plans:
            raise ValueError(f"Unknown plan: {plan}")
        plans[plan]["price"] = price
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO global_config (key, value) VALUES (?, ?)",
                ("subscription_plans", json.dumps(plans, ensure_ascii=False)),
            )
            await db.commit()
        logger.info(f"Обновлена цена тарифа {plan}: {price} коп.")

    async def has_active(self, user_id: int) -> bool:
        """Проверить есть ли активная платная подписка"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT active_until FROM subscriptions WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return False
                return datetime.fromisoformat(row[0]) > datetime.utcnow()

    async def get_trial_info(self, user_id: int) -> dict | None:
        """Получить информацию о пробном периоде из таблицы users"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT trial_until FROM users WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if not row or not row[0]:
                    return None
                trial_until = datetime.fromisoformat(row[0])
                return {
                    "trial_until": row[0],
                    "is_active": trial_until > datetime.utcnow(),
                }

    async def has_access(self, user_id: int) -> bool:
        """Активная платная подписка ИЛИ активный пробный период"""
        if await self.has_active(user_id):
            return True
        trial = await self.get_trial_info(user_id)
        return bool(trial and trial["is_active"])

    async def get_info(self, user_id: int) -> dict | None:
        """Получить информацию о подписке пользователя"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM subscriptions WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
                return dict(row)

    async def activate(self, user_id: int, plan: str):
        """Активировать подписку"""
        plans = await self.get_plans()
        if plan not in plans:
            raise ValueError(f"Invalid plan: {plan}")

        now = datetime.utcnow()
        days = plans[plan]["days"]

        # Если есть активная подписка — продлить от текущей даты окончания
        existing = await self.get_info(user_id)
        if existing:
            existing_until = datetime.fromisoformat(existing["active_until"])
            if existing_until > now:
                active_until = existing_until + timedelta(days=days)
            else:
                active_until = now + timedelta(days=days)
        else:
            active_until = now + timedelta(days=days)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO subscriptions (user_id, plan, active_until, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    plan = excluded.plan,
                    active_until = excluded.active_until,
                    updated_at = excluded.updated_at
                """,
                (
                    user_id,
                    plan,
                    active_until.isoformat(),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            await db.commit()

        logger.info(
            f"Subscription activated: user={user_id}, plan={plan}, "
            f"active_until={active_until.isoformat()}"
        )

    async def get_all_active(self) -> list[dict]:
        """Получить все активные подписки"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT s.*, u.username, u.full_name
                FROM subscriptions s
                LEFT JOIN users u ON s.user_id = u.user_id
                WHERE s.active_until > ?
                ORDER BY s.active_until DESC
                """,
                (datetime.utcnow().isoformat(),),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def revoke(self, user_id: int) -> bool:
        """Аннулировать подписку и пробный период пользователя.
        Возвращает True если у пользователя было что-то активное."""
        past = "2000-01-01T00:00:00"
        async with aiosqlite.connect(self.db_path) as db:
            cursor_sub = await db.execute(
                "DELETE FROM subscriptions WHERE user_id = ?", (user_id,)
            )
            sub_deleted = cursor_sub.rowcount > 0
            cursor_trial = await db.execute(
                "UPDATE users SET trial_until = ? WHERE user_id = ?", (past, user_id)
            )
            trial_reset = cursor_trial.rowcount > 0
            await db.commit()
        logger.info(f"Subscription revoked for user={user_id}, had_sub={sub_deleted}, had_trial={trial_reset}")
        return sub_deleted or trial_reset

    async def get_all_trial_active(self) -> list[dict]:
        """Получить всех пользователей с активным пробным периодом (без платной подписки)"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            now = datetime.utcnow().isoformat()
            async with db.execute(
                """
                SELECT u.user_id, u.username, u.full_name, u.trial_until
                FROM users u
                WHERE u.trial_until > ?
                  AND u.user_id NOT IN (
                      SELECT user_id FROM subscriptions WHERE active_until > ?
                  )
                ORDER BY u.trial_until DESC
                """,
                (now, now),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def delete_expired(self) -> int:
        """Удалить истекшие подписки. Возвращает количество удалённых."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM subscriptions WHERE active_until < ?",
                (datetime.utcnow().isoformat(),),
            )
            await db.commit()
            return cursor.rowcount
