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


def listar_eventos(
    fecha_desde: str = None,
    fecha_hasta: str = None,
    abogado_id: int | None = None,
) -> list:
    """Lista eventos, opcionalmente filtrados por rango de fechas y abogado.

    Si `abogado_id` se provee, solo retorna eventos de ese abogado (filtro
    multi-tenant). Si es None, retorna todos los eventos (uso interno: admin,
    APScheduler de notificaciones, backfill, etc.).
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    where_parts: list[str] = []
    params: list = []
    if fecha_desde:
        where_parts.append("fecha_hora >= ?")
        params.append(fecha_desde)
    if fecha_hasta:
        where_parts.append("fecha_hora <= ?")
        params.append(fecha_hasta)
    if abogado_id is not None:
        where_parts.append("abogado_id = ?")
        params.append(abogado_id)

    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
    cursor.execute(
        f"SELECT * FROM eventos_calendario {where_sql} ORDER BY fecha_hora ASC",
        params,
    )
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
