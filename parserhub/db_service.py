"""Сервис для работы с SQLite базой данных"""
import aiosqlite
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
                    realty_bot_token TEXT,
                    realty_chat_id INTEGER,
                    workers_chat_id INTEGER,
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
                SET realty_bot_token = ?, realty_chat_id = ?,
                    workers_chat_id = ?,
                    default_mode = ?
                WHERE user_id = ?
                """,
                (
                    settings.realty_bot_token,
                    settings.realty_chat_id,
                    settings.workers_chat_id,
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
