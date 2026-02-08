# ParserHub

Telegram-бот оркестратор для управления парсерами. Управляет сессиями пользователей и координирует работу микросервисов через API.

## Функционал

- **Авторизация** — создание Pyrogram-сессий Telegram для каждого пользователя
- **Мониторинг ПВЗ** — real-time мониторинг чатов с вакансиями (через `workers_service`)
- **Парсинг недвижимости** — парсинг Avito/Cian (через `parser_avito_cian`)
- **Черный список** — проверка пользователей по базе чатов ЧС
- **Подписки** — система платных подписок через Telegram Payments / YooKassa (временно отключена)
- **Админ-панель** — управление подписками, доходами и администраторами (временно отключена)

## Архитектура

```
ParserHub (оркестратор)
    |
    |--- workers_service    (порт 8002) — мониторинг ПВЗ + ЧС
    |--- parser_avito_cian  (порт 8001) — парсинг недвижимости
```

Все три сервиса связаны через Docker-сеть. ParserHub создает Pyrogram-сессии, а `workers_service` использует их для мониторинга. Сессии хранятся в общем Docker volume.

## Структура проекта

```
PurserHub/
├── parserhub/
│   ├── bot.py                  # Главный модуль: инициализация, регистрация handlers
│   ├── config.py               # Конфигурация из .env (Pydantic Settings)
│   ├── db_service.py           # SQLite: пользователи, настройки, задачи, админы, платежи
│   ├── models.py               # Pydantic-модели данных
│   ├── session_manager.py      # Управление Pyrogram-сессиями
│   ├── api_client.py           # HTTP-клиенты к микросервисам
│   ├── validators.py           # Валидация ввода (телефон, даты, цены, URL)
│   ├── handlers/
│   │   ├── start.py            # /start, главное меню
│   │   ├── auth.py             # Авторизация Telegram-аккаунтов
│   │   ├── settings.py         # Настройки уведомлений
│   │   ├── workers.py          # Мониторинг ПВЗ
│   │   ├── realty.py           # Парсинг недвижимости
│   │   ├── blacklist.py        # Черный список
│   │   ├── subscription.py     # Подписки и платежи (временно отключен)
│   │   └── admin.py            # Админ-панель (временно отключена)
│   └── services/
│       └── subscription_service.py  # Логика подписок
├── data/                       # БД и логи (создается автоматически)
├── sessions/                   # Pyrogram-сессии (создается автоматически)
├── run.py                      # Точка входа для локального запуска
├── Dockerfile
├── docker-compose.yml          # Оркестрация всех трех сервисов
├── requirements.txt
├── .env.example
└── .gitignore
```

## Требования

- Python 3.11+
- Telegram Bot Token ([@BotFather](https://t.me/BotFather))
- Telegram API credentials ([my.telegram.org](https://my.telegram.org))
- Микросервисы `workers_service` и `parser_avito_cian` (отдельные репозитории)

---

## Запуск на VPS (продакшн)

### 1. Подготовка сервера

```bash
# Обновление системы
sudo apt update && sudo apt upgrade -y

# Установка Docker и Docker Compose
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable docker
sudo usermod -aG docker $USER
# Перелогиниться для применения группы
```

### 2. Клонирование репозиториев

Все три проекта должны лежать рядом в одной директории:

```bash
mkdir -p /opt/parserhub && cd /opt/parserhub

git clone <url> PurserHub
git clone <url> workers_service
git clone <url> parser_avito_cian
```

Итоговая структура:

```
/opt/parserhub/
├── PurserHub/              # Этот репозиторий
├── workers_service/        # Микросервис мониторинга ПВЗ
└── parser_avito_cian/      # Микросервис парсинга недвижимости
```

### 3. Настройка переменных окружения

**PurserHub/.env:**

```bash
cd PurserHub
cp .env.example .env
nano .env
```

```ini
BOT_TOKEN=123456:ABC-DEF...       # Токен от @BotFather
ADMIN_ID=987654321                 # Ваш Telegram ID (владелец бота)
API_ID=12345678                    # С my.telegram.org
API_HASH=abcdef1234567890          # С my.telegram.org
PROVIDER_TOKEN=                    # YooKassa (заполнить при включении подписок)
```

Остальные переменные (URL микросервисов, пути) переопределяются в `docker-compose.yml` и менять их в `.env` не нужно.

**workers_service/.env:**

```bash
cd ../workers_service
cp .env.example .env
nano .env
```

```ini
API_ID=12345678                    # Те же, что в PurserHub
API_HASH=abcdef1234567890
```

> В `workers_service/.env` указываются **только** `API_ID` и `API_HASH`. Параметры `BOT_TOKEN`, `USER_ID`, `CHATS` передаются через API от ParserHub.

**parser_avito_cian** — настраивается через `config.toml` внутри проекта. Параметры `tg_token`, `tg_chat_id`, `urls` должны быть **закомментированы** (передаются через API).

### 4. Сборка и запуск

```bash
cd /opt/parserhub/PurserHub

# Сборка всех контейнеров
docker compose build

# Запуск в фоне
docker compose up -d
```

### 5. Проверка работоспособности

```bash
# Статус контейнеров
docker compose ps

# Логи ParserHub
docker compose logs -f parserhub

# Логи конкретного сервиса
docker compose logs -f workers_service
docker compose logs -f avito_cian_parser
```

### 6. Управление

```bash
# Остановка
docker compose down

# Перезапуск после изменений в коде
docker compose build --no-cache && docker compose up -d

# Просмотр БД (из контейнера)
docker exec -it parserhub_bot sqlite3 /app/data/parserhub.db ".tables"
```

### 7. Автозапуск при перезагрузке

Параметр `restart: unless-stopped` в `docker-compose.yml` уже обеспечивает автозапуск. Убедитесь, что Docker-демон включен:

```bash
sudo systemctl enable docker
```

---

## Локальный запуск (разработка)

### 1. Подготовка окружения

```bash
cd PurserHub

# Создание виртуального окружения
python3.11 -m venv .venv
source .venv/bin/activate

# Установка зависимостей
pip install -r requirements.txt
```

### 2. Настройка .env

```bash
cp .env.example .env
```

Заполните `.env`:

```ini
BOT_TOKEN=123456:ABC-DEF...
ADMIN_ID=987654321
API_ID=12345678
API_HASH=abcdef1234567890
PROVIDER_TOKEN=

# Для локального запуска — порты на localhost
WORKERS_SERVICE_URL=http://localhost:8002
REALTY_SERVICE_URL=http://localhost:8001

# Локальные пути
DB_PATH=data/parserhub.db
SESSIONS_DIR=./sessions
LOG_PATH=data/parserhub.log
```

### 3. Запуск микросервисов

Микросервисы нужно запустить **перед** ParserHub. Каждый в отдельном терминале:

**Терминал 1 — workers_service:**

```bash
cd ../workers_service
source .venv/bin/activate
python run.py
# Слушает на http://localhost:8002
```

**Терминал 2 — parser_avito_cian:**

```bash
cd ../parser_avito_cian
source .venv/bin/activate
python run.py
# Слушает на http://localhost:8001
```

### 4. Запуск ParserHub

**Терминал 3:**

```bash
cd PurserHub
source .venv/bin/activate
python run.py
```

Бот начнет polling. Откройте его в Telegram и отправьте `/start`.

### 5. Создание директорий (при первом запуске)

Если `data/` и `sessions/` не существуют — бот создаст их автоматически. Но при необходимости:

```bash
mkdir -p data sessions
```

---

## Переменные окружения

| Переменная | Обязательна | Описание |
|---|---|---|
| `BOT_TOKEN` | Да | Токен Telegram-бота от @BotFather |
| `ADMIN_ID` | Нет | Telegram ID владельца бота (администратор). По умолчанию `0` |
| `API_ID` | Да | Telegram API ID с my.telegram.org |
| `API_HASH` | Да | Telegram API Hash с my.telegram.org |
| `PROVIDER_TOKEN` | Нет | Токен YooKassa для платежей. Пустой — подписки отключены |
| `WORKERS_SERVICE_URL` | Нет | URL workers_service. По умолчанию `http://localhost:8002` |
| `REALTY_SERVICE_URL` | Нет | URL parser_avito_cian. По умолчанию `http://localhost:8001` |
| `DB_PATH` | Нет | Путь к SQLite БД. По умолчанию `parserhub.db` |
| `SESSIONS_DIR` | Нет | Директория Pyrogram-сессий. По умолчанию `./sessions` |
| `LOG_PATH` | Нет | Путь к лог-файлу. По умолчанию `parserhub.log` |

---

## Включение подписок

Система подписок реализована, но временно отключена. Для включения:

1. Получите `PROVIDER_TOKEN` через @BotFather > Payments > YooKassa
2. Добавьте его в `.env`
3. Раскомментируйте 4 блока кода:

| Файл | Строка | Что раскомментировать |
|---|---|---|
| `parserhub/handlers/start.py` | ~44 | Кнопка "Подписка" в главном меню |
| `parserhub/bot.py` | ~150 | `register_subscription_handlers(app)` |
| `parserhub/bot.py` | ~154 | `register_admin_handlers(app)` |
| `parserhub/handlers/workers.py` | ~365 | Блок проверки подписки в `confirm_start()` |
| `parserhub/handlers/realty.py` | ~252 | Блок проверки подписки в `confirm_start()` |

4. Пересоберите и перезапустите: `docker compose build --no-cache && docker compose up -d`

---

## Решение проблем

**Бот не отвечает:**
```bash
docker compose logs -f parserhub  # Проверить логи
docker compose ps                  # Проверить статус контейнеров
```

**Ошибка авторизации `PHONE_CODE_EXPIRED`:**
- Telegram отправляет код **в приложение**, а не по SMS
- **Не открывайте** уведомление в официальном Telegram-клиенте
- Просто скопируйте код и вставьте в бота

**Изменения в коде не применяются:**
```bash
docker compose build --no-cache && docker compose up -d
```

**Потеря соединения Pyrogram:**
- Встроена автоматическая переподключение с проверкой каждые 30 секунд

**Сброс БД:**
```bash
docker compose down
rm data/parserhub.db
docker compose up -d
```
