import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings

PREFIX = "enc:v1:"


def _fernet():
    digest = hashlib.sha256(settings.SECRET_KEY.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def seal(value):
    if not value or str(value).startswith(PREFIX):
        return value or ""
    token = _fernet().encrypt(str(value).encode("utf-8")).decode("ascii")
    return PREFIX + token


def unseal(value):
    if not value or not str(value).startswith(PREFIX):
        return value or ""
    raw = str(value)[len(PREFIX) :]
    try:
        return _fernet().decrypt(raw.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
