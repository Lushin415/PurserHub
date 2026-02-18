"""Валидаторы для защиты от некорректного ввода данных"""
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple


class ValidationError(Exception):
    """Ошибка валидации пользовательского ввода"""
    pass


class Validators:
    """Набор валидаторов для пользовательских данных"""

    @staticmethod
    def validate_phone_number(phone: str) -> Tuple[bool, str, Optional[str]]:
        """
        Валидация номера телефона
        Returns: (valid, normalized_phone, error_message)
        """
        # Убрать пробелы, дефисы, скобки
        phone = re.sub(r'[\s\-\(\)]', '', phone)

        # Проверка на буквы
        if not phone.replace('+', '').isdigit():
            return False, phone, "❌ Номер должен содержать только цифры"

        # Нормализация: 8 -> +7
        if phone.startswith('8'):
            phone = '+7' + phone[1:]
        elif phone.startswith('7'):
            phone = '+7' + phone[1:]
        elif not phone.startswith('+'):
            phone = '+' + phone

        # Проверка длины (для России +7XXXXXXXXXX = 12 символов)
        if not phone.startswith('+7'):
            return False, phone, "❌ Номер должен начинаться с +7"

        if len(phone) != 12:
            return False, phone, "❌ Неверная длина номера (должно быть 11 цифр после +7)"

        return True, phone, None

    @staticmethod
    def validate_date(date_str: str) -> Tuple[bool, Optional[datetime], Optional[str]]:
        """
        Валидация даты в формате ДД.ММ.ГГГГ
        Returns: (valid, datetime_obj, error_message)
        """
        try:
            dt = datetime.strptime(date_str, "%d.%m.%Y")
        except ValueError:
            return False, None, "❌ Неверный формат даты. Используйте: ДД.ММ.ГГГГ (например, 05.02.2026)"

        # Проверка на адекватность (не раньше 2020 и не позже +2 года)
        min_date = datetime(2020, 1, 1)
        max_date = datetime.now() + timedelta(days=730)  # +2 года

        if dt < min_date:
            return False, None, "❌ Дата слишком старая (не раньше 01.01.2020)"

        if dt > max_date:
            return False, None, f"❌ Дата слишком далеко в будущем (не позже {max_date.strftime('%d.%m.%Y')})"

        return True, dt, None

    @staticmethod
    def validate_date_range(date_from: str, date_to: str) -> Tuple[bool, Optional[str]]:
        """
        Валидация диапазона дат
        Returns: (valid, error_message)
        """
        valid_from, dt_from, err_from = Validators.validate_date(date_from)
        if not valid_from:
            return False, err_from

        valid_to, dt_to, err_to = Validators.validate_date(date_to)
        if not valid_to:
            return False, err_to

        if dt_from > dt_to:
            return False, "❌ Дата начала не может быть позже даты окончания"

        # Проверка на слишком большой диапазон (>365 дней)
        if (dt_to - dt_from).days > 365:
            return False, "❌ Диапазон дат слишком большой (максимум 365 дней)"

        return True, None

    @staticmethod
    def validate_price(price_str: str, allow_zero: bool = True) -> Tuple[bool, Optional[int], Optional[str]]:
        """
        Валидация цены
        Returns: (valid, price_int, error_message)
        """
        # Проверка на буквы
        if not price_str.isdigit() and not (price_str.startswith('-') and price_str[1:].isdigit()):
            return False, None, "❌ Цена должна быть числом"

        try:
            price = int(price_str)
        except ValueError:
            return False, None, "❌ Неверный формат цены"

        # Проверка на отрицательное
        if price < 0:
            return False, None, "❌ Цена не может быть отрицательной"

        # Проверка на ноль
        if price == 0 and not allow_zero:
            return False, None, "❌ Цена не может быть нулевой"

        # Проверка на слишком большое значение
        if price > 10_000_000:
            return False, None, "❌ Цена слишком большая (максимум 10,000,000)"

        return True, price, None

    @staticmethod
    def validate_price_range(min_price: int, max_price: int) -> Tuple[bool, Optional[str]]:
        """
        Валидация диапазона цен
        Returns: (valid, error_message)
        """
        if min_price > max_price:
            return False, "❌ Минимальная цена не может быть больше максимальной"

        if min_price == 0 and max_price == 0:
            return True, "⚠️ Внимание: диапазон цен 0-0 может не дать результатов"

        return True, None

    @staticmethod
    def validate_bot_token(token: str) -> Tuple[bool, Optional[str]]:
        """
        Валидация токена бота
        Returns: (valid, error_message)
        """
        token = token.strip()

        # Проверка на пустоту
        if not token:
            return False, "❌ Токен не может быть пустым"

        # Проверка формата (число:строка)
        if ':' not in token:
            return False, "❌ Неверный формат токена (должен быть вид: 123456:ABC-DEF...)"

        parts = token.split(':')
        if len(parts) != 2:
            return False, "❌ Неверный формат токена"

        bot_id, bot_secret = parts

        # Проверка ID бота (должно быть число)
        if not bot_id.isdigit():
            return False, "❌ ID бота должен быть числом"

        # Проверка длины секрета (обычно 35+ символов)
        if len(bot_secret) < 30:
            return False, "❌ Токен слишком короткий (проверьте правильность)"

        return True, None

    @staticmethod
    def validate_chat_id(chat_id_str: str) -> Tuple[bool, Optional[int], Optional[str]]:
        """
        Валидация Chat ID
        Returns: (valid, chat_id_int, error_message)
        """
        chat_id_str = chat_id_str.strip()

        # Проверка на буквы (кроме минуса)
        if not chat_id_str.lstrip('-').isdigit():
            return False, None, "❌ Chat ID должен быть числом"

        try:
            chat_id = int(chat_id_str)
        except ValueError:
            return False, None, "❌ Неверный формат Chat ID"

        # Проверка на ноль
        if chat_id == 0:
            return False, None, "❌ Chat ID не может быть нулевым"

        # Отрицательные ID допустимы (групповые чаты)
        return True, chat_id, None

    @staticmethod
    def validate_username(username: str) -> Tuple[bool, str, Optional[str]]:
        """
        Валидация username
        Returns: (valid, normalized_username, error_message)
        """
        username = username.strip()

        # Проверка на пустоту
        if not username:
            return False, username, "❌ Username не может быть пустым"

        # Добавить @ если нет
        if not username.startswith('@'):
            username = '@' + username

        # Проверка формата (только латиница, цифры, подчёркивания, 5-32 символа)
        pattern = r'^@[A-Za-z0-9_]{5,32}$'
        if not re.match(pattern, username):
            return False, username, "❌ Неверный формат username (только латиница, цифры, _ ; длина 5-32)"

        return True, username, None

    @staticmethod
    def validate_chats_list(chats: list[str]) -> Tuple[bool, list[str], Optional[str]]:
        """
        Валидация списка чатов
        Returns: (valid, normalized_chats, error_message)
        """
        if not chats:
            return False, [], "❌ Список чатов пуст. Добавьте хотя бы один чат"

        normalized = []
        seen = set()

        for chat in chats:
            valid, normalized_chat, error = Validators.validate_username(chat)
            if not valid:
                return False, [], f"❌ Неверный формат чата '{chat}': {error}"

            # Проверка на дубликаты
            if normalized_chat.lower() in seen:
                continue  # Пропускаем дубликаты

            seen.add(normalized_chat.lower())
            normalized.append(normalized_chat)

        if len(normalized) > 50:
            return False, [], "❌ Слишком много чатов (максимум 50)"

        return True, normalized, None

    @staticmethod
    def validate_url(url: str, allowed_domains: list[str]) -> Tuple[bool, Optional[str]]:
        """
        Валидация URL
        Returns: (valid, error_message)
        """
        url = url.strip()

        # Проверка на пустоту
        if not url:
            return False, "❌ URL не может быть пустым"

        # Проверка протокола
        if not url.startswith('http://') and not url.startswith('https://'):
            return False, "❌ URL должен начинаться с http:// или https://"

        # Проверка домена
        domain_found = False
        for domain in allowed_domains:
            if domain in url:
                domain_found = True
                break

        if not domain_found:
            domains_str = ", ".join(allowed_domains)
            return False, f"❌ URL должен быть с доменов: {domains_str}"

        return True, None

    @staticmethod
    def validate_pages_count(pages_str: str) -> Tuple[bool, Optional[int], Optional[str]]:
        """
        Валидация количества страниц
        Returns: (valid, pages_int, error_message)
        """
        # Проверка на буквы
        if not pages_str.isdigit():
            return False, None, "❌ Количество страниц должно быть числом"

        try:
            pages = int(pages_str)
        except ValueError:
            return False, None, "❌ Неверный формат числа"

        if pages < 1:
            return False, None, "❌ Количество страниц должно быть больше 0"

        if pages > 100:
            return False, None, "❌ Слишком много страниц (максимум 100)"

        return True, pages, None


# Декораторы для защиты от спама кнопок
class AntiSpam:
    """Защита от множественных нажатий кнопок"""

    # Хранилище последних нажатий: {user_id: {callback_data: timestamp}}
    _last_clicks: dict[int, dict[str, float]] = {}

    @staticmethod
    def check_and_update(user_id: int, callback_data: str, cooldown: float = 1.0) -> bool:
        """
        Проверить и обновить cooldown для кнопки
        Returns: True если можно обработать, False если спам
        """
        import time
        now = time.time()

        if user_id not in AntiSpam._last_clicks:
            AntiSpam._last_clicks[user_id] = {}

        last_click = AntiSpam._last_clicks[user_id].get(callback_data, 0)

        if now - last_click < cooldown:
            return False  # Спам!

        AntiSpam._last_clicks[user_id][callback_data] = now
        return True

    @staticmethod
    def cleanup_old(max_age: float = 300.0):
        """Очистить старые записи (>5 минут)"""
        import time
        now = time.time()

        for user_id in list(AntiSpam._last_clicks.keys()):
            user_clicks = AntiSpam._last_clicks[user_id]
            for callback_data in list(user_clicks.keys()):
                if now - user_clicks[callback_data] > max_age:
                    del user_clicks[callback_data]

            if not user_clicks:
                del AntiSpam._last_clicks[user_id]
