#!/bin/bash
# ScanBot — первоначальная настройка окружения
# Запускать из директории PurserHub: bash setup.sh

set -e

SCANBOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PURSERHUB_DIR="$SCANBOT_DIR/PurserHub"
WORKERS_DIR="$SCANBOT_DIR/workers_service"
REALTY_DIR="$SCANBOT_DIR/parser_avito_cian"

echo "=== ScanBot Setup ==="
echo "Корневая директория: $SCANBOT_DIR"
echo ""

# --- Проверка наличия репозиториев ---
for dir in "$WORKERS_DIR" "$REALTY_DIR"; do
    if [ ! -d "$dir" ]; then
        echo "ОШИБКА: директория не найдена: $dir"
        echo "Убедитесь, что все три репозитория лежат рядом:"
        echo "  $SCANBOT_DIR/PurserHub/"
        echo "  $SCANBOT_DIR/workers_service/"
        echo "  $SCANBOT_DIR/parser_avito_cian/"
        exit 1
    fi
done

# --- workers_service: директории и .env ---
echo "[1/3] workers_service..."
mkdir -p "$WORKERS_DIR/data/db"
mkdir -p "$WORKERS_DIR/data/logs"
if [ ! -f "$WORKERS_DIR/.env" ]; then
    cp "$WORKERS_DIR/.env.example" "$WORKERS_DIR/.env"
    echo "  Создан workers_service/.env из .env.example"
else
    echo "  workers_service/.env уже существует, пропускаю"
fi

# --- parser_avito_cian: файлы и директории ---
echo "[2/3] parser_avito_cian..."
mkdir -p "$REALTY_DIR/logs"
mkdir -p "$REALTY_DIR/result"
[ ! -f "$REALTY_DIR/cookies.json" ]      && echo '{}' > "$REALTY_DIR/cookies.json"      && echo "  Создан cookies.json"
[ ! -f "$REALTY_DIR/cookies_cian.json" ] && echo '{}' > "$REALTY_DIR/cookies_cian.json" && echo "  Создан cookies_cian.json"
[ ! -f "$REALTY_DIR/database.db" ]       && touch "$REALTY_DIR/database.db"              && echo "  Создан database.db"

# --- PurserHub: .env ---
echo "[3/3] PurserHub..."
if [ ! -f "$PURSERHUB_DIR/.env" ]; then
    cp "$PURSERHUB_DIR/.env.example" "$PURSERHUB_DIR/.env"
    echo "  Создан PurserHub/.env из .env.example"
else
    echo "  PurserHub/.env уже существует, пропускаю"
fi

echo ""
echo "=== Готово ==="
echo ""
echo "Следующий шаг — заполните .env файлы:"
echo ""
echo "  nano $PURSERHUB_DIR/.env"
echo "       BOT_TOKEN=...   (токен от @BotFather)"
echo "       ADMIN_ID=...    (ваш Telegram ID)"
echo "       API_ID=...      (my.telegram.org)"
echo "       API_HASH=...    (my.telegram.org)"
echo ""
echo "  nano $WORKERS_DIR/.env"
echo "       API_ID=...      (те же, что выше)"
echo "       API_HASH=...    (те же, что выше)"
echo ""
echo "После заполнения .env запустите:"
echo "  cd $PURSERHUB_DIR && docker compose up -d"
