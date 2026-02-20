"""
Тесты фичи: поиск в ЧС только по ФИО

Покрывает:
  - Validators.validate_fio()         — чистая функция, без моков
  - receive_username()                 — обработка кнопки FIO_ONLY и username
  - receive_fio()                      — логика ввода ФИО, валидация, защита от багов
  - WorkersAPI.check_blacklist params  — username=None не уходит в запрос
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, call

from telegram.ext import ConversationHandler

from parserhub.validators import Validators
from parserhub.handlers.blacklist import (
    BlacklistBtn,
    BlacklistState,
    receive_username,
    receive_fio,
)


# ─────────────────────────────────────────────
# Helpers: фиктивные объекты Telegram
# ─────────────────────────────────────────────

def make_update(text: str, user_id: int = 42, chat_id: int = 42) -> MagicMock:
    """Создаёт mock Update с нужным текстом сообщения."""
    update = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    update.effective_user.id = user_id
    update.effective_chat.id = chat_id
    return update


def make_context(user_data: dict = None, bot_data: dict = None) -> MagicMock:
    """Создаёт mock ContextTypes.DEFAULT_TYPE."""
    context = MagicMock()
    context.user_data = user_data if user_data is not None else {}
    context.bot_data = bot_data if bot_data is not None else {}
    context.bot = AsyncMock()
    return context


# ─────────────────────────────────────────────
# 1. Validators.validate_fio — чистые тесты
# ─────────────────────────────────────────────

class TestValidateFio:

    # --- Валидные входные данные ---

    def test_surname_only(self):
        valid, fio, err = Validators.validate_fio("Иванов")
        assert valid is True
        assert fio == "Иванов"
        assert err is None

    def test_surname_and_name(self):
        valid, fio, err = Validators.validate_fio("Иванов Иван")
        assert valid is True
        assert fio == "Иванов Иван"

    def test_full_fio(self):
        valid, fio, err = Validators.validate_fio("Иванов Иван Иванович")
        assert valid is True
        assert fio == "Иванов Иван Иванович"

    def test_double_surname_hyphen(self):
        """Двойная фамилия через дефис должна приниматься."""
        valid, fio, err = Validators.validate_fio("Иванов-Петров Иван")
        assert valid is True
        assert fio == "Иванов-Петров Иван"

    def test_lowercase_accepted(self):
        """Нижний регистр — допустим (поиск case-insensitive)."""
        valid, fio, err = Validators.validate_fio("иванов иван")
        assert valid is True

    def test_uppercase_accepted(self):
        """Полный верхний регистр — допустим."""
        valid, fio, err = Validators.validate_fio("ИВАНОВ ИВАН")
        assert valid is True

    def test_strips_leading_trailing_spaces(self):
        """Лишние пробелы по краям должны убираться."""
        valid, fio, err = Validators.validate_fio("  Иванов  ")
        assert valid is True
        assert fio == "Иванов"

    # --- Невалидные входные данные ---

    def test_empty_string(self):
        valid, _, err = Validators.validate_fio("")
        assert valid is False
        assert err is not None

    def test_whitespace_only(self):
        valid, _, err = Validators.validate_fio("   ")
        assert valid is False

    def test_at_sign_detected_as_username(self):
        """Если ввели @username вместо ФИО — специальная ошибка."""
        valid, _, err = Validators.validate_fio("@username")
        assert valid is False
        assert "никнейм" in err.lower() or "@" in err

    def test_latin_letters_rejected(self):
        valid, _, err = Validators.validate_fio("Ivan Ivanov")
        assert valid is False
        assert err is not None

    def test_mixed_cyrillic_latin_rejected(self):
        valid, _, err = Validators.validate_fio("Иванов Ivan")
        assert valid is False

    def test_digits_rejected(self):
        valid, _, err = Validators.validate_fio("Иванов123")
        assert valid is False

    def test_special_chars_rejected(self):
        valid, _, err = Validators.validate_fio("Иванов!")
        assert valid is False

    def test_at_in_middle_rejected(self):
        valid, _, err = Validators.validate_fio("Иванов@Иван")
        assert valid is False

    def test_too_many_words(self):
        """4 и более слов — отклонить."""
        valid, _, err = Validators.validate_fio("Иванов Иван Иванович Петров")
        assert valid is False
        assert "слов" in err.lower() or "максимум" in err.lower()

    def test_single_letter_word_rejected(self):
        """Слово из одной буквы — слишком коротко."""
        valid, _, err = Validators.validate_fio("И Иванов")
        assert valid is False

    def test_trailing_hyphen_rejected(self):
        """Дефис в конце слова — некорректно."""
        valid, _, err = Validators.validate_fio("Иванов-")
        assert valid is False

    def test_leading_hyphen_rejected(self):
        """Дефис в начале слова — некорректно."""
        valid, _, err = Validators.validate_fio("-Иванов")
        assert valid is False

    def test_double_hyphen_rejected(self):
        """Двойной дефис — некорректно."""
        valid, _, err = Validators.validate_fio("Иванов--Петров")
        assert valid is False


# ─────────────────────────────────────────────
# 2. receive_username — обработка ввода username
# ─────────────────────────────────────────────

class TestReceiveUsername:

    @pytest.mark.asyncio
    async def test_fio_only_button_sets_sentinel(self):
        """Кнопка FIO_ONLY → bl_username="" (sentinel), переход в WAITING_FIO."""
        update = make_update(BlacklistBtn.FIO_ONLY)
        context = make_context()

        state = await receive_username(update, context)

        assert state == BlacklistState.WAITING_FIO
        assert "bl_username" in context.user_data
        assert context.user_data["bl_username"] == ""

    @pytest.mark.asyncio
    async def test_fio_only_keyboard_has_no_skip_button(self):
        """В FIO-only режиме кнопка 'Пропустить' не отправляется."""
        update = make_update(BlacklistBtn.FIO_ONLY)
        context = make_context()

        await receive_username(update, context)

        # Проверяем, что в reply_markup нет текста кнопки SKIP_FIO
        call_kwargs = update.message.reply_text.call_args.kwargs
        markup = call_kwargs.get("reply_markup")
        if markup:
            all_button_texts = [
                btn.text
                for row in markup.keyboard
                for btn in row
            ]
            assert BlacklistBtn.SKIP_FIO not in all_button_texts

    @pytest.mark.asyncio
    async def test_valid_username_sets_bl_username(self):
        """Валидный @username → bl_username нормализован, переход в WAITING_FIO."""
        update = make_update("@testuser")
        context = make_context()

        state = await receive_username(update, context)

        assert state == BlacklistState.WAITING_FIO
        assert context.user_data["bl_username"] == "@testuser"

    @pytest.mark.asyncio
    async def test_username_without_at_gets_normalized(self):
        """username без @ → нормализуется до @username."""
        update = make_update("testuser")
        context = make_context()

        state = await receive_username(update, context)

        assert state == BlacklistState.WAITING_FIO
        assert context.user_data["bl_username"] == "@testuser"

    @pytest.mark.asyncio
    async def test_invalid_username_stays_in_state(self):
        """Невалидный username → остаёмся в WAITING_USERNAME."""
        update = make_update("bad!")  # спецсимволы
        context = make_context()

        state = await receive_username(update, context)

        assert state == BlacklistState.WAITING_USERNAME
        assert "bl_username" not in context.user_data

    @pytest.mark.asyncio
    async def test_username_too_short_stays_in_state(self):
        """Username < 5 символов → невалиден."""
        update = make_update("@ab")
        context = make_context()

        state = await receive_username(update, context)

        assert state == BlacklistState.WAITING_USERNAME

    @pytest.mark.asyncio
    async def test_valid_username_keyboard_has_skip_button(self):
        """После ввода username клавиатура содержит кнопку 'Пропустить'."""
        update = make_update("@validuser")
        context = make_context()

        await receive_username(update, context)

        call_kwargs = update.message.reply_text.call_args.kwargs
        markup = call_kwargs.get("reply_markup")
        assert markup is not None
        all_button_texts = [
            btn.text
            for row in markup.keyboard
            for btn in row
        ]
        assert BlacklistBtn.SKIP_FIO in all_button_texts


# ─────────────────────────────────────────────
# 3. receive_fio — логика ввода ФИО
# ─────────────────────────────────────────────

class TestReceiveFio:

    def _make_bot_data(self) -> dict:
        """bot_data с моком workers_api и db."""
        workers_api = MagicMock()
        workers_api.check_blacklist = AsyncMock(return_value={"found": False, "steps_done": []})
        db = MagicMock()
        return {
            "workers_api": workers_api,
            "db": db,
        }

    # --- FIO-only режим (bl_username="") ---

    @pytest.mark.asyncio
    async def test_fio_only_valid_fio_launches_search(self):
        """FIO-only + валидное ФИО → поиск запущен, bl_username удалён из context."""
        update = make_update("Иванов Иван Иванович")
        context = make_context(
            user_data={"bl_username": ""},
            bot_data=self._make_bot_data(),
        )

        with patch("parserhub.handlers.blacklist.asyncio.create_task") as mock_task:
            state = await receive_fio(update, context)

        assert state == ConversationHandler.END
        assert "bl_username" not in context.user_data
        mock_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_fio_only_skip_is_rejected(self):
        """FIO-only + нажали 'Пропустить' → ошибка, остаёмся в WAITING_FIO."""
        update = make_update(BlacklistBtn.SKIP_FIO)
        context = make_context(
            user_data={"bl_username": ""},
            bot_data=self._make_bot_data(),
        )

        state = await receive_fio(update, context)

        assert state == BlacklistState.WAITING_FIO
        # bl_username НЕ должен быть потерян
        assert context.user_data["bl_username"] == ""

    @pytest.mark.asyncio
    async def test_fio_only_invalid_fio_stays_in_state(self):
        """FIO-only + невалидное ФИО → остаёмся в WAITING_FIO, bl_username цел."""
        update = make_update("Ivan123")  # латиница + цифры
        context = make_context(
            user_data={"bl_username": ""},
            bot_data=self._make_bot_data(),
        )

        state = await receive_fio(update, context)

        assert state == BlacklistState.WAITING_FIO
        assert context.user_data["bl_username"] == ""  # не потеряли

    @pytest.mark.asyncio
    async def test_fio_only_invalid_no_skip_hint_in_error(self):
        """FIO-only: в сообщении об ошибке не должно быть подсказки про 'Пропустить'."""
        update = make_update("@someuser")
        context = make_context(
            user_data={"bl_username": ""},
            bot_data=self._make_bot_data(),
        )

        await receive_fio(update, context)

        error_text = update.message.reply_text.call_args.args[0]
        assert "Пропустить" not in error_text

    @pytest.mark.asyncio
    async def test_fio_only_username_in_search_launch_text(self):
        """FIO-only: в сообщении запуска показывается ФИО, не 'Никнейм'."""
        update = make_update("Петров Пётр")
        context = make_context(
            user_data={"bl_username": ""},
            bot_data=self._make_bot_data(),
        )

        with patch("parserhub.handlers.blacklist.asyncio.create_task"):
            await receive_fio(update, context)

        launch_text = update.message.reply_text.call_args.args[0]
        assert "ФИО" in launch_text
        assert "Петров Пётр" in launch_text
        assert "Никнейм" not in launch_text

    # --- Username режим (bl_username="@username") ---

    @pytest.mark.asyncio
    async def test_username_mode_with_fio_launches_search(self):
        """Username + ФИО → поиск запущен с обоими параметрами."""
        update = make_update("Сидоров Сергей")
        context = make_context(
            user_data={"bl_username": "@sidorov"},
            bot_data=self._make_bot_data(),
        )

        with patch("parserhub.handlers.blacklist.asyncio.create_task") as mock_task:
            state = await receive_fio(update, context)

        assert state == ConversationHandler.END
        mock_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_username_mode_skip_fio_launches_search(self):
        """Username + Пропустить → поиск без ФИО, статус END."""
        update = make_update(BlacklistBtn.SKIP_FIO)
        context = make_context(
            user_data={"bl_username": "@user123"},
            bot_data=self._make_bot_data(),
        )

        with patch("parserhub.handlers.blacklist.asyncio.create_task") as mock_task:
            state = await receive_fio(update, context)

        assert state == ConversationHandler.END
        mock_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_username_mode_invalid_fio_stays_in_state(self):
        """Username + невалидное ФИО → остаёмся в WAITING_FIO."""
        update = make_update("Иванов Иван Иванович Петров")  # 4 слова
        context = make_context(
            user_data={"bl_username": "@ivanov"},
            bot_data=self._make_bot_data(),
        )

        state = await receive_fio(update, context)

        assert state == BlacklistState.WAITING_FIO
        assert context.user_data["bl_username"] == "@ivanov"

    @pytest.mark.asyncio
    async def test_username_mode_invalid_fio_has_skip_hint(self):
        """Username + невалидное ФИО → в ошибке есть подсказка про 'Пропустить'."""
        update = make_update("Ivan123")
        context = make_context(
            user_data={"bl_username": "@test123"},
            bot_data=self._make_bot_data(),
        )

        await receive_fio(update, context)

        error_text = update.message.reply_text.call_args.args[0]
        assert "Пропустить" in error_text

    # --- Защита: bl_username не в context (ошибка сессии) ---

    @pytest.mark.asyncio
    async def test_missing_bl_username_returns_end(self):
        """Нет bl_username в context → сообщение об ошибке, ConversationHandler.END."""
        update = make_update("Иванов")
        context = make_context(user_data={})  # ключа нет

        state = await receive_fio(update, context)

        assert state == ConversationHandler.END
        update.message.reply_text.assert_awaited_once()
        error_text = update.message.reply_text.call_args.args[0]
        assert "Ошибка" in error_text

    @pytest.mark.asyncio
    async def test_already_searching_returns_end(self):
        """Если поиск уже идёт → сообщение 'подождите', ConversationHandler.END."""
        user_id = 99
        update = make_update("Иванов", user_id=user_id)
        bot_data = self._make_bot_data()
        bot_data["blacklist_searching"] = {user_id}  # уже ищет
        context = make_context(
            user_data={"bl_username": ""},
            bot_data=bot_data,
        )

        with patch("parserhub.handlers.blacklist.asyncio.create_task") as mock_task:
            state = await receive_fio(update, context)

        assert state == ConversationHandler.END
        mock_task.assert_not_called()

    # --- Sentinel гарантии: bl_username pop только при успехе ---

    @pytest.mark.asyncio
    async def test_bl_username_not_popped_on_fio_validation_failure(self):
        """При ошибке валидации ФИО — bl_username остаётся в context (не был pop-нут)."""
        update = make_update("@nikto")  # ввёл никнейм в поле ФИО
        context = make_context(
            user_data={"bl_username": "@real_user"},
            bot_data=self._make_bot_data(),
        )

        await receive_fio(update, context)

        # Ключ должен остаться нетронутым
        assert "bl_username" in context.user_data
        assert context.user_data["bl_username"] == "@real_user"


# ─────────────────────────────────────────────
# 4. api_client.check_blacklist — параметры запроса
# ─────────────────────────────────────────────

class TestApiClientCheckBlacklist:

    def _make_api_client(self, mock_response: dict):
        """Создаёт WorkersAPI с замоканным httpx клиентом."""
        from parserhub.api_client import WorkersAPI

        client = WorkersAPI.__new__(WorkersAPI)
        client.base_url = "http://test"

        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = MagicMock(return_value=mock_response)

        client.client = AsyncMock()
        client.client.post = AsyncMock(return_value=mock_resp)
        return client

    @pytest.mark.asyncio
    async def test_username_none_not_in_params(self):
        """username=None → 'username' не передаётся в params запроса."""
        api = self._make_api_client({"found": False})

        await api.check_blacklist(None, "/sessions/42_blacklist", fio="Иванов")

        _, call_kwargs = api.client.post.call_args
        params = call_kwargs.get("params", {})
        assert "username" not in params

    @pytest.mark.asyncio
    async def test_username_empty_string_not_in_params(self):
        """username='' → 'username' не передаётся (пустая строка = None)."""
        api = self._make_api_client({"found": False})

        await api.check_blacklist("", "/sessions/42_blacklist", fio="Иванов")

        _, call_kwargs = api.client.post.call_args
        params = call_kwargs.get("params", {})
        assert "username" not in params

    @pytest.mark.asyncio
    async def test_username_present_in_params(self):
        """username='@test' → передаётся в params."""
        api = self._make_api_client({"found": False})

        await api.check_blacklist("@test", "/sessions/42_blacklist")

        _, call_kwargs = api.client.post.call_args
        params = call_kwargs.get("params", {})
        assert params["username"] == "@test"

    @pytest.mark.asyncio
    async def test_fio_present_in_params(self):
        """fio='Иванов' → передаётся в params."""
        api = self._make_api_client({"found": False})

        await api.check_blacklist(None, "/sessions/42_blacklist", fio="Иванов")

        _, call_kwargs = api.client.post.call_args
        params = call_kwargs.get("params", {})
        assert params["fio"] == "Иванов"

    @pytest.mark.asyncio
    async def test_fio_none_not_in_params(self):
        """fio=None → 'fio' не передаётся в params."""
        api = self._make_api_client({"found": False})

        await api.check_blacklist("@user", "/sessions/42_blacklist", fio=None)

        _, call_kwargs = api.client.post.call_args
        params = call_kwargs.get("params", {})
        assert "fio" not in params

    @pytest.mark.asyncio
    async def test_session_path_always_in_params(self):
        """blacklist_session_path всегда передаётся, даже в FIO-only режиме."""
        api = self._make_api_client({"found": False})
        session = "/sessions/42_blacklist"

        await api.check_blacklist(None, session, fio="Иванов")

        _, call_kwargs = api.client.post.call_args
        params = call_kwargs.get("params", {})
        assert params["blacklist_session_path"] == session


# ─────────────────────────────────────────────
# 5. blacklist_service — early return guard
# ─────────────────────────────────────────────

class TestBlacklistServiceGuard:
    """
    Тестируем условие раннего выхода в search_in_blacklist.
    Мокаем DB и Pyrogram Client, не поднимаем реальный сервис.
    """

    def _make_service(self):
        import sys
        import os
        sys.path.insert(0, "/home/alex/PycharmProjects/workers_service")
        from blacklist_service import BlacklistService

        db_mock = AsyncMock()
        db_mock.get_blacklist_chats = AsyncMock(return_value=[])
        return BlacklistService(
            api_id=12345,
            api_hash="testhash",
            session_name="test_session",
            db_service=db_mock,
        )

    @pytest.mark.asyncio
    async def test_no_username_no_fio_returns_error(self):
        """username=None, fio=None → ранний выход с ошибкой."""
        service = self._make_service()
        result = await service.search_in_blacklist(username=None, fio=None)

        assert result["found"] is False
        assert "error" in result

    @pytest.mark.asyncio
    async def test_fio_only_passes_guard(self):
        """username=None, fio='Иванов' → не возвращает ошибку guard'а.
        Дальше вернёт 'нет активных чатов' — это нормально для unit-теста."""
        service = self._make_service()
        result = await service.search_in_blacklist(username=None, fio="Иванов")

        # Не должно быть ошибки guard'а "Необходимо указать username или ФИО"
        assert result.get("error") != "Необходимо указать username или ФИО для поиска"

    @pytest.mark.asyncio
    async def test_username_only_passes_guard(self):
        """username='@test', fio=None → guard пропускает."""
        service = self._make_service()
        result = await service.search_in_blacklist(username="@test", fio=None)

        assert result.get("error") != "Необходимо указать username или ФИО для поиска"
