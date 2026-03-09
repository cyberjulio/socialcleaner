import json
from cryptography.fernet import Fernet
from backend.config import settings
import base64
import hashlib


def _get_fernet() -> Fernet:
    key = hashlib.sha256(settings.secret_key.encode()).digest()
    key_b64 = base64.urlsafe_b64encode(key)
    return Fernet(key_b64)


def encrypt_json(data: dict) -> str:
    f = _get_fernet()
    return f.encrypt(json.dumps(data).encode()).decode()


def decrypt_json(token: str) -> dict:
    f = _get_fernet()
    return json.loads(f.decrypt(token.encode()).decode())
