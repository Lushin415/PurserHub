"""АЛЬТЕРНАТИВНЫЙ подход: использовать Pyrogram client.start() с колбеками"""
from pathlib import Path
from typing import Dict, Literal, Optional, Callable
from loguru import logger
from pyrogram import Client
from pyrogram.errors import SessionPasswordNeeded
import asyncio


class SessionManagerV2:
    """Управляет созданием Pyrogram сессий через client.start() с колбеками"""

    def __init__(self, sessions_dir: str, api_id: int, api_hash: str):
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.api_id = api_id
        self.api_hash = api_hash

        # Хранилище для кодов и паролей от пользователей
        self._pending_codes: Dict[int, asyncio.Future] = {}
        self._pending_passwords: Dict[int, asyncio.Future] = {}

    def get_session_path(self, user_id: int, session_type: Literal["parser", "blacklist"]) -> str:
        """Получить путь к сессии"""
        session_name = f"{user_id}_{session_type}"
        return str(self.sessions_dir / session_name)

    def session_exists(self, user_id: int, session_type: Literal["parser", "blacklist"]) -> bool:
        """Проверить существует ли файл сессии"""
        session_path = Path(self.get_session_path(user_id, session_type) + ".session")
        return session_path.exists()

    async def create_session_with_code(
        self,
        user_id: int,
        session_type: Literal["parser", "blacklist"],
        phone: str,
        code: str,
        password: Optional[str] = None
    ) -> bool:
        """
        Создать сессию используя client.start() с предоставленным кодом

        Args:
            user_id: ID пользователя Telegram
            session_type: Тип сессии ('parser' или 'blacklist')
            phone: Номер телефона
            code: Код подтверждения из Telegram
            password: Пароль 2FA (опционально)

        Returns:
            True если успешно, False если ошибка
        """
        session_name = self.get_session_path(user_id, session_type)

        logger.info(f"[AUTH V2] user_id={user_id}, session_type={session_type}, phone={phone}")
        logger.info(f"[AUTH V2] session_path={session_name}")

        # Создаём future для кода и пароля
        code_future = asyncio.Future()
        code_future.set_result(code)  # Сразу устанавливаем результат

        password_future = None
        if password:
            password_future = asyncio.Future()
            password_future.set_result(password)

        try:
            client = Client(
                name=session_name,
                api_id=self.api_id,
                api_hash=self.api_hash,
                phone_number=phone,
                phone_code=lambda: code_future,  # Колбек возвращает Future с кодом
                password=lambda: password_future if password_future else None,
                in_memory=False,
            )

            logger.info(f"[AUTH V2] Запуск client.start()...")
            await client.start()
            logger.info(f"[AUTH V2] ✅ Авторизация успешна!")

            # Отключаемся
            await client.stop()
            logger.info(f"[AUTH V2] Клиент остановлен")

            return True

        except SessionPasswordNeeded:
            logger.error(f"[AUTH V2] ❌ Требуется пароль 2FA, но не был предоставлен")
            return False
        except Exception as e:
            logger.error(f"[AUTH V2] ❌ Ошибка авторизации: {e}")
            return False

    async def delete_session(self, user_id: int, session_type: Literal["parser", "blacklist"]):
        """Удалить файл сессии"""
        session_path = Path(self.get_session_path(user_id, session_type) + ".session")
        if session_path.exists():
            session_path.unlink()
            logger.info(f"Сессия {session_type} удалена для user {user_id}")
