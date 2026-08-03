from packages.core.settings import Settings


def _isolated_settings(**overrides: str | int | None) -> Settings:
    base: dict[str, str | int | None] = {
        "_env_file": None,
        "telegram_bot_token": None,
        "telegram_allowed_user_id": None,
        "telegram_allowed_chat_id": None,
        "telegram_chat_id": None,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def test_telegram_token_not_configured_for_placeholder() -> None:
    s = _isolated_settings(telegram_bot_token="your_bot_token_here")
    assert s.telegram_token_configured() is False


def test_telegram_token_configured() -> None:
    s = _isolated_settings(telegram_bot_token="123456:ABC-DEF")
    assert s.telegram_token_configured() is True


def test_legacy_telegram_chat_id_alias() -> None:
    s = _isolated_settings(telegram_chat_id=99887766)
    assert s.telegram_allowed_chat_id == 99887766


def test_telegram_operator_restrictions_unset() -> None:
    s = _isolated_settings()
    info = s.telegram_operator_restrictions()
    assert "not set" in info["allowed_user_id"]
    assert "not set" in info["allowed_chat_id"]
