"""HTTP клиенты для вызова микросервисов"""
import httpx
from typing import Optional
from loguru import logger

from parserhub.models import (
    StartMonitoringRequest,
    MonitoringStatus,
    StartParsingRequest,
    ParsingStatus,
    BlacklistCheckResult,
)


class WorkersAPI:
    """HTTP клиент к workers_service"""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        """Закрыть HTTP клиент"""
        await self.client.aclose()

    async def start_monitoring(
        self,
        user_id: int,
        mode: str,
        chats: list[str],
        filters: dict,
        session_path: str,
        blacklist_session_path: str,
        notification_chat_id: int,
        parse_history_days: int = 3,
    ) -> dict:
        """POST /workers/start - Запуск мониторинга ПВЗ"""
        url = f"{self.base_url}/workers/start"

        payload = {
            "user_id": user_id,
            "mode": mode,
            "chats": chats,
            "filters": filters,
            "session_path": session_path,
            "blacklist_session_path": blacklist_session_path,
            "notification_chat_id": notification_chat_id,
            "parse_history_days": parse_history_days,
        }

        try:
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            logger.info(f"Мониторинг запущен: task_id={data.get('task_id')}")
            return data
        except httpx.HTTPError as e:
            logger.error(f"Ошибка запуска мониторинга: {e}")
            raise

    async def stop_monitoring(self, task_id: str) -> dict:
        """POST /workers/stop/{task_id} - Остановка мониторинга"""
        url = f"{self.base_url}/workers/stop/{task_id}"

        try:
            response = await self.client.post(url)
            response.raise_for_status()
            data = response.json()
            logger.info(f"Мониторинг остановлен: {task_id}")
            return data
        except httpx.HTTPError as e:
            logger.error(f"Ошибка остановки мониторинга {task_id}: {e}")
            raise

    async def get_status(self, task_id: str) -> dict:
        """GET /workers/status/{task_id} - Статус мониторинга"""
        url = f"{self.base_url}/workers/status/{task_id}"

        try:
            response = await self.client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Ошибка получения статуса {task_id}: {e}")
            raise

    async def get_found_items(self, task_id: str, limit: int = 50) -> dict:
        """GET /workers/list/{task_id} - Список найденных объявлений"""
        url = f"{self.base_url}/workers/list/{task_id}"
        params = {"limit": limit}

        try:
            response = await self.client.get(url, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Ошибка получения списка для {task_id}: {e}")
            raise

    async def check_blacklist(self, username: str, blacklist_session_path: str) -> dict:
        """POST /blacklist/check?username={username} - Проверка в ЧС"""
        url = f"{self.base_url}/blacklist/check"
        params = {
            "username": username,
            "blacklist_session_path": blacklist_session_path,
        }

        try:
            response = await self.client.post(url, params=params)
            response.raise_for_status()
            data = response.json()
            logger.info(f"Проверка ЧС для {username}: found={data.get('found')}")
            return data
        except httpx.HTTPError as e:
            logger.error(f"Ошибка проверки ЧС для {username}: {e}")
            raise

    async def get_blacklist_chats(self) -> dict:
        """GET /blacklist/chats - Список чатов ЧС"""
        url = f"{self.base_url}/blacklist/chats"

        try:
            response = await self.client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Ошибка получения чатов ЧС: {e}")
            raise

    async def add_blacklist_chat(
        self,
        chat_username: str,
        chat_title: str = "",
        topic_id: Optional[int] = None,
        topic_name: Optional[str] = None,
    ) -> dict:
        """POST /blacklist/chats/add - Добавить чат в ЧС"""
        url = f"{self.base_url}/blacklist/chats/add"
        params = {"chat_username": chat_username}
        if chat_title:
            params["chat_title"] = chat_title
        if topic_id is not None:
            params["topic_id"] = topic_id
        if topic_name:
            params["topic_name"] = topic_name

        try:
            response = await self.client.post(url, params=params)
            response.raise_for_status()
            data = response.json()
            topic_info = f" (топик: {topic_name})" if topic_name else ""
            logger.info(f"Чат {chat_username}{topic_info} добавлен в ЧС")
            return data
        except httpx.HTTPError as e:
            logger.error(f"Ошибка добавления чата {chat_username} в ЧС: {e}")
            raise

    async def get_chat_topics(self, chat_username: str, blacklist_session_path: str) -> dict:
        """GET /blacklist/chats/topics - Получить топики форума"""
        url = f"{self.base_url}/blacklist/chats/topics"
        params = {
            "chat_username": chat_username,
            "blacklist_session_path": blacklist_session_path,
        }

        try:
            response = await self.client.get(url, params=params, timeout=60.0)
            response.raise_for_status()
            data = response.json()
            logger.info(f"Топики чата {chat_username}: is_forum={data.get('is_forum')}, topics={len(data.get('topics', []))}")
            return data
        except httpx.HTTPError as e:
            logger.error(f"Ошибка получения топиков чата {chat_username}: {e}")
            raise

    async def remove_blacklist_chat(self, chat_username: str, topic_id: Optional[int] = None) -> dict:
        """POST /blacklist/chats/remove - Удалить чат из ЧС"""
        url = f"{self.base_url}/blacklist/chats/remove"
        params = {"chat_username": chat_username}
        if topic_id is not None:
            params["topic_id"] = topic_id

        try:
            response = await self.client.post(url, params=params)
            response.raise_for_status()
            data = response.json()
            logger.info(f"Чат {chat_username} удалён из ЧС")
            return data
        except httpx.HTTPError as e:
            logger.error(f"Ошибка удаления чата {chat_username} из ЧС: {e}")
            raise


class RealtyAPI:
    """HTTP клиент к avito_cian_parser"""

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(timeout=30.0)

    async def close(self):
        """Закрыть HTTP клиент"""
        await self.client.aclose()

    async def start_parsing(
        self,
        user_id: int,
        notification_bot_token: str,
        notification_chat_id: int,
        avito_url: Optional[str] = None,
        cian_url: Optional[str] = None,
        pages: int = 3,
    ) -> dict:
        """POST /parse/start - Запуск парсинга недвижимости"""
        url = f"{self.base_url}/parse/start"

        payload = {
            "user_id": user_id,
            "notification_bot_token": notification_bot_token,
            "notification_chat_id": notification_chat_id,
            "pages": pages,
        }

        if avito_url:
            payload["avito_url"] = avito_url
        if cian_url:
            payload["cian_url"] = cian_url

        try:
            response = await self.client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            logger.info(f"Парсинг запущен: task_id={data.get('task_id')}")
            return data
        except httpx.HTTPError as e:
            logger.error(f"Ошибка запуска парсинга: {e}")
            raise

    async def stop_parsing(self, task_id: str) -> dict:
        """POST /parse/stop/{task_id} - Остановка парсинга"""
        url = f"{self.base_url}/parse/stop/{task_id}"

        try:
            response = await self.client.post(url)
            response.raise_for_status()
            data = response.json()
            logger.info(f"Парсинг остановлен: {task_id}")
            return data
        except httpx.HTTPError as e:
            logger.error(f"Ошибка остановки парсинга {task_id}: {e}")
            raise

    async def get_status(self, task_id: str) -> dict:
        """GET /parse/status/{task_id} - Статус парсинга"""
        url = f"{self.base_url}/parse/status/{task_id}"

        try:
            response = await self.client.get(url)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error(f"Ошибка получения статуса парсинга {task_id}: {e}")
            raise
