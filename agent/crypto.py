"""
crypto.py — Compresión gzip + cifrado Fernet para texto de documentos.

Variable de entorno requerida (opcional, si no está solo comprime):
  DOCUMENT_ENCRYPTION_KEY — clave Fernet base64 de 32 bytes

Generar clave nueva:
  python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
"""

import os
import gzip
import base64
from cryptography.fernet import Fernet, InvalidToken

ENCRYPTION_KEY = os.getenv("DOCUMENT_ENCRYPTION_KEY", "")


def _get_fernet() -> "Fernet | None":
    if not ENCRYPTION_KEY:
        return None
    return Fernet(ENCRYPTION_KEY.encode())


def compress_encrypt(text: str) -> str:
    """
    Comprime con gzip y cifra con Fernet.
    Si DOCUMENT_ENCRYPTION_KEY no está configurada, solo comprime.
    Retorna string base64 seguro para almacenar en SQLite.
    """
    compressed = gzip.compress(text.encode("utf-8"), compresslevel=9)
    f = _get_fernet()
    if f:
        result = f.encrypt(compressed)
    else:
        result = compressed
    return base64.urlsafe_b64encode(result).decode("utf-8")


def decrypt_decompress(data: str) -> str:
    """
    Descifra y descomprime. Reverso de compress_encrypt.
    Si DOCUMENT_ENCRYPTION_KEY no está configurada, asume solo comprimido.
    """
    raw = base64.urlsafe_b64decode(data.encode("utf-8"))
    f = _get_fernet()
    if f:
        try:
            raw = f.decrypt(raw)
        except InvalidToken:
            # Fallback: datos sin cifrar (compatibilidad retroactiva)
            pass
    return gzip.decompress(raw).decode("utf-8")


def encryption_configured() -> bool:
    return bool(ENCRYPTION_KEY)
