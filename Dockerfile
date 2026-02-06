FROM python:3.11-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Копирование requirements
COPY requirements.txt .

# Установка Python зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода
COPY parserhub/ ./parserhub/
COPY .env .env

# Создание директории для БД
RUN mkdir -p /app/data

# Переменная окружения для Python
ENV PYTHONUNBUFFERED=1

# Запуск бота
CMD ["python", "-m", "parserhub.bot"]
