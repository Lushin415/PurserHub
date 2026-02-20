# commercebot — Quick Start Guide

---

## 1. Запуск локально в Docker

**Шаг 1.** Создайте `.env` из примера и заполните:

```bash
cp .env.example .env
```

```env
BOT_TOKEN=токен_от_BotFather
ADMIN_ID=ваш_Telegram_ID
API_ID=ваш_api_id
API_HASH=ваш_api_hash
```

> `API_ID` и `API_HASH` получают на [my.telegram.org](https://my.telegram.org) → API development tools.

**Шаг 2.** Запустите все сервисы:

```bash
docker compose up -d
```

**Шаг 3.** Проверьте, что все три контейнера запущены:

```bash
docker compose ps
```

---

## 2. Запуск на сервере в Docker

### Шаг 1 — Установка Docker (Ubuntu/Debian)

```bash
# Обновить систему
sudo apt update && sudo apt upgrade -y

# Установить зависимости
sudo apt install -y curl git ca-certificates gnupg

# Добавить официальный репозиторий Docker
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# Установить Docker и плагин Compose
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# Добавить текущего пользователя в группу docker (чтобы не нужен sudo)
sudo usermod -aG docker $USER

# Применить изменения группы без перезахода
newgrp docker

# Проверить установку
docker --version
docker compose version
```

### Шаг 2 — Клонировать репозитории

Все три репозитория должны лежать **рядом** в одной директории — это требование `docker-compose.yml`.

```bash
# Создать рабочую директорию
mkdir -p /opt/commercebot && cd /opt/commercebot

# Клонировать все три репозитория
git clone https://github.com/Lushin415/PurserHub.git
git clone https://github.com/Lushin415/workers_service.git
git clone https://github.com/Lushin415/parser_avito_cian.git

# Должна получиться такая структура:
# /opt/commercebot/
# ├── PurserHub/
# ├── workers_service/
# └── parser_avito_cian/
```

### Шаг 3 — Подготовить окружение (один скрипт)

```bash
cd /opt/commercebot/PurserHub
bash setup.sh
```

Скрипт автоматически создаст все нужные директории, пустые файлы для Docker volumes и скопирует `.env.example` → `.env` для каждого сервиса.

### Шаг 4 — Заполнить .env файлы

```bash
nano /opt/commercebot/PurserHub/.env
```
```env
BOT_TOKEN=токен_от_BotFather
ADMIN_ID=ваш_Telegram_ID
API_ID=ваш_api_id
API_HASH=ваш_api_hash
```

```bash
nano /opt/commercebot/workers_service/.env
```
```env
API_ID=ваш_api_id      # те же, что в PurserHub
API_HASH=ваш_api_hash
```

### Шаг 5 — Запустить

```bash
cd /opt/commercebot/PurserHub
docker compose up -d

# Проверить, что все три контейнера запущены
docker compose ps
```

---

## 3. Остановка и перезапуск

```bash
# Остановить все сервисы (graceful shutdown ~30 сек)
docker compose down

# Перезапустить конкретный сервис
docker compose restart parserhub
docker compose restart workers-service
docker compose restart realty-monitor

# Пересобрать и перезапустить после изменений кода
docker compose up -d --build

# Пересобрать без кеша (если изменения не применяются)
docker compose build --no-cache && docker compose up -d
```

---

## 4. Логи

```bash
# Следить за логами в реальном времени
docker compose logs -f parserhub
docker compose logs -f workers-service
docker compose logs -f realty-monitor

# Последние 100 строк конкретного сервиса
docker compose logs --tail=100 parserhub

# Все сервисы сразу
docker compose logs -f
```

---

## 5. Первичная настройка бота

### Авторизация Telegram-аккаунта

1. Откройте бота, отправьте `/start`
2. Перейдите **👤 Мой аккаунт**
3. Выберите **🔑 Авторизация парсера ПВЗ** (и/или **🔑 Авторизация Черного списка**)
4. Введите номер телефона в формате `+79991234567`
5. Telegram пришлёт код подтверждения — введите его **через пробелы**: `1 2 3 4 5` или `12 456`
6. Если включена двухфакторная аутентификация — введите пароль 2FA

### Настройка чатов (администратор)

1. Перейдите в **⚙️ Панель администратора**
2. **📝 Чаты ПВЗ** → Изменить список → введите чаты по одному в строке:
   ```
   @pvz_vacancy
   @pvz_forum/12345
   ```
3. **📝 Чаты ЧС** → аналогично

> Формат `@chat/topic_id` используется для мониторинга конкретного топика форума.

### Запуск мониторинга

- **👷 Мониторинг ПВЗ** → выбрать режим → город → даты → ставки → Запустить
- **🏠 Недвижимость** → источник → вставить URL с фильтрами → Запустить
- **⚫ Черный список** → ввести `@username` для проверки

---

## 6. Проверка работы

```bash
# Health-check микросервисов
curl http://localhost:8002/health   # workers-service
curl http://localhost:8009/health   # realty-monitor

# Статус запущенных контейнеров
docker compose ps

# Проверить сеть между контейнерами
docker network inspect parserhub_parserhub_network
```

В Telegram: запустите мониторинг ПВЗ — бот должен ответить сообщением об успешном старте задачи.

---

## 7. Полезные команды

```bash
# Войти в контейнер для отладки
docker exec -it parserhub bash
docker exec -it workers-service bash
docker exec -it realty-monitor bash

# Посмотреть все Docker volumes
docker volume ls

# Сбросить все Pyrogram-сессии (переавторизация всех пользователей)
docker volume rm parserhub_shared_sessions

# Пересоздать volume заново
docker volume create parserhub_shared_sessions
```
