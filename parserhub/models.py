"""Модели данных для ParserHub"""
from datetime import datetime
from typing import Optional, Literal
from pydantic import BaseModel, Field


# ===== Модели пользователей =====

class User(BaseModel):
    """Модель пользователя"""
    user_id: int
    username: Optional[str] = None
    full_name: Optional[str] = None
    phone: Optional[str] = None
    is_parser_authorized: bool = False
    is_blacklist_authorized: bool = False
    created_at: datetime
    last_active: Optional[datetime] = None


class UserSettings(BaseModel):
    """Настройки пользователя"""
    user_id: int

    # Бот для уведомлений: недвижимость
    realty_bot_token: Optional[str] = None
    realty_chat_id: Optional[int] = None

    # Chat ID для уведомлений ПВЗ (общий бот workers_service)
    workers_chat_id: Optional[int] = None

    # Defaults
    default_mode: Literal["worker", "employer"] = "worker"


# ===== Модели задач =====

class ActiveTask(BaseModel):
    """Активная задача пользователя"""
    id: Optional[int] = None
    user_id: int
    task_id: str  # UUID из микросервиса
    service: Literal["workers", "realty"]
    task_type: Optional[str] = None  # 'monitoring', 'avito', 'cian'
    status: str = "running"
    created_at: datetime


# ===== Модели для Workers Service =====

class WorkersFilters(BaseModel):
    """Фильтры для мониторинга ПВЗ"""
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    min_price: Optional[int] = None
    max_price: Optional[int] = None
    shk_filter: str = "любое"


class StartMonitoringRequest(BaseModel):
    """Запрос на запуск мониторинга ПВЗ"""
    user_id: int
    mode: Literal["worker", "employer"]
    chats: list[str]
    filters: WorkersFilters
    session_path: str
    blacklist_session_path: str
    notification_chat_id: int  # Chat ID для уведомлений (общий бот workers_service)
    parse_history_days: int = 3


class MonitoringStatus(BaseModel):
    """Статус мониторинга"""
    task_id: str
    status: Literal["pending", "running", "stopped", "error"]
    mode: str
    stats: Optional[dict] = None


# ===== Модели для Realty Service =====

class StartParsingRequest(BaseModel):
    """Запрос на запуск парсинга недвижимости"""
    user_id: int
    notification_bot_token: str
    notification_chat_id: int
    avito_url: Optional[str] = None
    cian_url: Optional[str] = None
    pages: int = 3


class ParsingStatus(BaseModel):
    """Статус парсинга недвижимости"""
    task_id: str
    user_id: int
    status: Literal["pending", "running", "completed", "failed", "stopped"]
    progress: Optional[dict] = None
    started_at: datetime
    updated_at: datetime
    completed_at: Optional[datetime] = None
    error_message: Optional[str] = None
    results: Optional[dict] = None


# ===== Модели для Blacklist =====

class BlacklistCheckResult(BaseModel):
    """Результат проверки в черном списке"""
    found: bool
    username: Optional[str] = None
    user_id: Optional[int] = None
    match_type: Optional[str] = None
    match_value: Optional[str] = None
    chat: Optional[str] = None
    message_link: Optional[str] = None
    message_id: Optional[int] = None
    message_date: Optional[str] = None
    extracted_info: Optional[dict] = None
    message_text: Optional[str] = None
    messages_checked: Optional[int] = None
    chats_checked: Optional[list[str]] = None
    message: Optional[str] = None


class BlacklistChat(BaseModel):
    """Чат черного списка"""
    chat_username: str
    chat_title: str
    added_at: str
    is_active: int
