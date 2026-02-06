# ТЗ: ParserHub — Оркестровый Telegram Бот

## Что это

Telegram бот — единая точка входа для пользователя. Управляет двумя микросервисами:
- **workers_service** (порт 8002) — мониторинг Telegram чатов ПВЗ
- **avito_cian_parser** (порт 8001) — парсинг недвижимости Avito/Cian

Сам НЕ парсит. Только управляет, хранит сессии и настройки пользователей.

---

## Архитектура

```
Пользователь (Telegram)
       ↓
   ParserHub (бот, порт 8003)
   ├── SQLite (пользователи, настройки)
   ├── /shared/sessions/ (Pyrogram сессии)
   │
   ├──→ workers_service:8002 (HTTP API)
   └──→ avito_cian_parser:8001 (HTTP API)
```

Shared volume для сессий (Docker):
```
/shared/sessions/
├── {user_id}_parser.session       # сессия для парсинга ПВЗ
├── {user_id}_blacklist.session    # сессия для поиска в ЧС
├── 123456789_parser.session       # другой пользователь
└── 123456789_blacklist.session
```

---

## Технологический стек

```
python-telegram-bot==21.0   — Telegram бот
httpx                       — HTTP клиент (вызовы API микросервисов)
pyrogram                    — создание/авторизация сессий пользователей
aiosqlite                   — БД пользователей
loguru                      — логирование
pydantic                    — валидация
```

---

## Структура проекта

```
parserhub/
├── bot.py                  # Точка входа, запуск бота
├── config.py               # Конфигурация из .env
├── db_service.py           # SQLite (пользователи, настройки)
├── models.py               # Pydantic/dataclass модели
├── api_client.py           # HTTP клиент к микросервисам
├── session_manager.py      # Управление Pyrogram сессиями
├── handlers/
│   ├── start.py            # /start, главное меню
│   ├── auth.py             # Подключение Telegram аккаунта (2 сессии)
│   ├── workers.py          # Меню "Мониторинг ПВЗ"
│   ├── realty.py           # Меню "Парсинг недвижимости"
│   ├── blacklist.py        # Меню "Черный список"
│   └── settings.py         # Настройки пользователя
├── requirements.txt
├── Dockerfile
├── .env.example
└── README.md
```

---

## База данных

### Таблица users

```sql
CREATE TABLE users (
    user_id INTEGER PRIMARY KEY,          -- Telegram User ID
    username TEXT,                          -- @username
    full_name TEXT,
    phone TEXT,                             -- номер телефона (для авторизации)
    is_parser_authorized BOOLEAN DEFAULT 0, -- сессия парсера ПВЗ создана?
    is_blacklist_authorized BOOLEAN DEFAULT 0, -- сессия ЧС создана?
    created_at TEXT NOT NULL,
    last_active TEXT
);
```

### Таблица user_settings

```sql
CREATE TABLE user_settings (
    user_id INTEGER PRIMARY KEY,

    -- Бот для уведомлений: Avito/Cian (недвижимость)
    realty_bot_token TEXT,
    realty_chat_id INTEGER,

    -- Бот для уведомлений: ПВЗ (работники/работодатели)
    workers_bot_token TEXT,
    workers_chat_id INTEGER,

    -- Defaults
    default_mode TEXT DEFAULT 'worker',  -- worker/employer

    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

### Таблица active_tasks

```sql
CREATE TABLE active_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    task_id TEXT NOT NULL,              -- task_id из микросервиса
    service TEXT NOT NULL,              -- 'workers' или 'realty'
    task_type TEXT,                      -- 'monitoring', 'avito', 'cian'
    status TEXT DEFAULT 'running',
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);
```

---

## Flow авторизации (handlers/auth.py)

У пользователя ДВЕ отдельные авторизации:
- **Парсер ПВЗ** → `{user_id}_parser.session`
- **Черный список** → `{user_id}_blacklist.session`

Каждая авторизация — одинаковый flow, но создаёт разную сессию.

### Шаги:

```
1. Пользователь нажимает "Авторизация парсера ПВЗ" (или "Авторизация ЧС")
2. Бот: "Введите номер телефона в формате +7XXXXXXXXXX"
3. Пользователь: +79991234567
4. session_manager создаёт Pyrogram клиент:
   Client(name=f"sessions/{user_id}_parser", ...)  # или _blacklist
   await client.connect()
   code_hash = await client.send_code(phone)
5. Бот: "Введите код из Telegram"
6. Пользователь: 12345
7. await client.sign_in(phone, code_hash, code)
8. (если 2FA) Бот: "Введите пароль двухфакторной аутентификации"
9. await client.check_password(password)
10. Сессия сохранена → обновляем БД:
    users.is_parser_authorized = 1  (или is_blacklist_authorized = 1)
11. Бот: "Аккаунт подключён! ✅"
```

---

## Меню бота (дерево кнопок)

```
/start
└── Главное меню
    │
    ├── 👤 Мой аккаунт
    │   ├── 🔑 Авторизация парсера ПВЗ
    │   │   ├── (не подключён ❌ / подключён ✅)
    │   │   └── → создаёт {user_id}_parser.session
    │   ├── 🔑 Авторизация Черного списка
    │   │   ├── (не подключён ❌ / подключён ✅)
    │   │   └── → создаёт {user_id}_blacklist.session
    │   ├── Статус подключений
    │   │   └── "Парсер ПВЗ: ✅ | ЧС: ❌"
    │   └── Отключить аккаунт (удалить обе сессии)
    │
    ├── 👷 Мониторинг ПВЗ
    │   ├── Запустить мониторинг
    │   │   ├── Режим: Работники / Работодатели
    │   │   ├── Выбор чатов (список или ввод @username)
    │   │   ├── Фильтры (дата, цена, ШК)
    │   │   └── Подтверждение → POST workers_service/workers/start
    │   ├── Мои задачи (активные)
    │   │   ├── Статус → GET workers_service/workers/status/{id}
    │   │   └── Остановить → POST workers_service/workers/stop/{id}
    │   └── История
    │
    ├── 🏠 Парсинг недвижимости
    │   ├── Avito
    │   │   ├── Ввод ссылки
    │   │   ├── Кол-во страниц
    │   │   └── Запустить → POST realty_service/parse/start
    │   ├── Cian
    │   │   ├── Ввод ссылки
    │   │   ├── Кол-во страниц
    │   │   └── Запустить → POST realty_service/parse/start
    │   ├── Мои задачи
    │   │   ├── Статус → GET realty_service/parse/status/{id}
    │   │   └── Остановить → POST realty_service/parse/stop/{id}
    │   └── История
    │
    ├── ⚫ Черный список
    │   ├── Проверить по @username
    │   │   └── Ввод → POST workers_service/blacklist/check
    │   ├── Чаты ЧС
    │   │   ├── Список → GET workers_service/blacklist/chats
    │   │   ├── Добавить → POST workers_service/blacklist/chats/add
    │   │   └── Удалить → POST workers_service/blacklist/chats/remove
    │   └── Назад
    │
    └── ⚙️ Настройки
        ├── 👷 Бот для ПВЗ (работники/работодатели)
        │   ├── Токен бота: ***задан*** ✅ (или ❌ не задан)
        │   ├── Chat ID: 338908929
        │   └── Изменить
        ├── 🏠 Бот для недвижимости (Avito/Cian)
        │   ├── Токен бота: ***задан*** ✅ (или ❌ не задан)
        │   ├── Chat ID: 338908929
        │   └── Изменить
        └── Назад
```

---

## API Client (api_client.py)

HTTP клиент для вызова микросервисов. Используем `httpx.AsyncClient`.

```python
class WorkersAPI:
    """Клиент к workers_service"""
    BASE_URL = "http://workers_service:8002"  # Docker
    # BASE_URL = "http://localhost:8002"      # Локально

    async def start_monitoring(
        self,
        user_id: int,
        mode: str,                    # "worker" / "employer"
        chats: list[str],
        filters: dict,
        session_path: str,            # путь к сессии пользователя
        blacklist_session_path: str,  # путь к сессии ЧС
        notification_bot_token: str,  # workers_bot_token из настроек
        notification_chat_id: int,    # workers_chat_id из настроек
        parse_history_days: int = 3
    ) -> dict:
        """POST /workers/start"""

    async def stop_monitoring(self, task_id: str) -> dict:
        """POST /workers/stop/{task_id}"""

    async def get_status(self, task_id: str) -> dict:
        """GET /workers/status/{task_id}"""

    async def get_found_items(self, task_id: str, limit: int = 50) -> dict:
        """GET /workers/list/{task_id}"""

    async def check_blacklist(self, username: str) -> dict:
        """POST /blacklist/check?username={username}"""

    async def get_blacklist_chats(self) -> dict:
        """GET /blacklist/chats"""

    async def add_blacklist_chat(self, chat_username: str) -> dict:
        """POST /blacklist/chats/add?chat_username={chat_username}"""

    async def remove_blacklist_chat(self, chat_username: str) -> dict:
        """POST /blacklist/chats/remove?chat_username={chat_username}"""


class RealtyAPI:
    """Клиент к avito_cian_parser"""
    BASE_URL = "http://avito_cian_parser:8001"  # Docker
    # BASE_URL = "http://localhost:8001"         # Локально

    async def start_parsing(
        self,
        user_id: int,
        avito_url: str | None,
        cian_url: str | None,
        pages: int,
        notification_bot_token: str,  # realty_bot_token из настроек
        notification_chat_id: int     # realty_chat_id из настроек
    ) -> dict:
        """POST /parse/start"""

    async def stop_parsing(self, task_id: str) -> dict:
        """POST /parse/stop/{task_id}"""

    async def get_status(self, task_id: str) -> dict:
        """GET /parse/status/{task_id}"""
```

---

## Session Manager (session_manager.py)

Управляет Pyrogram сессиями пользователей.

```python
class SessionManager:
    def __init__(self, sessions_dir: str, api_id: int, api_hash: str):
        self.sessions_dir = sessions_dir
        self.api_id = api_id
        self.api_hash = api_hash

    def get_session_path(self, user_id: int, session_type: str) -> str:
        """
        Путь к файлу сессии.
        session_type: 'parser' или 'blacklist'
        Возвращает: sessions/{user_id}_parser  (без .session)
        """

    def session_exists(self, user_id: int, session_type: str) -> bool:
        """Проверить существует ли файл сессии"""

    async def start_auth(self, user_id: int, session_type: str, phone: str) -> str:
        """
        Начать авторизацию: отправить код.
        Возвращает phone_code_hash.
        Клиент сохраняется в self._pending_clients[user_id]
        """

    async def confirm_code(self, user_id: int, code: str) -> str:
        """
        Подтвердить код.
        Возвращает: 'success' / 'need_2fa' / 'error'
        """

    async def confirm_2fa(self, user_id: int, password: str) -> bool:
        """Подтвердить 2FA пароль"""

    async def delete_session(self, user_id: int, session_type: str):
        """Удалить файл сессии"""
```

---

## Конфигурация (.env)

```env
# ===== TELEGRAM BOT =====
# Токен бота ParserHub (получить у @BotFather)
BOT_TOKEN=your_parserhub_bot_token

# ===== TELEGRAM API =====
# Для создания Pyrogram сессий пользователей (my.telegram.org)
API_ID=your_api_id
API_HASH=your_api_hash

# ===== МИКРОСЕРВИСЫ =====
WORKERS_SERVICE_URL=http://localhost:8002
REALTY_SERVICE_URL=http://localhost:8001

# ===== PATHS =====
DB_PATH=parserhub.db
SESSIONS_DIR=./sessions
LOG_PATH=parserhub.log

# ===== SERVER =====
HOST=0.0.0.0
PORT=8003
```

---

## Docker Compose (общий для всех сервисов)

```yaml
version: '3.8'

volumes:
  sessions:

services:
  parserhub:
    build: ./parserhub
    ports:
      - "8003:8003"
    volumes:
      - sessions:/shared/sessions
    env_file: ./parserhub/.env
    depends_on:
      - workers_service
      - realty_service

  workers_service:
    build: ./workers_service
    ports:
      - "8002:8002"
    volumes:
      - sessions:/shared/sessions
    env_file: ./workers_service/.env

  realty_service:
    build: ./avito_cian_parser
    ports:
      - "8001:8001"
    env_file: ./avito_cian_parser/.env
```

---

## TODO: Доработки workers_service для интеграции с ParserHub

### 1. Динамический session_path в /workers/start

**Сейчас:** session_path берётся из конфига (фиксированный).
**Нужно:** принимать в запросе от ParserHub.

```
POST /workers/start
{
    ...существующие поля...
    "session_path": "/shared/sessions/338908929_parser",       ← ДОБАВИТЬ
    "blacklist_session_path": "/shared/sessions/338908929_blacklist"  ← ДОБАВИТЬ
}
```

**Файлы для доработки:**
- `models_api.py` — добавить `session_path` и `blacklist_session_path` в `StartMonitoringRequest`
- `api.py` — передать session_path в start_monitoring_task
- `tasks.py` — использовать session_path из запроса вместо config.SESSION_PATH
- `parser.py` — принимать session_name как параметр (уже так)

### 2. Динамический session_path для blacklist

**Сейчас:** blacklist_service использует config.BLACKLIST_SESSION_PATH.
**Нужно:** принимать путь к сессии ЧС из запроса или из `/workers/start`.

**Файлы для доработки:**
- `blacklist_service.py` — метод `search_in_blacklist` должен принимать `session_name` как параметр
- `callback_handler.py` — при обработке кнопки "Проверить в ЧС" нужно знать путь к сессии ЧС этого пользователя
- `api.py` — endpoint `/blacklist/check` должен принимать `session_path` (или хранить маппинг user_id → session_path)

### 3. Убрать api_id и api_hash из запроса /workers/start

**Сейчас:** `api_id` и `api_hash` передаются в каждом запросе.
**Нужно:** брать из конфига workers_service (они одинаковые для всех, т.к. сессии уже авторизованы).

**Файлы для доработки:**
- `models_api.py` — убрать `api_id` и `api_hash` из `StartMonitoringRequest` (или сделать optional)
- `tasks.py` — если не переданы, брать из config

### 4. Хранение маппинга task_id → session_paths

**Проблема:** когда пользователь нажимает кнопку "Проверить в ЧС" в уведомлении, callback_handler должен знать какую сессию ЧС использовать для этого пользователя.

**Решение:** при создании задачи сохранять `blacklist_session_path` в таблице `tasks` (или в state_manager).

**Файлы для доработки:**
- `models_db.py` — добавить `blacklist_session_path` в Task
- `db_service.py` — миграция, сохранение
- `callback_handler.py` — при обработке кнопки доставать session_path из задачи

---

## Порядок разработки ParserHub

1. **bot.py + config.py** — скелет бота, конфигурация, запуск
2. **db_service.py + models.py** — БД пользователей, настройки
3. **handlers/start.py** — команда /start, главное меню с кнопками
4. **session_manager.py + handlers/auth.py** — авторизация (2 сессии)
5. **api_client.py** — HTTP клиент к workers_service и avito_cian_parser
6. **handlers/settings.py** — настройки (2 бота для уведомлений)
7. **handlers/workers.py** — мониторинг ПВЗ (запуск, статус, стоп)
8. **handlers/realty.py** — парсинг Avito/Cian (запуск, статус, стоп)
9. **handlers/blacklist.py** — проверка в ЧС, управление чатами
10. **Dockerfile + docker-compose.yml** — контейнеризация

---

## Проверка работоспособности

```bash
# 1. Запуск всех сервисов
docker-compose up -d

# 2. Проверка health
curl http://localhost:8001/health   # avito_cian_parser
curl http://localhost:8002/health   # workers_service
curl http://localhost:8003/health   # parserhub

# 3. В Telegram: открыть бота @ParserHub_bot
#    - /start
#    - Подключить аккаунт
#    - Настроить уведомления
#    - Запустить мониторинг
```
