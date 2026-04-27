# auth.py — Utilidades de autenticación (JWT + bcrypt)

import os
import hashlib
import hmac
import secrets
import time
import json
import base64

# Secret key para JWT — en producción usar variable de entorno
_jwt_secret = os.getenv("JWT_SECRET_KEY", "")
if not _jwt_secret:
    import logging as _logging
    _logging.getLogger("agentkit").critical(
        "[SECURITY] JWT_SECRET_KEY no configurada — el servidor NO puede arrancar sin esta variable. "
        "Configúrala en Railway: Settings > Variables."
    )
    raise RuntimeError(
        "JWT_SECRET_KEY no configurada. "
        "El servidor no puede arrancar sin una clave secreta para JWT."
    )
SECRET_KEY = _jwt_secret
TOKEN_EXPIRY = 24 * 60 * 60  # 24 horas en segundos


def hash_password(password: str) -> str:
    """Hash de password usando PBKDF2 (no requiere bcrypt externo)."""
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
    return f"{salt}:{dk.hex()}"


def verify_password(password: str, password_hash: str) -> bool:
    """Verifica un password contra su hash PBKDF2."""
    try:
        salt, hash_hex = password_hash.split(":")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 100000)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def create_token(user_id: int, email: str, nombre: str, rol: str) -> str:
    """Crea un JWT token simple (HS256)."""
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": user_id,
        "email": email,
        "nombre": nombre,
        "rol": rol,
        "exp": int(time.time()) + TOKEN_EXPIRY,
        "iat": int(time.time()),
    }

    header_b64 = _b64url_encode(json.dumps(header).encode())
    payload_b64 = _b64url_encode(json.dumps(payload).encode())

    message = f"{header_b64}.{payload_b64}"
    signature = hmac.new(SECRET_KEY.encode(), message.encode(), hashlib.sha256).digest()
    sig_b64 = _b64url_encode(signature)

    return f"{message}.{sig_b64}"


def decode_token(token: str) -> dict | None:
    """Decodifica y valida un JWT token. Retorna el payload o None si es inválido."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None

        header_b64, payload_b64, sig_b64 = parts

        # Verificar firma
        message = f"{header_b64}.{payload_b64}"
        expected_sig = hmac.new(SECRET_KEY.encode(), message.encode(), hashlib.sha256).digest()
        actual_sig = _b64url_decode(sig_b64)

        if not hmac.compare_digest(expected_sig, actual_sig):
            return None

        # Decodificar payload
        payload = json.loads(_b64url_decode(payload_b64))

        # Verificar expiración
        if payload.get("exp", 0) < time.time():
            return None

        return payload
    except Exception:
        return None
