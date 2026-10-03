"""Encryption for datasource credentials.

Fernet (AES-128-CBC with an HMAC) from `cryptography`. The key comes from the
environment, never the database, so a leaked database dump alone does not expose
passwords. This is encryption at rest, not a vault: anyone holding both the key
and the database can decrypt.

Only the secret field is encrypted. Host, port and database name stay as plain
JSON so they remain inspectable and queryable.
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import get_settings


class CryptoError(RuntimeError):
    """The key is missing or does not match the stored ciphertext."""


def _fernet() -> Fernet:
    key = get_settings().datasource_encryption_key.get_secret_value()
    if not key:
        raise CryptoError(
            "DATASOURCE_ENCRYPTION_KEY is not set. Generate one with:\n"
            '  python -c "from cryptography.fernet import Fernet; '
            'print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise CryptoError("DATASOURCE_ENCRYPTION_KEY is not a valid Fernet key.") from exc


def encrypt_secret(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        # Distinct from a bad password: the stored value cannot be read at all,
        # which usually means the key changed since it was written.
        raise CryptoError(
            "Could not decrypt the stored credential. "
            "Was DATASOURCE_ENCRYPTION_KEY changed after it was saved?"
        ) from exc
