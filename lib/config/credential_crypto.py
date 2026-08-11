"""Authenticated encryption for provider credential database columns."""

from __future__ import annotations

import base64
import os
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import Text, TypeDecorator

_PREFIX = "enc:v1:"


@lru_cache(maxsize=2)
def _cipher(encoded_key: str) -> AESGCM:
    try:
        key = base64.urlsafe_b64decode(encoded_key.encode())
    except (ValueError, TypeError) as error:
        raise RuntimeError("XINGJING_CREDENTIAL_ENCRYPTION_KEY_INVALID") from error
    if len(key) != 32:
        raise RuntimeError("XINGJING_CREDENTIAL_ENCRYPTION_KEY_MUST_BE_32_BYTES")
    return AESGCM(key)


def _configured_cipher() -> AESGCM:
    key = os.environ.get("XINGJING_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not key:
        raise RuntimeError("XINGJING_CREDENTIAL_ENCRYPTION_KEY_REQUIRED")
    return _cipher(key)


def encrypt_secret(value: str) -> str:
    if value.startswith(_PREFIX):
        return value
    nonce = os.urandom(12)
    encrypted = _configured_cipher().encrypt(nonce, value.encode(), b"xingjing-provider-credential-v1")
    return _PREFIX + base64.urlsafe_b64encode(nonce + encrypted).decode()


def decrypt_secret(value: str) -> str:
    if not value.startswith(_PREFIX):
        # Legacy plaintext remains readable only so operators can rotate it;
        # every subsequent write is encrypted by process_bind_param.
        return value
    try:
        payload = base64.urlsafe_b64decode(value.removeprefix(_PREFIX).encode())
        return _configured_cipher().decrypt(payload[:12], payload[12:], b"xingjing-provider-credential-v1").decode()
    except (ValueError, UnicodeDecodeError) as error:
        raise RuntimeError("PROVIDER_CREDENTIAL_DECRYPTION_FAILED") from error


class EncryptedText(TypeDecorator[str]):
    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        del dialect
        return encrypt_secret(value) if value else value

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        del dialect
        return decrypt_secret(value) if value else value
