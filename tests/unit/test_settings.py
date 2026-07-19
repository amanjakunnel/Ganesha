from typing import Any

from packages.core.settings import Settings
from sqlalchemy.engine import URL


def test_sqlalchemy_url_construction_env_overrides(monkeypatch: Any) -> None:
    monkeypatch.setenv('POSTGRES_USER', 'u1')
    monkeypatch.setenv('POSTGRES_PASSWORD', 'p1')
    monkeypatch.setenv('POSTGRES_DB', 'db1')
    monkeypatch.setenv('POSTGRES_HOST', 'h1')
    monkeypatch.setenv('POSTGRES_PORT', '54321')

    s = Settings()
    url = s.sqlalchemy_url()
    assert isinstance(url, URL)
    assert url.username == 'u1'
    assert url.password == 'p1'
    assert url.database == 'db1'
    assert url.host == 'h1'
    assert url.port == 54321


def test_password_special_characters_handled(monkeypatch: Any) -> None:
    special = r"p@$/::\"'"
    monkeypatch.setenv('POSTGRES_USER', 'u2')
    monkeypatch.setenv('POSTGRES_PASSWORD', special)
    monkeypatch.setenv('POSTGRES_DB', 'db2')
    monkeypatch.setenv('POSTGRES_HOST', 'h2')
    monkeypatch.setenv('POSTGRES_PORT', '5432')

    s = Settings()
    url = s.sqlalchemy_url()
    # URL object should contain the raw password value (not redacted)
    assert url.password == special


def test_redacted_url_does_not_contain_password(monkeypatch: Any) -> None:
    monkeypatch.setenv('POSTGRES_USER', 'u3')
    monkeypatch.setenv('POSTGRES_PASSWORD', 'secret-pass')
    monkeypatch.setenv('POSTGRES_DB', 'db3')
    monkeypatch.setenv('POSTGRES_HOST', 'h3')
    monkeypatch.setenv('POSTGRES_PORT', '5432')

    s = Settings()
    redacted = s.redacted_sqlalchemy_url()
    assert 'secret-pass' not in redacted
    # Ensure username and host are present
    assert 'u3' in redacted
    assert 'h3' in redacted
