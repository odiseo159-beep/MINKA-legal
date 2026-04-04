# users_db.py — Modelo de datos para usuarios/abogados
# Usa la misma base de datos SQLite que cases_db.py

import sqlite3
import os
from datetime import datetime

DB_PATH = os.getenv("DATABASE_PATH", "agentkit.db")


def init_users_db():
    """Crea la tabla de usuarios si no existe."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            nombre TEXT,
            rol TEXT DEFAULT 'abogado',
            activo INTEGER DEFAULT 1,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def obtener_usuario_por_email(email: str) -> dict | None:
    """Busca un usuario por email."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM usuarios WHERE email = ? AND activo = 1", (email,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def obtener_usuario_por_id(user_id: int) -> dict | None:
    """Busca un usuario por ID."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM usuarios WHERE id = ? AND activo = 1", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def crear_usuario(email: str, password_hash: str, nombre: str = "", rol: str = "abogado") -> dict:
    """Crea un nuevo usuario."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO usuarios (email, password_hash, nombre, rol)
        VALUES (?, ?, ?, ?)
    """, (email, password_hash, nombre, rol))
    conn.commit()
    user_id = cursor.lastrowid
    conn.close()
    return obtener_usuario_por_id(user_id)


def usuario_existe(email: str) -> bool:
    """Verifica si ya existe un usuario con ese email."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM usuarios WHERE email = ?", (email,))
    existe = cursor.fetchone() is not None
    conn.close()
    return existe
