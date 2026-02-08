"""Сервис управления подписками"""
import aiosqlite
from datetime import datetime, timedelta
from pathlib import Path
from loguru import logger


class SubscriptionService:

    PLANS = {
        "day": {"days": 1, "price": 30000, "label": "1 день"},
        "month": {"days": 30, "price": 50000, "label": "30 дней"},
        "quarter": {"days": 90, "price": 100000, "label": "90 дней"},
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

    async def has_active(self, user_id: int) -> bool:
        """Проверить есть ли активная подписка"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT active_until FROM subscriptions WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return False
                return datetime.fromisoformat(row[0]) > datetime.utcnow()

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
        if plan not in self.PLANS:
            raise ValueError(f"Invalid plan: {plan}")

        now = datetime.utcnow()
        days = self.PLANS[plan]["days"]

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

    async def delete_expired(self) -> int:
        """Удалить истекшие подписки. Возвращает количество удалённых."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM subscriptions WHERE active_until < ?",
                (datetime.utcnow().isoformat(),),
            )
            await db.commit()
            return cursor.rowcount
