"""Управление Pyrogram сессиями пользователей"""
from pathlib import Path
from typing import Dict, Literal, Optional
from loguru import logger
from pyrogram import Client
from pyrogram.errors import (
    PhoneCodeInvalid,
    PhoneCodeExpired,
    SessionPasswordNeeded,
    PasswordHashInvalid,
)


class SessionManager:
    """Управляет созданием и авторизацией Pyrogram сессий"""

    def __init__(self, sessions_dir: str, api_id: int, api_hash: str):
        self.sessions_dir = Path(sessions_dir)
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.api_id = api_id
        self.api_hash = api_hash

        # Временное хранилище клиентов в процессе авторизации
        self._pending_clients: Dict[int, Client] = {}
        self._phone_hashes: Dict[int, str] = {}
        self._phones: Dict[int, str] = {}

    def get_session_path(self, user_id: int, session_type: Literal["parser", "blacklist"]) -> str:
        """
        Получить путь к сессии (БЕЗ .session расширения).
        Pyrogram сам добавит .session
        """
        session_name = f"{user_id}_{session_type}"
        return str(self.sessions_dir / session_name)

    def session_exists(self, user_id: int, session_type: Literal["parser", "blacklist"]) -> bool:
        """Проверить существует ли файл сессии"""
        session_path = Path(self.get_session_path(user_id, session_type) + ".session")
        return session_path.exists()

    async def start_auth(
        self, user_id: int, session_type: Literal["parser", "blacklist"], phone: str
    ) -> str:
        """
        Начать процесс авторизации: отправить код на номер.
        Возвращает 'code_sent' или raise Exception
        """
        session_name = self.get_session_path(user_id, session_type)

        logger.info(f"[AUTH START] user_id={user_id}, session_type={session_type}, phone={phone}")
        logger.info(f"[AUTH START] session_path={session_name}")

        try:
            # Создать клиент
            # ВАЖНО: НЕ передаём phone_number, иначе Pyrogram автоматически начнёт авторизацию!
            # Мы делаем авторизацию вручную через send_code() и sign_in()
            logger.debug(f"[AUTH START] Создание Client...")
            client = Client(
                name=session_name,
                api_id=self.api_id,
                api_hash=self.api_hash,
                # phone_number НЕ УКАЗЫВАЕМ - делаем авторизацию вручную!
                in_memory=False,  # Сохранять на диск
            )

            logger.debug(f"[AUTH START] Подключение...")
            await client.connect()

            # Отправить код
            logger.debug(f"[AUTH START] Отправка кода на {phone}...")
            sent_code = await client.send_code(phone)
            phone_code_hash = sent_code.phone_code_hash

            logger.info(f"[AUTH START] Получен phone_code_hash: {phone_code_hash[:20]}...")
            logger.info(f"[AUTH START] sent_code type: {sent_code.type}")

            # Сохранить во временное хранилище
            self._pending_clients[user_id] = client
            self._phone_hashes[user_id] = phone_code_hash
            self._phones[user_id] = phone

            logger.info(f"[AUTH START] ✅ Код отправлен на {phone} для user {user_id}, тип: {session_type}")
            logger.info(f"[AUTH START] Клиент сохранён в pending_clients, user_id={user_id}")
            return "code_sent"

        except Exception as e:
            logger.error(f"Ошибка отправки кода для user {user_id}: {e}")
            # Очистка
            if user_id in self._pending_clients:
                try:
                    await self._pending_clients[user_id].disconnect()
                except:
                    pass
                del self._pending_clients[user_id]
            raise

    async def confirm_code(self, user_id: int, code: str) -> Literal["success", "need_2fa", "error"]:
        """
        Подтвердить код из Telegram.
        Возвращает:
        - 'success' - авторизация успешна
        - 'need_2fa' - требуется пароль двухфакторной аутентификации
        - 'error' - ошибка
        """
        logger.info(f"[CONFIRM CODE] user_id={user_id}, code_length={len(code)}")

        if user_id not in self._pending_clients:
            logger.error(f"[CONFIRM CODE] ❌ Клиент для user {user_id} не найден в pending_clients!")
            logger.error(f"[CONFIRM CODE] Pending clients: {list(self._pending_clients.keys())}")
            return "error"

        client = self._pending_clients[user_id]
        phone = self._phones[user_id]
        phone_code_hash = self._phone_hashes[user_id]

        logger.debug(f"[CONFIRM CODE] phone={phone}, code_hash={phone_code_hash[:10]}...")
        logger.debug(f"[CONFIRM CODE] code={code}")
        logger.debug(f"[CONFIRM CODE] client connected: {client.is_connected}")

        try:
            # Попытка войти с кодом
            logger.info(f"[CONFIRM CODE] Вызов client.sign_in(phone={phone}, code_hash={phone_code_hash[:15]}..., code={code})...")
            await client.sign_in(phone, phone_code_hash, code)
            logger.info(f"[CONFIRM CODE] ✅ Авторизация успешна для user {user_id}")

            # Отключить и очистить
            logger.debug(f"[CONFIRM CODE] Отключение клиента...")
            await client.disconnect()
            self._cleanup_user(user_id)
            logger.info(f"[CONFIRM CODE] Клиент отключён и очищен")

            return "success"

        except SessionPasswordNeeded:
            # Нужен пароль 2FA
            logger.info(f"[CONFIRM CODE] 🔐 Требуется 2FA для user {user_id}")
            return "need_2fa"

        except (PhoneCodeInvalid, PhoneCodeExpired) as e:
            logger.error(f"[CONFIRM CODE] ❌ Неверный/истёкший код для user {user_id}: {e}")
            await client.disconnect()
            self._cleanup_user(user_id)
            return "error"

        except Exception as e:
            logger.error(f"[CONFIRM CODE] ❌ Ошибка подтверждения кода для user {user_id}: {e}")
            try:
                await client.disconnect()
            except:
                pass
            self._cleanup_user(user_id)
            return "error"

    async def confirm_2fa(self, user_id: int, password: str) -> bool:
        """
        Подтвердить пароль двухфакторной аутентификации.
        Возвращает True если успех, False если ошибка
        """
        if user_id not in self._pending_clients:
            logger.error(f"Клиент для user {user_id} не найден")
            return False

        client = self._pending_clients[user_id]

        try:
            await client.check_password(password)
            logger.info(f"2FA пароль принят для user {user_id}")

            # Отключить и очистить
            await client.disconnect()
            self._cleanup_user(user_id)

            return True

        except PasswordHashInvalid:
            logger.error(f"Неверный 2FA пароль для user {user_id}")
            await client.disconnect()
            self._cleanup_user(user_id)
            return False

        except Exception as e:
            logger.error(f"Ошибка 2FA для user {user_id}: {e}")
            try:
                await client.disconnect()
            except:
                pass
            self._cleanup_user(user_id)
            return False

    async def delete_session(self, user_id: int, session_type: Literal["parser", "blacklist"]):
        """Удалить файл сессии"""
        session_path = Path(self.get_session_path(user_id, session_type) + ".session")
        if session_path.exists():
            session_path.unlink()
            logger.info(f"Сессия {session_type} удалена для user {user_id}")

    def _cleanup_user(self, user_id: int):
        """Очистить временные данные пользователя"""
        self._pending_clients.pop(user_id, None)
        self._phone_hashes.pop(user_id, None)
        self._phones.pop(user_id, None)
