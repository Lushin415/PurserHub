"""Конфигурация ParserHub из переменных окружения"""
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    """Настройки приложения из .env"""

    # Telegram Bot
    BOT_TOKEN: str
    ADMIN_ID: int = 0

    # Telegram API (для Pyrogram сессий)
    API_ID: int
    API_HASH: str

    # Микросервисы
    WORKERS_SERVICE_URL: str = "http://localhost:8002"
    REALTY_SERVICE_URL: str = "http://localhost:8001"

    # Paths
    DB_PATH: str = "parserhub.db"
    SESSIONS_DIR: str = "./sessions"
    LOG_PATH: str = "parserhub.log"

    # Payments (YooKassa через BotFather)
    PROVIDER_TOKEN: str = ""

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8003

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True
    )

    @property
    def sessions_path(self) -> Path:
        """Абсолютный путь к директории сессий"""
        return Path(self.SESSIONS_DIR).resolve()


# Глобальный экземпляр конфига
config = Config()
