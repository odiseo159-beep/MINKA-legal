# events_db.py — Modelo de datos para EventoCalendario
# Minka — Asistente Legal AI para abogados peruanos

import sqlite3
import os
from datetime import datetime, timedelta

DB_PATH = os.getenv("DATABASE_PATH", "agentkit.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)


def init_events_db():
    """Crea la tabla eventos_calendario si no existe."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS eventos_calendario (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            caso_id             INTEGER,
            abogado_id          INTEGER,
            titulo              TEXT NOT NULL,
            fecha_hora          TEXT NOT NULL,
            tipo                TEXT DEFAULT 'audiencia',
            recordatorio_dias   INTEGER DEFAULT 1,
            notificado          INTEGER DEFAULT 0,
            notas               TEXT,
            fecha_creacion      TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()


def crear_evento(data: dict) -> dict:
    """Crea un nuevo evento en el calendario."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO eventos_calendario
            (caso_id, abogado_id, titulo, fecha_hora, tipo, recordatorio_dias, notas)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("caso_id"),
        data.get("abogado_id"),
        data.get("titulo", ""),
        data.get("fecha_hora", ""),
        data.get("tipo", "audiencia"),
        data.get("recordatorio_dias", 1),
        data.get("notas"),
    ))
    conn.commit()
    evento_id = cursor.lastrowid
    conn.close()
    return obtener_evento(evento_id)


def obtener_evento(evento_id: int) -> dict | None:
    """Obtiene un evento por su ID."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM eventos_calendario WHERE id = ?", (evento_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def listar_eventos(fecha_desde: str = None, fecha_hasta: str = None) -> list:
    """Lista todos los eventos, opcionalmente filtrados por rango de fechas."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    if fecha_desde and fecha_hasta:
        cursor.execute(
            "SELECT * FROM eventos_calendario WHERE fecha_hora >= ? AND fecha_hora <= ? ORDER BY fecha_hora ASC",
            (fecha_desde, fecha_hasta),
        )
    elif fecha_desde:
        cursor.execute(
            "SELECT * FROM eventos_calendario WHERE fecha_hora >= ? ORDER BY fecha_hora ASC",
            (fecha_desde,),
        )
    elif fecha_hasta:
        cursor.execute(
            "SELECT * FROM eventos_calendario WHERE fecha_hora <= ? ORDER BY fecha_hora ASC",
            (fecha_hasta,),
        )
    else:
        cursor.execute("SELECT * FROM eventos_calendario ORDER BY fecha_hora ASC")

    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def eventos_proximos(dias: int = 7) -> list:
    """Retorna eventos cuya fecha_hora esté dentro de los próximos `dias` días y aún no hayan sido notificados."""
    ahora = datetime.now()
    limite = ahora + timedelta(days=dias)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM eventos_calendario
        WHERE fecha_hora >= ?
          AND fecha_hora <= ?
          AND notificado = 0
        ORDER BY fecha_hora ASC
        """,
        (ahora.isoformat(), limite.isoformat()),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def marcar_notificado(evento_id: int):
    """Marca el evento como notificado (notificado = 1)."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE eventos_calendario SET notificado = 1 WHERE id = ?",
        (evento_id,),
    )
    conn.commit()
    conn.close()


def actualizar_evento(evento_id: int, data: dict) -> dict | None:
    """Actualiza un evento existente."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    campos_permitidos = [
        "caso_id", "abogado_id", "titulo", "fecha_hora",
        "tipo", "recordatorio_dias", "notas",
    ]
    updates = []
    values = []
    for campo in campos_permitidos:
        if campo in data:
            updates.append(f"{campo} = ?")
            values.append(data[campo])

    if not updates:
        conn.close()
        return obtener_evento(evento_id)

    values.append(evento_id)
    query = f"UPDATE eventos_calendario SET {', '.join(updates)} WHERE id = ?"
    cursor.execute(query, values)
    conn.commit()
    conn.close()
    return obtener_evento(evento_id)


def eliminar_evento(evento_id: int) -> bool:
    """Elimina un evento por su ID."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM eventos_calendario WHERE id = ?", (evento_id,))
    eliminado = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return eliminado
