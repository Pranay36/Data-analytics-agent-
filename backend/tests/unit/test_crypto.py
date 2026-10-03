import pytest
from cryptography.fernet import Fernet

from app.core import crypto
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("DATASOURCE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_round_trip() -> None:
    token = crypto.encrypt_secret("hunter2")
    assert token != "hunter2"
    assert "hunter2" not in token
    assert crypto.decrypt_secret(token) == "hunter2"


def test_same_secret_encrypts_differently_each_time() -> None:
    """Fernet includes a random IV, so equal passwords are not visibly equal."""
    assert crypto.encrypt_secret("same") != crypto.encrypt_secret("same")


def test_a_changed_key_is_reported_as_such(monkeypatch) -> None:
    token = crypto.encrypt_secret("hunter2")
    monkeypatch.setenv("DATASOURCE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    get_settings.cache_clear()

    with pytest.raises(crypto.CryptoError, match="changed"):
        crypto.decrypt_secret(token)


def test_missing_key_gives_actionable_instructions(monkeypatch) -> None:
    monkeypatch.setenv("DATASOURCE_ENCRYPTION_KEY", "")
    get_settings.cache_clear()

    with pytest.raises(crypto.CryptoError, match="Fernet.generate_key"):
        crypto.encrypt_secret("x")
