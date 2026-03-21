# cases_db.py — Modelo de datos para gestión de casos legales
# Se integra con la misma base de datos SQLite que usa memory.py

import sqlite3
import os
from datetime import datetime

DB_PATH = os.getenv("DATABASE_PATH", "agentkit.db")


def init_cases_db():
    """Crea la tabla de casos si no existe."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS casos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telefono TEXT NOT NULL,
            nombre_cliente TEXT NOT NULL,
            expediente TEXT,
            tipo_caso TEXT,
            estado TEXT DEFAULT 'nuevo',
            proxima_fecha TEXT,
            proxima_accion TEXT,
            documentos_pendientes TEXT,
            notas TEXT,
            abogado_asignado TEXT,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            fecha_actualizacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def normalizar_telefono(telefono: str) -> str:
    """Normaliza el número de teléfono para búsqueda consistente.
    Elimina espacios, guiones, y el prefijo +51 si existe."""
    telefono = telefono.strip().replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    if telefono.startswith("+51"):
        telefono = telefono[3:]
    if telefono.startswith("51") and len(telefono) == 11:
        telefono = telefono[2:]
    return telefono


def crear_caso(data: dict) -> dict:
    """Crea un nuevo caso."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    telefono = normalizar_telefono(data.get("telefono", ""))
    cursor.execute("""
        INSERT INTO casos (telefono, nombre_cliente, expediente, tipo_caso, estado,
                          proxima_fecha, proxima_accion, documentos_pendientes, notas, abogado_asignado)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        telefono,
        data.get("nombre_cliente", ""),
        data.get("expediente", ""),
        data.get("tipo_caso", ""),
        data.get("estado", "nuevo"),
        data.get("proxima_fecha", ""),
        data.get("proxima_accion", ""),
        data.get("documentos_pendientes", ""),
        data.get("notas", ""),
        data.get("abogado_asignado", ""),
    ))
    conn.commit()
    caso_id = cursor.lastrowid
    conn.close()
    return obtener_caso(caso_id)


def obtener_caso(caso_id: int) -> dict:
    """Obtiene un caso por su ID."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM casos WHERE id = ?", (caso_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def buscar_por_telefono(telefono: str) -> list:
    """Busca todos los casos asociados a un número de teléfono."""
    telefono = normalizar_telefono(telefono)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    # Buscar con diferentes variantes del teléfono
    cursor.execute("""
        SELECT * FROM casos 
        WHERE telefono = ? OR telefono = ? OR telefono = ?
        ORDER BY fecha_actualizacion DESC
    """, (telefono, f"51{telefono}", f"+51{telefono}"))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def listar_casos(filtro_estado: str = None) -> list:
    """Lista todos los casos, opcionalmente filtrados por estado."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if filtro_estado:
        cursor.execute("SELECT * FROM casos WHERE estado = ? ORDER BY fecha_actualizacion DESC", (filtro_estado,))
    else:
        cursor.execute("SELECT * FROM casos ORDER BY fecha_actualizacion DESC")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def actualizar_caso(caso_id: int, data: dict) -> dict:
    """Actualiza un caso existente."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    campos_permitidos = [
        "nombre_cliente", "expediente", "tipo_caso", "estado",
        "proxima_fecha", "proxima_accion", "documentos_pendientes",
        "notas", "abogado_asignado", "telefono"
    ]
    
    updates = []
    values = []
    for campo in campos_permitidos:
        if campo in data:
            if campo == "telefono":
                updates.append(f"{campo} = ?")
                values.append(normalizar_telefono(data[campo]))
            else:
                updates.append(f"{campo} = ?")
                values.append(data[campo])
    
    if not updates:
        return obtener_caso(caso_id)
    
    updates.append("fecha_actualizacion = ?")
    values.append(datetime.now().isoformat())
    values.append(caso_id)
    
    query = f"UPDATE casos SET {', '.join(updates)} WHERE id = ?"
    cursor.execute(query, values)
    conn.commit()
    conn.close()
    return obtener_caso(caso_id)


def eliminar_caso(caso_id: int) -> bool:
    """Elimina un caso por su ID."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM casos WHERE id = ?", (caso_id,))
    eliminado = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return eliminado
