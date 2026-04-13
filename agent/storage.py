"""
storage.py — Almacenamiento de documentos en Cloudflare R2 (S3-compatible)

Variables de entorno requeridas:
  R2_ACCOUNT_ID   — ID de cuenta Cloudflare
  R2_ACCESS_KEY   — Access Key ID del API Token
  R2_SECRET_KEY   — Secret Access Key del API Token
  R2_BUCKET       — Nombre del bucket (default: minka-docs)
"""

import os
import uuid
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY = os.getenv("R2_ACCESS_KEY", "")
R2_SECRET_KEY = os.getenv("R2_SECRET_KEY", "")
R2_BUCKET = os.getenv("R2_BUCKET", "minka-docs")


def r2_configured() -> bool:
    return bool(R2_ACCOUNT_ID and R2_ACCESS_KEY and R2_SECRET_KEY)


def _get_client():
    return boto3.client(
        "s3",
        endpoint_url=f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=R2_ACCESS_KEY,
        aws_secret_access_key=R2_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def upload_document(contenido: bytes, nombre_archivo: str, content_type: str, caso_id: int) -> str:
    """
    Sube un documento al bucket R2.
    Retorna la clave del objeto (ej: 'casos/42/abc123.pdf').
    """
    ext = nombre_archivo.rsplit(".", 1)[-1].lower() if "." in nombre_archivo else "bin"
    key = f"casos/{caso_id}/{uuid.uuid4().hex}.{ext}"

    client = _get_client()
    client.put_object(
        Bucket=R2_BUCKET,
        Key=key,
        Body=contenido,
        ContentType=content_type,
        ContentDisposition=f'attachment; filename="{nombre_archivo}"',
    )
    return key


def generate_presigned_url(key: str, expires_seconds: int = 3600) -> str:
    """Genera una URL firmada temporal para descargar el documento."""
    client = _get_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": R2_BUCKET, "Key": key},
        ExpiresIn=expires_seconds,
    )


def delete_document(key: str) -> bool:
    """Elimina un documento del bucket. Retorna True si fue exitoso."""
    try:
        client = _get_client()
        client.delete_object(Bucket=R2_BUCKET, Key=key)
        return True
    except ClientError:
        return False
