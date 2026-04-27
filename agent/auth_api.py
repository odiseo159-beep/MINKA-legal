# auth_api.py — Endpoints de autenticación
# POST /auth/login, POST /auth/register, GET /auth/verificar, POST /auth/logout

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agent.auth import hash_password, verify_password, create_token, decode_token
from agent.users_db import obtener_usuario_por_email, obtener_usuario_por_id, usuario_existe, crear_usuario

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: str
    password: str


class RegisterRequest(BaseModel):
    email: str
    password: str
    nombre: str = ""


def _get_token_from_request(request: Request) -> str | None:
    """Extrae el token Bearer del header Authorization."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return None


def get_current_user(request: Request) -> dict | None:
    """Valida el token y retorna el usuario. Retorna None si no es válido."""
    token = _get_token_from_request(request)
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    return payload


@router.post("/login")
def login(data: LoginRequest):
    """Autenticación con email y password. Retorna JWT token."""
    usuario = obtener_usuario_por_email(data.email)
    if not usuario:
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    if not verify_password(data.password, usuario["password_hash"]):
        raise HTTPException(status_code=401, detail="Credenciales inválidas")

    token = create_token(
        user_id=usuario["id"],
        email=usuario["email"],
        nombre=usuario.get("nombre", ""),
        rol=usuario.get("rol", "abogado"),
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "usuario": {
            "id": usuario["id"],
            "email": usuario["email"],
            "nombre": usuario.get("nombre", ""),
            "rol": usuario.get("rol", "abogado"),
            "activo": True,
            "fecha_creacion": usuario.get("fecha_creacion", ""),
        },
    }


@router.post("/register")
def register(data: RegisterRequest):
    """Registro de nuevo usuario. Retorna JWT token (auto-login)."""
    if usuario_existe(data.email):
        raise HTTPException(status_code=409, detail="Ya existe una cuenta con ese correo electrónico")

    if len(data.password) < 10:
        raise HTTPException(status_code=422, detail="La contraseña debe tener al menos 10 caracteres")

    password_hash = hash_password(data.password)
    usuario = crear_usuario(
        email=data.email,
        password_hash=password_hash,
        nombre=data.nombre,
        rol="abogado",
    )

    token = create_token(
        user_id=usuario["id"],
        email=usuario["email"],
        nombre=usuario.get("nombre", ""),
        rol=usuario.get("rol", "abogado"),
    )

    return {
        "access_token": token,
        "token_type": "bearer",
        "usuario": {
            "id": usuario["id"],
            "email": usuario["email"],
            "nombre": usuario.get("nombre", ""),
            "rol": usuario.get("rol", "abogado"),
            "activo": True,
            "fecha_creacion": usuario.get("fecha_creacion", ""),
        },
    }


@router.get("/verificar")
def verificar(request: Request):
    """Verifica si el token actual es válido."""
    payload = get_current_user(request)
    if not payload:
        return {"autenticado": False}

    return {
        "autenticado": True,
        "email": payload.get("email"),
        "nombre": payload.get("nombre"),
        "rol": payload.get("rol"),
    }


@router.post("/logout")
def logout():
    """Logout (client-side: el frontend elimina el token)."""
    return {"ok": True, "mensaje": "Sesión cerrada"}


@router.post("/refresh")
def refresh(request: Request):
    """Renueva el JWT si es válido. Implementa sliding expiry — extiende 24h adicionales."""
    token = _get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Token requerido")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

    nuevo_token = create_token(
        user_id=payload["sub"],
        email=payload["email"],
        nombre=payload.get("nombre", ""),
        rol=payload.get("rol", "abogado"),
    )
    return {"access_token": nuevo_token, "token_type": "bearer"}
