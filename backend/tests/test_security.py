import time

import jwt as pyjwt
import pytest


def test_password_hash_and_verify():
    from app.core.security import hash_password, verify_password

    hashed = hash_password("S3cure!password")
    assert hashed != "S3cure!password"
    assert verify_password("S3cure!password", hashed)
    assert not verify_password("wrong", hashed)


def test_password_over_72_bytes_rejected():
    from app.core.security import hash_password

    with pytest.raises(ValueError):
        hash_password("x" * 73)


def test_access_token_roundtrip():
    from app.core.security import create_access_token, decode_access_token

    payload = decode_access_token(create_access_token("user-123"))
    assert payload["sub"] == "user-123"
    assert payload["type"] == "access"
    assert "jti" in payload


def test_tampered_token_rejected():
    from app.core.security import create_access_token, decode_access_token

    token = create_access_token("user-123")
    tampered = token[:-4] + ("AAAA" if token[-4:] != "AAAA" else "BBBB")
    with pytest.raises(pyjwt.PyJWTError):
        decode_access_token(tampered)


def test_wrong_audience_rejected():
    from app.config import settings
    from app.core.security import decode_access_token

    bad = pyjwt.encode(
        {"sub": "u", "type": "access", "iss": settings.JWT_ISSUER, "aud": "someone-else",
         "iat": int(time.time()), "exp": int(time.time()) + 600},
        settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM,
    )
    with pytest.raises(pyjwt.PyJWTError):
        decode_access_token(bad)


def test_expired_token_rejected():
    from app.config import settings
    from app.core.security import decode_access_token

    expired = pyjwt.encode(
        {"sub": "u", "type": "access", "iss": settings.JWT_ISSUER, "aud": settings.JWT_AUDIENCE,
         "iat": int(time.time()) - 7200, "exp": int(time.time()) - 3600},
        settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM,
    )
    with pytest.raises(pyjwt.ExpiredSignatureError):
        decode_access_token(expired)


def test_refresh_token_type_not_valid_as_access():
    """A refresh-shaped JWT must never pass access-token validation."""
    from app.config import settings
    from app.core.security import decode_access_token

    refresh_like = pyjwt.encode(
        {"sub": "u", "type": "refresh", "iss": settings.JWT_ISSUER, "aud": settings.JWT_AUDIENCE,
         "iat": int(time.time()), "exp": int(time.time()) + 600},
        settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM,
    )
    with pytest.raises(pyjwt.PyJWTError):
        decode_access_token(refresh_like)


def test_refresh_token_hashing():
    from app.core.security import generate_refresh_token, hash_refresh_token

    raw = generate_refresh_token()
    assert len(raw) >= 48
    assert hash_refresh_token(raw) == hash_refresh_token(raw)
    assert len(hash_refresh_token(raw)) == 64


def test_token_encryption_roundtrip_and_tamper():
    from app.services.encryption import TokenEncryptionError, decrypt_token, encrypt_token

    secret = "EAABsbCS1iHgBA-long-meta-page-token"
    ciphertext = encrypt_token(secret)
    assert ciphertext != secret
    assert decrypt_token(ciphertext) == secret

    tampered = ciphertext[:-2] + ("ab" if ciphertext[-2:] != "ab" else "cd")
    with pytest.raises(TokenEncryptionError):
        decrypt_token(tampered)


def test_appsecret_proof_and_webhook_signature():
    import hashlib
    import hmac

    from app.services.meta_oauth import appsecret_proof, verify_meta_webhook_signature

    expected = hmac.new(b"test-app-secret", b"token-x", hashlib.sha256).hexdigest()
    assert appsecret_proof("token-x") == expected

    body = b'{"object":"page"}'
    good = "sha256=" + hmac.new(b"test-app-secret", body, hashlib.sha256).hexdigest()
    assert verify_meta_webhook_signature(body, good)
    assert not verify_meta_webhook_signature(body, "sha256=deadbeef")
    assert not verify_meta_webhook_signature(body, None)
