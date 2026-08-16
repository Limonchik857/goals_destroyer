"""Шифрование OAuth-токенов (Fernet).

Ключ задаётся переменной окружения TOKEN_ENCRYPTION_KEY и нужен только
на стороне приложения. Токены не должны попадать в HTML, логи,
сообщения об ошибках или JSON-ответы.
"""
import base64
import hashlib

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.crypto import constant_time_compare

_fernet = None


def _get_fernet():
    """Fernet-шифратор; ключ выводится из TOKEN_ENCRYPTION_KEY."""
    global _fernet
    if _fernet is None:
        from cryptography.fernet import Fernet

        raw = getattr(settings, "TOKEN_ENCRYPTION_KEY", "")
        if not raw:
            raise ImproperlyConfigured(
                "Переменная окружения TOKEN_ENCRYPTION_KEY не задана. "
                "Она нужна для шифрования OAuth-токенов интеграций."
            )
        # Любая строка заданной длины превращается в валидный ключ Fernet.
        key = base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
        _fernet = Fernet(key)
    return _fernet


def encrypt_token(plain_text):
    if not plain_text:
        return ""
    return _get_fernet().encrypt(plain_text.encode("utf-8")).decode("ascii")


def decrypt_token(cipher_text):
    if not cipher_text:
        return ""
    return _get_fernet().decrypt(cipher_text.encode("ascii")).decode("utf-8")


def token_equals(token, other):
    """Сравнить расшифрованный токен с ожидаемым значением."""
    if not token or not other:
        return False
    return constant_time_compare(token, other)