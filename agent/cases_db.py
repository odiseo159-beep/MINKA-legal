# cases_db.py — Modelo de datos para gestión de casos legales
# Se integra con la misma base de datos SQLite que usa memory.py

import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.getenv("DATABASE_PATH", "agentkit.db")

# Crear directorio de la base de datos si no existe (ej: /app/data/ en Railway)
os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)


def init_cases_db():
    """Crea la tabla de casos si no existe y aplica migraciones."""
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
            documento_texto TEXT,
            documento_url TEXT,
            documento_nombre TEXT,
            documento_tipo TEXT,
            fecha_creacion TEXT DEFAULT CURRENT_TIMESTAMP,
            fecha_actualizacion TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migraciones: agregar columnas si no existen en DBs anteriores
    for columna, definicion in [
        ("documento_texto", "TEXT"),
        ("documento_url", "TEXT"),
        ("documento_nombre", "TEXT"),
        ("documento_tipo", "TEXT"),
        ("version", "INTEGER DEFAULT 0"),
        ("eliminado", "INTEGER DEFAULT 0"),
        ("extracted_fields", "TEXT"),  # JSON snapshot de lo que Claude extrajo al crear el caso
    ]:
        try:
            cursor.execute(f"ALTER TABLE casos ADD COLUMN {columna} {definicion}")
        except Exception:
            pass  # La columna ya existe

    # Tabla de documentos por caso
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS caso_documentos (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            caso_id     INTEGER NOT NULL,
            nombre      TEXT NOT NULL,
            tipo_archivo TEXT NOT NULL,
            key_r2      TEXT NOT NULL,
            resumen_json     TEXT,
            texto_relevante  TEXT,
            fecha_subida TEXT NOT NULL,
            FOREIGN KEY (caso_id) REFERENCES casos(id) ON DELETE CASCADE
        )
    """)

    # Tabla de mensajes de chat por caso
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_mensajes (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            caso_id  INTEGER NOT NULL,
            role     TEXT NOT NULL,
            content  TEXT NOT NULL,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (caso_id) REFERENCES casos(id) ON DELETE CASCADE
        )
    """)
    # Índices para búsquedas frecuentes
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_casos_telefono ON casos(telefono)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_casos_estado ON casos(estado)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_casos_expediente ON casos(expediente)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_casos_fecha_act ON casos(fecha_actualizacion DESC)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_chat_caso_id ON chat_mensajes(caso_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_docs_caso_id ON caso_documentos(caso_id)")
    conn.commit()
    conn.close()


def normalizar_telefono(telefono: str) -> str:
    """Normaliza el número de teléfono para búsqueda consistente.
    Elimina espacios, guiones, sufijos WhatsApp (@s.whatsapp.net) y prefijos +51/51."""
    # Eliminar sufijo de WhatsApp (ej: 51912345678@s.whatsapp.net)
    if "@" in telefono:
        telefono = telefono.split("@")[0]
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
                          proxima_fecha, proxima_accion, documentos_pendientes, notas,
                          abogado_asignado, documento_texto, extracted_fields)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        data.get("documento_texto", ""),
        data.get("extracted_fields"),
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


def buscar_por_telefono(telefono: str, abogado_id: int | None = None) -> list:
    """Busca casos asociados a un número de teléfono.

    Si se proporciona abogado_id, filtra solo los casos de ese abogado (multi-tenant).
    Excluye siempre los casos eliminados (soft-delete).
    """
    telefono = normalizar_telefono(telefono)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    base_where = "(telefono = ? OR telefono = ? OR telefono = ?) AND (eliminado IS NULL OR eliminado = 0)"
    params: list = [telefono, f"51{telefono}", f"+51{telefono}"]

    if abogado_id is not None:
        cursor.execute(
            f"SELECT * FROM casos WHERE {base_where} AND abogado_id = ? ORDER BY fecha_actualizacion DESC",
            (*params, abogado_id),
        )
    else:
        cursor.execute(
            f"SELECT * FROM casos WHERE {base_where} ORDER BY fecha_actualizacion DESC",
            params,
        )

    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def listar_casos(filtro_estado: str = None) -> list:
    """Lista todos los casos activos (no eliminados), opcionalmente filtrados por estado."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    if filtro_estado:
        cursor.execute(
            "SELECT * FROM casos WHERE estado = ? AND (eliminado IS NULL OR eliminado = 0) ORDER BY fecha_actualizacion DESC",
            (filtro_estado,)
        )
    else:
        cursor.execute(
            "SELECT * FROM casos WHERE (eliminado IS NULL OR eliminado = 0) ORDER BY fecha_actualizacion DESC"
        )
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
        "notas", "abogado_asignado", "telefono", "documento_texto",
        "documento_url", "documento_nombre", "documento_tipo",
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
    updates.append("version = COALESCE(version, 0) + 1")
    values.append(caso_id)
    
    query = f"UPDATE casos SET {', '.join(updates)} WHERE id = ?"
    cursor.execute(query, values)
    conn.commit()
    conn.close()
    return obtener_caso(caso_id)


def eliminar_caso(caso_id: int) -> bool:
    """Marca un caso como eliminado (soft delete). Preserva historial."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE casos SET eliminado = 1, fecha_actualizacion = ? WHERE id = ?",
        (datetime.now().isoformat(), caso_id)
    )
    eliminado = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return eliminado


# ─────────────────────────────────────────────
# CRUD — Documentos por caso
# ─────────────────────────────────────────────

def crear_documento_caso(
    caso_id: int,
    nombre: str,
    tipo_archivo: str,
    key_r2: str,
    resumen_json: str = "",
    texto_relevante: str = "",
) -> dict:
    """Inserta un documento en caso_documentos y retorna el registro creado."""
    from datetime import timezone
    fecha = datetime.now(timezone.utc).isoformat()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """INSERT INTO caso_documentos
           (caso_id, nombre, tipo_archivo, key_r2, resumen_json, texto_relevante, fecha_subida)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (caso_id, nombre, tipo_archivo, key_r2, resumen_json, texto_relevante, fecha),
    )
    conn.commit()
    doc_id = cursor.lastrowid
    conn.close()
    return obtener_documento_caso(doc_id)


def listar_documentos_caso(caso_id: int) -> list:
    """Lista todos los documentos de un caso, ordenados por fecha_subida desc."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, caso_id, nombre, tipo_archivo, key_r2,
                  resumen_json, texto_relevante, fecha_subida
           FROM caso_documentos
           WHERE caso_id = ?
           ORDER BY fecha_subida DESC""",
        (caso_id,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]


def obtener_documento_caso(doc_id: int) -> dict:
    """Obtiene un documento por su ID."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """SELECT id, caso_id, nombre, tipo_archivo, key_r2,
                  resumen_json, texto_relevante, fecha_subida
           FROM caso_documentos WHERE id = ?""",
        (doc_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return dict(row)
    return None


def eliminar_documento_caso(doc_id: int) -> bool:
    """Elimina un documento por su ID. Retorna True si existía."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM caso_documentos WHERE id = ?", (doc_id,))
    eliminado = cursor.rowcount > 0
    conn.commit()
    conn.close()
    return eliminado


# ─────────────────────────────────────────────
# CRUD — Chat por caso
# ─────────────────────────────────────────────

def guardar_mensaje_chat(caso_id: int, role: str, content: str) -> None:
    """Guarda un mensaje del chat en la base de datos."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chat_mensajes (caso_id, role, content) VALUES (?, ?, ?)",
        (caso_id, role, content)
    )
    conn.commit()
    conn.close()


def listar_mensajes_chat(caso_id: int, limit: int = 100) -> list:
    """Retorna los últimos `limit` mensajes del chat de un caso, en orden cronológico."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        """SELECT role, content, timestamp FROM chat_mensajes
           WHERE caso_id = ?
           ORDER BY id DESC LIMIT ?""",
        (caso_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    # Invertir para orden cronológico (más antiguo primero)
    return [dict(r) for r in reversed(rows)]


def limpiar_chat_caso(caso_id: int) -> None:
    """Elimina todos los mensajes del chat de un caso."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_mensajes WHERE caso_id = ?", (caso_id,))
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────
# Feedback loop — correcciones del abogado
# ─────────────────────────────────────────────

def init_corrections_db():
    """Crea la tabla corrections para capturar diferencias entre extracción IA y datos finales del abogado."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS corrections (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            caso_id     INTEGER NOT NULL,
            abogado_id  INTEGER,
            campo       TEXT NOT NULL,
            valor_claude TEXT,
            valor_abogado TEXT,
            tipo_caso   TEXT,
            created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (caso_id) REFERENCES casos(id)
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_corrections_campo    ON corrections(campo)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_corrections_tipo     ON corrections(tipo_caso)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_corrections_caso     ON corrections(caso_id)")
    conn.commit()
    conn.close()


_CAMPOS_CORREGIBLES = [
    "expediente", "nombre_cliente", "telefono", "tipo_caso",
    "estado", "proxima_accion", "documentos_pendientes", "notas", "proxima_fecha",
]


def capturar_correcciones(
    caso_id: int,
    abogado_id: int | None,
    extracted_fields_json: str | None,
    datos_guardados: dict,
    tipo_caso: str | None,
) -> None:
    """Compara lo que Claude extrajo con lo que el abogado guardó y registra las diferencias.

    Se llama tanto al crear (para detectar cambios pre-guardado) como al actualizar el caso.
    Solo registra diferencias: correcciones (Claude extrajo X, abogado puso Y) y
    adiciones (Claude no encontró nada, abogado llenó el campo).
    """
    if not extracted_fields_json:
        return
    try:
        extraido = json.loads(extracted_fields_json)
    except Exception:
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    for campo in _CAMPOS_CORREGIBLES:
        if campo not in datos_guardados:
            continue
        valor_nuevo = datos_guardados.get(campo) or ""
        valor_extraido = extraido.get(campo) or ""

        if not valor_nuevo and not valor_extraido:
            continue
        if valor_nuevo == valor_extraido:
            continue

        cursor.execute(
            """INSERT INTO corrections (caso_id, abogado_id, campo, valor_claude, valor_abogado, tipo_caso)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (caso_id, abogado_id, campo,
             valor_extraido or None,
             valor_nuevo or None,
             tipo_caso),
        )

    conn.commit()
    conn.close()
