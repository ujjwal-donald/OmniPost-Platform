"""OAuth token encryption at rest.

Fernet = AES-128-CBC + HMAC-SHA256 (authenticated). The key is validated at
boot (app.config). Phase 2 swaps these internals for AWS KMS envelope
encryption; call sites stay unchanged.
"""
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings


class TokenEncryptionError(Exception):
    """Stored token cannot be decrypted — wrong FERNET_KEY or tampered data."""


@lru_cache
def _fernet() -> Fernet:
    return Fernet(settings.FERNET_KEY.encode())


def encrypt_token(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_token(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise TokenEncryptionError(
            "Stored token could not be decrypted — wrong FERNET_KEY or corrupted data."
        ) from exc
