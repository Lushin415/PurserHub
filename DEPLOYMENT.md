# 🚀 Развёртывание ParserHub в Docker

## 📁 Структура проектов на сервере

```
/home/alex/
├── avito_cian_parser/       # Проект 1
│   ├── Dockerfile
│   ├── requirements.txt
│   └── ... (ваш код)
│
├── workers_service/         # Проект 2
│   ├── Dockerfile
│   ├── requirements.txt
│   └── ... (ваш код)
│
└── PurserHub/              # Проект 3 (этот)
    ├── docker-compose.yml  # ← Главный файл оркестрации
    ├── Dockerfile
    ├── .env
    ├── parserhub/
    └── ...
```

## ⚙️ Шаг 1: Подготовка .env

В директории `PurserHub/` создайте `.env`:

```bash
cd ~/PurserHub
nano .env
```

Содержимое:

```env
# ===== TELEGRAM BOT =====
BOT_TOKEN=7123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw

# ===== TELEGRAM API =====
API_ID=12345678
API_HASH=0123456789abcdef0123456789abcdef

# ===== МИКРОСЕРВИСЫ (в Docker используются имена контейнеров) =====
WORKERS_SERVICE_URL=http://workers_service:8002
REALTY_SERVICE_URL=http://avito_cian_parser:8001

# ===== PATHS (пути внутри контейнера) =====
DB_PATH=/app/data/parserhub.db
SESSIONS_DIR=/shared/sessions
LOG_PATH=/app/data/parserhub.log

# ===== SERVER =====
HOST=0.0.0.0
PORT=8003
```

## 🔨 Шаг 2: Проверка Dockerfile в других проектах

### avito_cian_parser/Dockerfile

Убедитесь, что есть Dockerfile примерно такой:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]  # Или ваша точка входа
```

### workers_service/Dockerfile

Аналогично:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Системные зависимости (если нужны)
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Создать директорию для сессий
RUN mkdir -p /shared/sessions

ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]  # Или ваша точка входа
```

## 🐳 Шаг 3: Запуск всех контейнеров

Из директории `PurserHub/`:

```bash
cd ~/PurserHub

# Собрать и запустить ВСЕ контейнеры
docker-compose up -d --build
```

Что произойдёт:
1. Docker соберёт образы:
   - `avito_cian_parser:latest`
   - `workers_service:latest`
   - `purserhub:latest`
2. Создаст volume `shared_sessions`
3. Создаст сеть `parserhub_network`
4. Запустит 3 контейнера

## 📊 Шаг 4: Проверка статуса

```bash
# Статус контейнеров
docker-compose ps

# Должно быть:
# NAME                   STATUS
# avito_cian_parser      Up
# workers_service        Up
# parserhub_bot          Up

# Логи ParserHub
docker-compose logs -f parserhub

# Логи workers_service
docker-compose logs -f workers_service

# Логи avito_cian_parser
docker-compose logs -f avito_cian_parser
```

## 🔍 Проверка работоспособности

### 1. Health checks (внутри контейнера)

```bash
# Проверка workers_service
curl http://localhost:8002/health

# Проверка avito_cian_parser
curl http://localhost:8001/health
```

### 2. Проверка shared volume

```bash
# Войти в контейнер ParserHub
docker exec -it parserhub_bot bash

# Проверить директорию сессий
ls -la /shared/sessions/

# Создать тестовый файл
touch /shared/sessions/test.txt

# Выйти
exit

# Войти в контейнер workers_service
docker exec -it workers_service bash

# Проверить что файл виден
ls -la /shared/sessions/test.txt  # Должен быть виден!

# Выйти
exit
```

### 3. Проверка сети (контейнеры видят друг друга)

```bash
# Войти в ParserHub
docker exec -it parserhub_bot bash

# Проверить связь
curl http://workers_service:8002/health
curl http://avito_cian_parser:8001/health

# Выйти
exit
```

## 🛑 Управление контейнерами

```bash
# Остановить все
docker-compose down

# Остановить и удалить volumes (ОСТОРОЖНО! Удалятся сессии!)
docker-compose down -v

# Перезапустить один контейнер
docker-compose restart parserhub

# Пересобрать один контейнер
docker-compose up -d --build parserhub

# Посмотреть логи последних 100 строк
docker-compose logs --tail=100 parserhub
```

## 📝 Структура после запуска

```
Docker Host
├── Volume: shared_sessions/
│   ├── 338908929_parser.session      ← Создаётся ParserHub
│   ├── 338908929_blacklist.session   ← Создаётся ParserHub
│   └── (читаются workers_service)
│
├── Container: avito_cian_parser
│   └── Port 8001 → localhost:8001
│
├── Container: workers_service
│   ├── Port 8002 → localhost:8002
│   └── Mount: shared_sessions → /shared/sessions
│
└── Container: parserhub_bot
    ├── Port 8003 → localhost:8003
    ├── Mount: shared_sessions → /shared/sessions
    └── Mount: ./data → /app/data (БД и логи)
```

## ⚠️ Важные моменты

### 1. Пути к проектам в docker-compose.yml

Проверьте в `docker-compose.yml`:

```yaml
avito_cian_parser:
  build:
    context: ../avito_cian_parser  # ← Относительный путь!

workers_service:
  build:
    context: ../workers_service    # ← Относительный путь!
```

Если ваши проекты в других местах, измените пути:

```yaml
# Если проекты рядом:
context: ../avito_cian_parser

# Если проекты в другом месте:
context: /home/alex/projects/avito_cian_parser

# Если используете готовые образы:
image: my-registry.com/avito_cian_parser:latest
# (тогда удалите блок build:)
```

### 2. Переменные окружения

Микросервисы должны читать переменные:
- `SESSIONS_DIR=/shared/sessions`
- `HOST=0.0.0.0`
- `PORT=8001` / `PORT=8002`

### 3. Логи доступны на хосте

```bash
# БД и логи ParserHub сохраняются на хосте
ls -la ~/PurserHub/data/
# → parserhub.db
# → parserhub.log
```

## 🔧 Отладка проблем

### Проблема: контейнеры не видят друг друга

```bash
# Проверить сеть
docker network ls
docker network inspect parserhub_network

# Убедиться что контейнеры в одной сети
docker inspect parserhub_bot | grep -A 10 Networks
docker inspect workers_service | grep -A 10 Networks
```

### Проблема: сессии не видны в workers_service

```bash
# Проверить volume
docker volume inspect parserhub_shared_sessions

# Проверить mount points
docker inspect workers_service | grep -A 5 Mounts
docker inspect parserhub_bot | grep -A 5 Mounts
```

### Проблема: API недоступен

```bash
# Проверить порты
docker-compose ps

# Проверить логи
docker-compose logs workers_service | grep -i error
docker-compose logs avito_cian_parser | grep -i error
```

## ✅ Готово!

После запуска откройте Telegram и найдите вашего ParserHub бота → `/start`

Все три сервиса работают в Docker и общаются между собой! 🎉
