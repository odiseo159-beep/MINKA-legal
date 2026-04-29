# auth_api.py — Endpoints de autenticación
# POST /auth/login, POST /auth/register, GET /auth/verificar, POST /auth/logout

import time
import re
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from agent.auth import hash_password, verify_password, create_token, decode_token
from agent.users_db import obtener_usuario_por_email, obtener_usuario_por_id, usuario_existe, crear_usuario

router = APIRouter(prefix="/auth", tags=["auth"])

# RFC 5322 simplificado — suficiente para validación pre-envío
_EMAIL_REGEX = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")

# Rate limiter en memoria: máx 10 intentos por IP cada 15 minutos
_login_attempts: dict[str, list[float]] = defaultdict(list)
_LOGIN_MAX = 10
_LOGIN_WINDOW = 15 * 60  # 15 minutos en segundos

# Rate limiter para register: 5 intentos por IP por hora
_register_attempts: dict[str, list[float]] = defaultdict(list)
_REGISTER_MAX = 5
_REGISTER_WINDOW = 60 * 60  # 1 hora


def _check_login_rate(ip: str) -> None:
    now = time.time()
    attempts = _login_attempts[ip]
    # Limpiar intentos fuera de la ventana
    _login_attempts[ip] = [t for t in attempts if now - t < _LOGIN_WINDOW]
    if len(_login_attempts[ip]) >= _LOGIN_MAX:
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos de inicio de sesión. Espera 15 minutos."
        )
    _login_attempts[ip].append(now)


def _check_register_rate(ip: str) -> None:
    """Rate limiter para registro: 5 cuentas por IP por hora."""
    now = time.time()
    attempts = _register_attempts[ip]
    _register_attempts[ip] = [t for t in attempts if now - t < _REGISTER_WINDOW]
    if len(_register_attempts[ip]) >= _REGISTER_MAX:
        raise HTTPException(
            status_code=429,
            detail="Demasiados intentos de registro. Intenta de nuevo en 1 hora."
        )
    _register_attempts[ip].append(now)


def _validar_email(email: str) -> str:
    """Valida formato de email y retorna versión normalizada (lowercase)."""
    if not email or not isinstance(email, str):
        raise HTTPException(status_code=422, detail="Correo electrónico requerido")
    email = email.strip().lower()
    if len(email) > 254 or not _EMAIL_REGEX.match(email):
        raise HTTPException(status_code=422, detail="Formato de correo electrónico inválido")
    return email


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
def login(data: LoginRequest, request: Request):
    """Autenticación con email y password. Retorna JWT token."""
    ip = request.client.host if request.client else "unknown"
    _check_login_rate(ip)
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
def register(data: RegisterRequest, request: Request):
    """Registro de nuevo usuario. Retorna JWT token (auto-login).

    Endurecido contra:
    - Account enumeration (email duplicado y password corto retornan el mismo error genérico)
    - Brute-force registration (rate limit por IP)
    - Email malformado (validación regex previa)
    """
    ip = request.client.host if request.client else "unknown"
    _check_register_rate(ip)

    email = _validar_email(data.email)

    if len(data.password) < 10:
        # Mismo mensaje genérico que email duplicado para evitar enumeración
        raise HTTPException(
            status_code=422,
            detail="No se pudo crear la cuenta. Verifica que el correo sea válido y la contraseña tenga al menos 10 caracteres."
        )

    if usuario_existe(email):
        # Mensaje genérico — no revelar si existe o no
        raise HTTPException(
            status_code=422,
            detail="No se pudo crear la cuenta. Verifica que el correo sea válido y la contraseña tenga al menos 10 caracteres."
        )

    password_hash = hash_password(data.password)
    usuario = crear_usuario(
        email=email,
        password_hash=password_hash,
        nombre=data.nombre,
        rol="abogado",
    )

    # Auto-crear el perfil de abogado vinculado al email del usuario. Sin esto,
    # entre el registro y el primer "Guardar perfil" en Configuración, el
    # usuario podría crear casos que quedarían con abogado_id=NULL (legacy
    # huérfanos) y se mostrarían en queries de otros tenants. Falla silenciosa
    # si ya existe (UNIQUE en email) — el endpoint de Configuración lo detecta.
    try:
        from agent.lawyers_db import crear_abogado, obtener_abogado_por_email
        if not obtener_abogado_por_email(email):
            crear_abogado({
                "email": email,
                "nombre": data.nombre or "",
                "telefono": "",
                "whatsapp_numero": "",
                "colegiatura": "",
                "especialidades": [],
                "activo": True,
            })
    except Exception:
        # No queremos que un error en auto-crear abogado bloquee el registro.
        # El usuario puede completarlo después en Configuración.
        import logging
        logging.getLogger("agentkit").exception(
            "[REGISTER] No se pudo auto-crear abogado para %s — continuando", email
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
    """Verifica si el token actual es válido. Re-lee rol desde BD por si fue actualizado."""
    payload = get_current_user(request)
    if not payload:
        return {"autenticado": False}

    # Re-leer desde BD para reflejar cambios de rol (p.ej. promoción a admin) sin re-login
    usuario = obtener_usuario_por_email(payload.get("email", ""))
    rol_actual = usuario.get("rol", payload.get("rol", "abogado")) if usuario else payload.get("rol")

    return {
        "autenticado": True,
        "email": payload.get("email"),
        "nombre": payload.get("nombre"),
        "rol": rol_actual,
    }


@router.post("/logout")
def logout():
    """Logout (client-side: el frontend elimina el token)."""
    return {"ok": True, "mensaje": "Sesión cerrada"}


@router.post("/refresh")
def refresh(request: Request):
    """Renueva el JWT si es válido. Sliding expiry de 24h. Re-lee rol desde BD."""
    token = _get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Token requerido")
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Token inválido o expirado")

    # Re-leer desde BD para que cambios de rol (p.ej. admin) se propaguen al refrescar
    usuario = obtener_usuario_por_email(payload.get("email", ""))
    if not usuario:
        raise HTTPException(status_code=401, detail="Usuario no encontrado")

    nuevo_token = create_token(
        user_id=usuario["id"],
        email=usuario["email"],
        nombre=usuario.get("nombre", payload.get("nombre", "")),
        rol=usuario.get("rol", "abogado"),
    )
    return {"access_token": nuevo_token, "token_type": "bearer"}
