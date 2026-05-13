# lawyers_db.py — Modelos de Abogado y EstudioJuridico
# Minka — Asistente Legal AI

import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.getenv("DATABASE_PATH", "agentkit.db")
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)


def init_lawyers_db():
    """Crea las tablas estudios y abogados si no existen. Seguro de ejecutar múltiples veces."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS estudios (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre           TEXT NOT NULL,
            ruc              TEXT,
            direccion        TEXT,
            plan             TEXT DEFAULT 'starter',
            fecha_creacion   TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS abogados (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            estudio_id       INTEGER REFERENCES estudios(id),
            nombre           TEXT NOT NULL,
            email            TEXT UNIQUE,
            telefono         TEXT,
            whatsapp_numero  TEXT,
            colegiatura      TEXT,
            especialidades   TEXT DEFAULT '[]',
            activo           INTEGER DEFAULT 1,
            fecha_creacion   TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Agregar columna abogado_id a casos si no existe (migration segura)
    try:
        cursor.execute("ALTER TABLE casos ADD COLUMN abogado_id INTEGER REFERENCES abogados(id)")
    except sqlite3.OperationalError:
        pass  # La columna ya existe

    # Migraciones de canal Whapi por abogado
    for columna, definicion in [
        ("whapi_token",      "TEXT"),
        ("whapi_channel_id", "TEXT"),
        ("modo_atencion",    "TEXT DEFAULT 'individual'"),  # 'individual' | 'estudio'
    ]:
        try:
            cursor.execute(f"ALTER TABLE abogados ADD COLUMN {columna} {definicion}")
        except sqlite3.OperationalError:
            pass

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_abogados_canal ON abogados(whapi_channel_id)")

    # UNIQUE partial index en whatsapp_numero — clave para el modelo single-channel
    # donde el bot identifica al abogado por su número. Si dos abogados tuvieran el
    # mismo whatsapp_numero, el routing del webhook entrante sería ambiguo.
    # Partial index (WHERE not null/empty) permite múltiples abogados sin número
    # registrado durante el onboarding sin disparar el constraint.
    # Try/except por si hay duplicados en producción al deployar — log warning
    # en lugar de fallar el startup. El admin debe limpiar manualmente.
    try:
        cursor.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_abogados_whatsapp_numero "
            "ON abogados(whatsapp_numero) WHERE whatsapp_numero IS NOT NULL "
            "AND whatsapp_numero != ''"
        )
    except sqlite3.IntegrityError as e:
        import logging as _logging
        _logging.getLogger("agentkit").warning(
            f"[INIT] No se pudo crear UNIQUE INDEX en abogados.whatsapp_numero: {e}. "
            f"Hay duplicados en producción — limpiar manualmente para que el routing "
            f"del bot por número sea inequívoco."
        )

    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# Estudios Jurídicos
# ─────────────────────────────────────────────

def crear_estudio(data: dict) -> dict:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO estudios (nombre, ruc, direccion, plan)
        VALUES (?, ?, ?, ?)
    """, (
        data.get("nombre", ""),
        data.get("ruc", ""),
        data.get("direccion", ""),
        data.get("plan", "starter"),
    ))
    conn.commit()
    estudio_id = cursor.lastrowid
    conn.close()
    return obtener_estudio(estudio_id)


def obtener_estudio(estudio_id: int) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM estudios WHERE id = ?", (estudio_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def listar_estudios() -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM estudios ORDER BY nombre")
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def actualizar_estudio(estudio_id: int, data: dict) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    campos = ["nombre", "ruc", "direccion", "plan"]
    updates = [f"{c} = ?" for c in campos if c in data]
    values = [data[c] for c in campos if c in data]
    if not updates:
        conn.close()
        return obtener_estudio(estudio_id)
    values.append(estudio_id)
    cursor.execute(f"UPDATE estudios SET {', '.join(updates)} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return obtener_estudio(estudio_id)


# ─────────────────────────────────────────────
# Abogados
# ─────────────────────────────────────────────

def _normalizar_telefono(telefono: str) -> str:
    if not telefono:
        return ""
    t = telefono.strip().replace(" ", "").replace("-", "").replace("+", "")
    if t.startswith("51") and len(t) == 11:
        return t[2:]
    return t


def crear_abogado(data: dict) -> dict:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    especialidades = data.get("especialidades", [])
    if isinstance(especialidades, list):
        especialidades = json.dumps(especialidades, ensure_ascii=False)
    cursor.execute("""
        INSERT INTO abogados (estudio_id, nombre, email, telefono, whatsapp_numero,
                              colegiatura, especialidades, activo)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data.get("estudio_id"),
        data.get("nombre", ""),
        data.get("email", ""),
        _normalizar_telefono(data.get("telefono", "")),
        _normalizar_telefono(data.get("whatsapp_numero", "")),
        data.get("colegiatura", ""),
        especialidades,
        1 if data.get("activo", True) else 0,
    ))
    conn.commit()
    abogado_id = cursor.lastrowid
    conn.close()
    return obtener_abogado(abogado_id)


def obtener_abogado(abogado_id: int) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM abogados WHERE id = ?", (abogado_id,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["especialidades"] = json.loads(d.get("especialidades") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["especialidades"] = []
    return d


def obtener_abogado_por_whatsapp(numero: str) -> dict | None:
    """Busca un abogado por su número de WhatsApp. Clave para detectar comandos."""
    numero_norm = _normalizar_telefono(numero)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM abogados WHERE (whatsapp_numero = ? OR telefono = ?) AND activo = 1",
        (numero_norm, numero_norm),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["especialidades"] = json.loads(d.get("especialidades") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["especialidades"] = []
    return d


def obtener_abogado_por_email(email: str) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM abogados WHERE email = ?", (email,))
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["especialidades"] = json.loads(d.get("especialidades") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["especialidades"] = []
    return d


def listar_abogados(solo_activos: bool = False) -> list:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if solo_activos:
        cursor.execute("SELECT * FROM abogados WHERE activo = 1 ORDER BY nombre")
    else:
        cursor.execute("SELECT * FROM abogados ORDER BY nombre")
    rows = cursor.fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["especialidades"] = json.loads(d.get("especialidades") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["especialidades"] = []
        result.append(d)
    return result


def listar_abogados_por_email(email: str, solo_activos: bool = False) -> list:
    """Devuelve sólo los abogados cuyo email coincide con el del usuario autenticado.

    Usado para multi-tenancy: cada usuario debería ver sólo SU registro de abogado,
    no los de otros estudios/cuentas que comparten la base de datos.
    """
    if not email:
        return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if solo_activos:
        cursor.execute(
            "SELECT * FROM abogados WHERE email = ? AND activo = 1 ORDER BY nombre",
            (email.lower(),),
        )
    else:
        cursor.execute(
            "SELECT * FROM abogados WHERE email = ? ORDER BY nombre",
            (email.lower(),),
        )
    rows = cursor.fetchall()
    conn.close()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["especialidades"] = json.loads(d.get("especialidades") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["especialidades"] = []
        result.append(d)
    return result


def listar_estudios_por_email(email: str) -> list:
    """Devuelve los estudios que tienen al menos un abogado con el email dado.

    Multi-tenant: el usuario sólo ve estudios donde figura como abogado.
    """
    if not email:
        return []
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT DISTINCT e.* FROM estudios e
        INNER JOIN abogados a ON a.estudio_id = e.id
        WHERE LOWER(a.email) = LOWER(?)
        ORDER BY e.nombre
        """,
        (email,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def obtener_abogado_por_canal(channel_id: str) -> dict | None:
    """Busca un abogado por el ID del canal Whapi configurado en su perfil."""
    if not channel_id:
        return None
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM abogados WHERE whapi_channel_id = ? AND activo = 1",
        (channel_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["especialidades"] = json.loads(d.get("especialidades") or "[]")
    except (json.JSONDecodeError, TypeError):
        d["especialidades"] = []
    return d


def actualizar_abogado(abogado_id: int, data: dict) -> dict | None:
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    campos = ["estudio_id", "nombre", "email", "telefono", "whatsapp_numero",
              "colegiatura", "especialidades", "activo",
              "whapi_token", "whapi_channel_id", "modo_atencion"]
    updates = []
    values = []
    for c in campos:
        if c not in data:
            continue
        if c in ("telefono", "whatsapp_numero"):
            updates.append(f"{c} = ?")
            values.append(_normalizar_telefono(data[c]))
        elif c == "especialidades":
            updates.append(f"{c} = ?")
            esp = data[c]
            values.append(json.dumps(esp if isinstance(esp, list) else [], ensure_ascii=False))
        elif c == "activo":
            updates.append(f"{c} = ?")
            values.append(1 if data[c] else 0)
        else:
            updates.append(f"{c} = ?")
            values.append(data[c])
    if not updates:
        conn.close()
        return obtener_abogado(abogado_id)
    values.append(abogado_id)
    cursor.execute(f"UPDATE abogados SET {', '.join(updates)} WHERE id = ?", values)
    conn.commit()
    conn.close()
    return obtener_abogado(abogado_id)


def eliminar_abogado(abogado_id: int) -> bool:
    """Soft delete: marca como inactivo en lugar de borrar."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("UPDATE abogados SET activo = 0 WHERE id = ?", (abogado_id,))
    ok = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return ok
