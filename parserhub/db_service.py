"""Сервис для работы с SQLite базой данных"""
import aiosqlite
import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from loguru import logger

from parserhub.models import User, UserSettings, ActiveTask


class DatabaseService:
    """Управление базой данных пользователей и задач"""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)

    async def init_db(self):
        """Инициализация БД и создание таблиц"""
        async with aiosqlite.connect(self.db_path) as db:
            # Таблица пользователей
            await db.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    full_name TEXT,
                    phone TEXT,
                    is_parser_authorized BOOLEAN DEFAULT 0,
                    is_blacklist_authorized BOOLEAN DEFAULT 0,
                    created_at TEXT NOT NULL,
                    last_active TEXT
                )
            """)

            # Таблица настроек пользователей
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_settings (
                    user_id INTEGER PRIMARY KEY,
                    default_mode TEXT DEFAULT 'worker',
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)

            # Таблица активных задач
            await db.execute("""
                CREATE TABLE IF NOT EXISTS active_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    task_id TEXT NOT NULL,
                    service TEXT NOT NULL,
                    task_type TEXT,
                    status TEXT DEFAULT 'running',
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                )
            """)

            # Таблица администраторов
            await db.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    user_id INTEGER PRIMARY KEY,
                    added_by INTEGER,
                    created_at TEXT NOT NULL
                )
            """)

            # Таблица платежей (для статистики доходов)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS payments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    plan TEXT NOT NULL,
                    amount INTEGER NOT NULL,
                    currency TEXT NOT NULL DEFAULT 'RUB',
                    created_at TEXT NOT NULL
                )
            """)

            # Таблица глобальных настроек (для чатов ПВЗ и ЧС)
            await db.execute("""
                CREATE TABLE IF NOT EXISTS global_config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            """)

            await db.commit()
            logger.info(f"База данных инициализирована: {self.db_path}")

    # ===== Пользователи =====

    async def get_user(self, user_id: int) -> Optional[User]:
        """Получить пользователя по ID"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM users WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return User(**dict(row))
                return None

    async def get_user_by_username(self, username: str) -> Optional[User]:
        """Найти пользователя по username"""
        username = username.lstrip("@")
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return User(**dict(row))
                return None

    async def create_or_update_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        full_name: Optional[str] = None,
        phone: Optional[str] = None,
    ) -> User:
        """Создать или обновить пользователя"""
        existing = await self.get_user(user_id)
        now = datetime.utcnow().isoformat()

        async with aiosqlite.connect(self.db_path) as db:
            if existing:
                # Обновление
                await db.execute(
                    """
                    UPDATE users
                    SET username = ?, full_name = ?, phone = COALESCE(?, phone), last_active = ?
                    WHERE user_id = ?
                    """,
                    (username, full_name, phone, now, user_id),
                )
            else:
                # Создание
                await db.execute(
                    """
                    INSERT INTO users (user_id, username, full_name, phone, created_at, last_active)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, username, full_name, phone, now, now),
                )
                # Создать настройки по умолчанию
                await db.execute(
                    "INSERT INTO user_settings (user_id) VALUES (?)", (user_id,)
                )

            await db.commit()

        return await self.get_user(user_id)

    async def update_auth_status(
        self, user_id: int, session_type: str, authorized: bool
    ):
        """Обновить статус авторизации (parser или blacklist)"""
        column = (
            "is_parser_authorized"
            if session_type == "parser"
            else "is_blacklist_authorized"
        )
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                f"UPDATE users SET {column} = ? WHERE user_id = ?",
                (1 if authorized else 0, user_id),
            )
            await db.commit()

    # ===== Настройки =====

    async def get_settings(self, user_id: int) -> Optional[UserSettings]:
        """Получить настройки пользователя"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM user_settings WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return UserSettings(**dict(row))
                return None

    async def update_settings(self, settings: UserSettings):
        """Обновить настройки пользователя"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                UPDATE user_settings
                SET default_mode = ?
                WHERE user_id = ?
                """,
                (
                    settings.default_mode,
                    settings.user_id,
                ),
            )
            await db.commit()

    # ===== Задачи =====

    async def add_task(self, task: ActiveTask) -> int:
        """Добавить активную задачу"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO active_tasks (user_id, task_id, service, task_type, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    task.user_id,
                    task.task_id,
                    task.service,
                    task.task_type,
                    task.status,
                    task.created_at.isoformat(),
                ),
            )
            await db.commit()
            return cursor.lastrowid

    async def get_user_tasks(self, user_id: int, service: Optional[str] = None) -> list[ActiveTask]:
        """Получить задачи пользователя"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if service:
                query = "SELECT * FROM active_tasks WHERE user_id = ? AND service = ? ORDER BY created_at DESC"
                params = (user_id, service)
            else:
                query = "SELECT * FROM active_tasks WHERE user_id = ? ORDER BY created_at DESC"
                params = (user_id,)

            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [ActiveTask(**dict(row)) for row in rows]

    async def update_task_status(self, task_id: str, status: str):
        """Обновить статус задачи"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE active_tasks SET status = ? WHERE task_id = ?",
                (status, task_id),
            )
            await db.commit()

    async def delete_task(self, task_id: str):
        """Удалить задачу"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM active_tasks WHERE task_id = ?", (task_id,))
            await db.commit()

    async def clear_running_tasks(self) -> int:
        """Удалить все задачи со статусом running (вызывается при shutdown)"""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM active_tasks WHERE status = 'running'")
            await db.commit()
            return cursor.rowcount

    # ===== Администраторы =====

    async def is_admin(self, user_id: int) -> bool:
        """Проверить является ли пользователь администратором (из БД)"""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT user_id FROM admins WHERE user_id = ?", (user_id,)
            ) as cursor:
                return await cursor.fetchone() is not None

    async def get_admins(self) -> list[dict]:
        """Получить список администраторов из БД (с username из таблицы users)"""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT a.*, u.username FROM admins a
                LEFT JOIN users u ON a.user_id = u.user_id
                ORDER BY a.created_at"""
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def add_admin(self, user_id: int, added_by: int):
        """Добавить администратора"""
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO admins (user_id, added_by, created_at) VALUES (?, ?, ?)",
                (user_id, added_by, now),
            )
            await db.commit()

    async def remove_admin(self, user_id: int):
        """Удалить администратора"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM admins WHERE user_id = ?", (user_id,))
            await db.commit()

    # ===== Глобальные настройки =====

    async def get_global_chats(self, key: str) -> list:
        """Получить список чатов из global_config

        Args:
            key: Ключ настройки ('pvz_monitoring_chats' или 'blacklist_chats')

        Returns:
            Список чатов или пустой список
        """
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT value FROM global_config WHERE key = ?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
                if row and row[0]:
                    try:
                        return json.loads(row[0])
                    except json.JSONDecodeError:
                        logger.error(f"Не удалось распарсить JSON для ключа {key}")
                        return []
                return []

    async def set_global_chats(self, key: str, chats: list):
        """Сохранить список чатов в global_config

        Args:
            key: Ключ настройки ('pvz_monitoring_chats' или 'blacklist_chats')
            chats: Список чатов для сохранения
        """
        async with aiosqlite.connect(self.db_path) as db:
            value_json = json.dumps(chats, ensure_ascii=False)
            await db.execute(
                "INSERT OR REPLACE INTO global_config (key, value) VALUES (?, ?)",
                (key, value_json),
            )
            await db.commit()
            logger.info(f"Сохранены глобальные чаты для {key}: {len(chats)} чатов")

    # ===== Платежи =====

    async def log_payment(self, user_id: int, plan: str, amount: int, currency: str = "RUB"):
        """Записать платёж"""
        now = datetime.utcnow().isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO payments (user_id, plan, amount, currency, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, plan, amount, currency, now),
            )
            await db.commit()

    async def get_revenue_stats(self) -> dict:
        """Статистика доходов"""
        async with aiosqlite.connect(self.db_path) as db:
            # Всего
            async with db.execute("SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM payments") as cur:
                row = await cur.fetchone()
                total_amount = row[0]
                total_count = row[1]

            # За текущий месяц
            month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0).isoformat()
            async with db.execute(
                "SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM payments WHERE created_at >= ?",
                (month_start,),
            ) as cur:
                row = await cur.fetchone()
                month_amount = row[0]
                month_count = row[1]

            # За сегодня
            today_start = datetime.utcnow().replace(hour=0, minute=0, second=0).isoformat()
            async with db.execute(
                "SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM payments WHERE created_at >= ?",
                (today_start,),
            ) as cur:
                row = await cur.fetchone()
                today_amount = row[0]
                today_count = row[1]

            return {
                "total_amount": total_amount,
                "total_count": total_count,
                "month_amount": month_amount,
                "month_count": month_count,
                "today_amount": today_amount,
                "today_count": today_count,
            }
