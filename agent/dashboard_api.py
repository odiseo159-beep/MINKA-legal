import os
import json
import httpx
from fastapi import APIRouter, HTTPException, UploadFile, File, Request, Depends
from fastapi.responses import HTMLResponse
from agent.storage import r2_configured, upload_document, generate_presigned_url, delete_document
from pydantic import BaseModel
from typing import Optional, List
from agent.cases_db import (
    listar_casos,
    obtener_caso,
    crear_caso,
    actualizar_caso,
    eliminar_caso,
    crear_documento_caso,
    listar_documentos_caso,
    obtener_documento_caso,
    eliminar_documento_caso,
    guardar_mensaje_chat,
    listar_mensajes_chat,
    limpiar_chat_caso,
    capturar_correcciones,
    DB_PATH,
)
from agent.crypto import compress_encrypt, decrypt_decompress
from agent.lawyers_db import (
    listar_abogados,
    listar_abogados_por_email,
    obtener_abogado,
    crear_abogado,
    actualizar_abogado,
    eliminar_abogado,
    listar_estudios,
    listar_estudios_por_email,
    obtener_estudio,
    crear_estudio,
    actualizar_estudio,
)
from agent.document_extractor import extraer_datos_documento, extraer_resumen_estructurado
from agent.legal_advisor import generar_consejo_procesal
from agent.rag import buscar_normativa, formatear_para_prompt
from agent.auth_api import get_current_user
from agent.events_db import (
    listar_eventos,
    obtener_evento,
    crear_evento,
    actualizar_evento,
    eliminar_evento,
)
from agent.deadline_calculator import calcular_vencimiento, dias_restantes_habiles
from agent.prompts import CHAT_CASO_SYSTEM
from agent.legal_agent import ejecutar_agente, ACCIONES_VALIDAS
from pydantic import Field

router = APIRouter()

# Cache module-level: caso_id → (doc_count, chunks_list)
_doc_chunks_cache: dict[int, tuple[int, list[str]]] = {}

WHAPI_TOKEN = os.getenv("WHAPI_TOKEN")
REQUIRE_AUTH = os.getenv("REQUIRE_AUTH", "true").lower() == "true"


def require_auth(request: Request):
    """Dependency que verifica autenticación en endpoints protegidos."""
    if not REQUIRE_AUTH:
        return None
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="No autorizado. Inicia sesión.")
    return user


def get_abogado_for_user(user: dict | None) -> dict | None:
    """Mapea user (JWT payload) → abogado record por email.

    Devuelve None si no hay user (auth desactivada) o si el usuario no tiene perfil de abogado.
    """
    if not user:
        return None
    from agent.lawyers_db import obtener_abogado_por_email
    email = user.get("email")
    if not email:
        return None
    return obtener_abogado_por_email(email)


def require_caso_access(caso_id: int, user=Depends(require_auth)) -> dict:
    """Dependency: valida que el usuario autenticado tenga acceso al caso.

    Devuelve el dict del caso (para evitar duplicar `obtener_caso` en el handler).
    Reglas:
    - Si REQUIRE_AUTH=false (dev), devuelve el caso sin validar.
    - Admins (rol=admin) pueden ver cualquier caso.
    - Abogados solo ven casos cuyo abogado_id coincide con el suyo.
    - Casos legacy sin abogado_id se permiten (compatibilidad temporal con log de warning).
    - 404 ante intento de acceso ajeno (no leak de existencia).
    """
    caso = obtener_caso(caso_id)
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")

    if not REQUIRE_AUTH:
        return caso

    if (user or {}).get("rol") == "admin":
        return caso

    abogado = get_abogado_for_user(user)
    if not abogado:
        raise HTTPException(
            status_code=403,
            detail="Tu cuenta no está vinculada a un perfil de abogado. Crea uno en Configuración.",
        )

    caso_abogado_id = caso.get("abogado_id")
    if caso_abogado_id is None:
        # Legacy: no enforcement, but log it
        import logging as _logging
        _logging.getLogger("agentkit").warning(
            f"[IDOR] caso {caso_id} sin abogado_id — permitiendo acceso a abogado {abogado['id']} (legacy)"
        )
        return caso

    if caso_abogado_id != abogado["id"]:
        raise HTTPException(status_code=404, detail="Caso no encontrado")

    return caso


WHAPI_API_URL = os.getenv("WHAPI_API_URL", "https://gate.whapi.cloud")

# ─────────────────────────────────────────────
# Modelos
# ─────────────────────────────────────────────

class CaseCreate(BaseModel):
    telefono: str
    nombre_cliente: str
    expediente: Optional[str] = None
    tipo_caso: Optional[str] = None
    estado: Optional[str] = "nuevo"
    proxima_fecha: Optional[str] = None
    proxima_accion: Optional[str] = None
    documentos_pendientes: Optional[str] = None
    notas: Optional[str] = None
    abogado_asignado: Optional[str] = None
    documento_texto: Optional[str] = None
    extracted_fields: Optional[str] = None  # JSON snapshot de extracción IA — para feedback loop

class CaseUpdate(BaseModel):
    telefono: Optional[str] = None
    nombre_cliente: Optional[str] = None
    expediente: Optional[str] = None
    tipo_caso: Optional[str] = None
    estado: Optional[str] = None
    proxima_fecha: Optional[str] = None
    proxima_accion: Optional[str] = None
    documentos_pendientes: Optional[str] = None
    notas: Optional[str] = None
    abogado_asignado: Optional[str] = None
    documento_texto: Optional[str] = None
    notificar_cliente: Optional[bool] = True

class ChatRequest(BaseModel):
    pregunta: str

class AgentRequest(BaseModel):
    accion: str
    parametros: dict = Field(default_factory=dict)

# ─────────────────────────────────────────────
# Notificación proactiva vía Whapi
# ─────────────────────────────────────────────

ESTADOS_LABELS = {
    "nuevo":               "📋 Nuevo",
    "en_tramite":          "⚙️ En trámite",
    "en_audiencia":        "⚖️ En audiencia",
    "pendiente_documento": "📄 Pendiente de documento",
    "en_revision":         "🔍 En revisión",
    "en_apelacion":        "📢 En apelación",
    "resuelto":            "✅ Resuelto",
    "archivado":           "🗂️ Archivado",
}

def _tel_whatsapp(telefono: str) -> str:
    t = telefono.strip().replace(" ", "").replace("-", "").replace("+", "")
    if len(t) == 9:
        return f"51{t}"
    if t.startswith("51") and len(t) == 11:
        return t
    return t

async def enviar_notificacion_whatsapp(caso: dict) -> bool:
    """Envía notificación al cliente vía WhatsApp.

    Usa el token Whapi específico del abogado dueño del caso (multi-tenant).
    Si el caso no tiene abogado_id o el abogado no tiene token configurado,
    cae al WHAPI_TOKEN global (legacy).
    El mensaje se personaliza con el nombre del abogado y/o del estudio.
    """
    # Determinar qué token usar y obtener datos del abogado/estudio para personalización
    token = None
    abogado = None
    estudio = None
    if caso.get("abogado_id"):
        from agent.lawyers_db import obtener_abogado, obtener_estudio
        abogado = obtener_abogado(caso["abogado_id"])
        if abogado:
            if abogado.get("whapi_token"):
                token = abogado["whapi_token"]
            if abogado.get("estudio_id"):
                estudio = obtener_estudio(abogado["estudio_id"])
    if not token:
        token = WHAPI_TOKEN
    if not token:
        print("[Notificación] No hay token Whapi disponible (ni del abogado ni global), omitiendo.")
        return False

    telefono_wa  = _tel_whatsapp(caso.get("telefono", ""))
    nombre       = caso.get("nombre_cliente", "cliente")
    estado       = caso.get("estado", "")
    estado_label = ESTADOS_LABELS.get(estado, estado)
    expediente   = caso.get("expediente") or ""
    proxima_fecha   = caso.get("proxima_fecha") or ""
    proxima_accion  = caso.get("proxima_accion") or ""
    documentos      = caso.get("documentos_pendientes") or ""

    nombre_abogado = (abogado.get("nombre") if abogado else "") or ""
    nombre_estudio = (estudio.get("nombre") if estudio else "") or ""

    # Encabezado: prefiere nombre del estudio, luego del abogado, luego genérico
    if nombre_estudio:
        encabezado = f"Hola {nombre}, le escribimos del estudio {nombre_estudio} sobre su caso."
    elif nombre_abogado:
        encabezado = f"Hola {nombre}, le escribimos del despacho del Dr(a). {nombre_abogado} sobre su caso."
    else:
        encabezado = f"Hola {nombre}, le escribimos sobre su caso."

    # Firma: nombre del abogado si existe; sino, nombre del estudio; sino sin firma
    firma = nombre_abogado or nombre_estudio

    lineas = [
        f"👋 {encabezado}",
        "",
        "*Actualización de su caso:*",
        f"📁 Expediente: {expediente}" if expediente else None,
        f"📊 Estado: {estado_label}",
    ]
    if proxima_fecha:
        lineas.append(f"📅 Próxima fecha: {proxima_fecha}")
    if proxima_accion:
        lineas.append(f"▶️ Próxima acción: {proxima_accion}")
    if documentos:
        lineas.append(f"📎 Documentos pendientes: {documentos}")
    lineas.append("")
    lineas.append("Si tiene alguna consulta, puede responder a este mensaje.")
    if firma:
        lineas.append(f"— _{firma}_")

    mensaje = "\n".join(l for l in lineas if l is not None)
    payload = {
        "to": f"{telefono_wa}@s.whatsapp.net",
        "body": mensaje,
        "typing_time": 1,
    }

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f"{WHAPI_API_URL}/messages/text",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            ok = response.status_code in (200, 201)
            scope = "ab=" + str(caso.get("abogado_id")) if caso.get("abogado_id") else "global"
            print(f"[Notificación {scope}] {'OK' if ok else 'FAIL'} {response.status_code} -> {telefono_wa}")
            if not ok:
                print(f"[Notificación] response body: {response.text[:300]}")
            return ok
    except Exception as e:
        print(f"[Notificación] Excepción: {e}")
        return False

# ─────────────────────────────────────────────
# Endpoints API REST — Casos
# ─────────────────────────────────────────────

@router.get("/api/casos")
def api_listar_casos(request: Request, estado: Optional[str] = None, buscar: Optional[str] = None, user=Depends(require_auth)):
    # Multi-tenant: admins ven todo, abogados solo sus casos
    abogado_id = None
    if user and user.get("rol") != "admin":
        abogado = get_abogado_for_user(user)
        abogado_id = abogado["id"] if abogado else -1  # -1 fuerza no-resultados si no hay perfil
    casos = listar_casos(filtro_estado=estado, abogado_id=abogado_id)
    if buscar:
        q = buscar.lower()
        casos = [c for c in casos if
                 q in (c.get("nombre_cliente") or "").lower() or
                 q in (c.get("expediente") or "").lower() or
                 q in (c.get("telefono") or "").lower()]
    return casos

@router.get("/api/casos/stats")
def api_stats(request: Request, user=Depends(require_auth)):
    abogado_id = None
    if user and user.get("rol") != "admin":
        abogado = get_abogado_for_user(user)
        abogado_id = abogado["id"] if abogado else -1
    casos = listar_casos(abogado_id=abogado_id)
    total = len(casos)
    por_estado = {}
    for c in casos:
        e = c.get("estado", "desconocido")
        por_estado[e] = por_estado.get(e, 0) + 1
    activos = sum(v for k, v in por_estado.items()
                  if k not in ("resuelto", "archivado"))
    return {
        "total": total,
        "activos": activos,
        "resueltos": por_estado.get("resuelto", 0),
        "por_estado": por_estado,
    }

@router.get("/api/casos/{caso_id}")
def api_obtener_caso(caso_id: int, caso=Depends(require_caso_access)):
    return caso

@router.post("/api/casos", status_code=201)
def api_crear_caso(data: CaseCreate, request: Request, user=Depends(require_auth)):
    case_data = data.dict()
    # Auto-vincular al abogado del usuario autenticado (multi-tenant)
    abogado = get_abogado_for_user(user)
    if abogado:
        case_data["abogado_id"] = abogado["id"]
    extracted_json = case_data.get("extracted_fields")
    nuevo_caso = crear_caso(case_data)
    if extracted_json:
        capturar_correcciones(
            nuevo_caso["id"],
            abogado["id"] if abogado else None,
            extracted_json,
            case_data,
            nuevo_caso.get("tipo_caso"),
        )
    return nuevo_caso

@router.put("/api/casos/{caso_id}")
async def api_actualizar_caso(
    caso_id: int,
    data: CaseUpdate,
    request: Request,
    caso_existente=Depends(require_caso_access),
):
    notificar   = data.notificar_cliente
    update_data = data.dict(exclude_none=True, exclude={"notificar_cliente"})

    # Capturar correcciones del abogado respecto a la extracción IA original
    extracted_json = caso_existente.get("extracted_fields")
    if extracted_json:
        capturar_correcciones(
            caso_id,
            caso_existente.get("abogado_id"),
            extracted_json,
            update_data,
            caso_existente.get("tipo_caso"),
        )

    caso_actualizado = actualizar_caso(caso_id, update_data)

    notificacion_enviada = False
    if notificar and caso_actualizado:
        notificacion_enviada = await enviar_notificacion_whatsapp(caso_actualizado)

    return {**caso_actualizado, "_notificacion_enviada": notificacion_enviada}

@router.post("/api/casos/{caso_id}/notificar")
async def api_notificar_caso(caso_id: int, caso=Depends(require_caso_access)):
    """Envía notificación WhatsApp al cliente con el estado actual del caso."""
    enviado = await enviar_notificacion_whatsapp(caso)
    if not enviado:
        raise HTTPException(status_code=500, detail="No se pudo enviar la notificación. Verifica WHAPI_TOKEN.")
    return {"ok": True, "mensaje": f"Notificación enviada a {caso.get('nombre_cliente', 'cliente')}"}

@router.delete("/api/casos/{caso_id}")
def api_eliminar_caso(caso_id: int, caso=Depends(require_caso_access)):
    _doc_chunks_cache.pop(caso_id, None)
    eliminar_caso(caso_id)
    return {"ok": True, "mensaje": "Caso eliminado"}

# ─────────────────────────────────────────────
# Endpoints — Almacenamiento de documentos (R2)
# ─────────────────────────────────────────────

@router.post("/api/casos/{caso_id}/documento")
async def api_subir_documento(
    caso_id: int,
    archivo: UploadFile = File(...),
    caso=Depends(require_caso_access),
):
    """
    Sube el archivo original del caso a Cloudflare R2 y guarda la referencia en BD.
    Requiere variables de entorno: R2_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET_KEY, R2_BUCKET.
    """
    if not r2_configured():
        raise HTTPException(status_code=503, detail="El almacenamiento de documentos no está configurado. Contacta al administrador.")

    MAX_SIZE_MB = 10
    contenido = await archivo.read()
    if len(contenido) > MAX_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"El archivo supera los {MAX_SIZE_MB}MB permitidos.")

    ext = (archivo.filename or "").lower().rsplit(".", 1)[-1]
    if ext not in ("pdf", "doc", "docx"):
        raise HTTPException(status_code=415, detail="Solo se aceptan archivos PDF y DOCX.")

    content_type = archivo.content_type or "application/octet-stream"
    nombre = archivo.filename or f"documento.{ext}"

    try:
        key = upload_document(contenido, nombre, content_type, caso_id)
    except Exception as e:
        print(f"[Storage] ❌ Error al subir a R2: {e}")
        raise HTTPException(status_code=500, detail="No se pudo subir el documento. Intenta de nuevo.")

    caso_actualizado = actualizar_caso(caso_id, {
        "documento_url": key,
        "documento_nombre": nombre,
        "documento_tipo": content_type,
    })
    return caso_actualizado


@router.get("/api/casos/{caso_id}/documento")
def api_obtener_url_documento(caso_id: int, caso=Depends(require_caso_access)):
    """
    Genera una URL firmada temporal (1 hora) para descargar el documento del caso.
    """
    if not r2_configured():
        raise HTTPException(status_code=503, detail="El almacenamiento de documentos no está configurado.")

    key = caso.get("documento_url")
    if not key:
        raise HTTPException(status_code=404, detail="Este caso no tiene documento almacenado.")

    try:
        url = generate_presigned_url(key, expires_seconds=3600)
    except Exception as e:
        print(f"[Storage] ❌ Error al generar URL: {e}")
        raise HTTPException(status_code=500, detail="No se pudo generar el enlace de descarga.")

    return {
        "url": url,
        "nombre": caso.get("documento_nombre", "documento"),
        "tipo": caso.get("documento_tipo", ""),
    }


@router.delete("/api/casos/{caso_id}/documento")
def api_eliminar_documento(caso_id: int, caso=Depends(require_caso_access)):
    """
    Elimina el documento almacenado del caso (de R2 y de la BD).
    """
    if not r2_configured():
        raise HTTPException(status_code=503, detail="El almacenamiento de documentos no está configurado.")

    key = caso.get("documento_url")
    if not key:
        raise HTTPException(status_code=404, detail="Este caso no tiene documento almacenado.")

    delete_document(key)
    caso_actualizado = actualizar_caso(caso_id, {
        "documento_url": None,
        "documento_nombre": None,
        "documento_tipo": None,
    })
    return caso_actualizado

# ─────────────────────────────────────────────
# Endpoints — Multi-documentos por caso
# ─────────────────────────────────────────────

@router.get("/api/casos/{caso_id}/documentos")
def api_listar_documentos(caso_id: int, caso=Depends(require_caso_access)):
    """Lista todos los documentos subidos para un caso (solo metadata, sin texto)."""
    docs = listar_documentos_caso(caso_id)
    return [
        {
            "id": d["id"],
            "caso_id": d["caso_id"],
            "nombre": d["nombre"],
            "tipo_archivo": d["tipo_archivo"],
            "fecha_subida": d["fecha_subida"],
        }
        for d in docs
    ]


@router.post("/api/casos/{caso_id}/documentos")
async def api_subir_documento_caso(
    caso_id: int,
    archivo: UploadFile = File(...),
    caso=Depends(require_caso_access),
):
    """
    Sube un documento al caso:
    1. Sube el archivo original a R2
    2. Extrae resumen estructurado con Claude Haiku
    3. Comprime + cifra el resumen y texto relevante
    4. Guarda metadata en caso_documentos
    """
    if not r2_configured():
        raise HTTPException(status_code=503, detail="El almacenamiento de documentos no está configurado.")

    MAX_MB = 10
    contenido = await archivo.read()
    if len(contenido) > MAX_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"El archivo supera los {MAX_MB}MB permitidos.")

    ext = (archivo.filename or "").lower().rsplit(".", 1)[-1]
    if ext not in ("pdf", "doc", "docx"):
        raise HTTPException(status_code=415, detail="Solo se aceptan archivos PDF y DOCX.")

    content_type = archivo.content_type or "application/octet-stream"
    nombre = archivo.filename or f"documento.{ext}"

    # 1. Subir a R2
    try:
        key = upload_document(contenido, nombre, content_type, caso_id)
    except Exception as e:
        print(f"[Multi-doc] ❌ Error R2: {e}")
        raise HTTPException(status_code=500, detail="No se pudo subir el archivo.")

    # 2. Extracción inteligente con Claude Haiku
    try:
        resumen_dict, texto_relevante_str = extraer_resumen_estructurado(
            contenido, nombre, content_type
        )
        resumen_json_enc = compress_encrypt(json.dumps(resumen_dict, ensure_ascii=False))
        texto_enc = compress_encrypt(texto_relevante_str) if texto_relevante_str else ""
    except Exception as e:
        print(f"[Multi-doc] ⚠️ Error extracción: {e}")
        resumen_json_enc = ""
        texto_enc = ""

    # 3. Guardar en BD — si falla, limpiar R2
    try:
        doc = crear_documento_caso(
            caso_id=caso_id,
            nombre=nombre,
            tipo_archivo=content_type,
            key_r2=key,
            resumen_json=resumen_json_enc,
            texto_relevante=texto_enc,
        )
    except Exception as e:
        # BD falló — eliminar archivo huérfano de R2
        try:
            delete_document(key)
        except Exception:
            pass
        raise HTTPException(status_code=500, detail="Error al guardar el documento. Intenta de nuevo.")

    # Invalidar cache BM25 para este caso
    _doc_chunks_cache.pop(caso_id, None)

    return {
        "id": doc["id"],
        "caso_id": doc["caso_id"],
        "nombre": doc["nombre"],
        "tipo_archivo": doc["tipo_archivo"],
        "fecha_subida": doc["fecha_subida"],
    }


@router.get("/api/casos/{caso_id}/documentos/{doc_id}")
def api_obtener_url_doc(
    caso_id: int, doc_id: int, caso=Depends(require_caso_access)
):
    """Genera una URL firmada temporal (1 hora) para descargar un documento específico."""
    if not r2_configured():
        raise HTTPException(status_code=503, detail="El almacenamiento no está configurado.")
    doc = obtener_documento_caso(doc_id)
    if not doc or doc["caso_id"] != caso_id:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    try:
        url = generate_presigned_url(doc["key_r2"], expires_seconds=3600)
    except Exception as e:
        raise HTTPException(status_code=500, detail="No se pudo generar el enlace.")
    return {"url": url, "nombre": doc["nombre"], "tipo": doc["tipo_archivo"]}


@router.delete("/api/casos/{caso_id}/documentos/{doc_id}")
def api_eliminar_doc(
    caso_id: int, doc_id: int, caso=Depends(require_caso_access)
):
    """Elimina un documento de R2 y de la BD."""
    if not r2_configured():
        raise HTTPException(status_code=503, detail="El almacenamiento no está configurado.")
    doc = obtener_documento_caso(doc_id)
    if not doc or doc["caso_id"] != caso_id:
        raise HTTPException(status_code=404, detail="Documento no encontrado.")
    delete_document(doc["key_r2"])
    eliminar_documento_caso(doc_id)
    # Invalidar cache BM25 para este caso
    _doc_chunks_cache.pop(caso_id, None)
    return {"ok": True, "id": doc_id}


@router.post("/api/casos/{caso_id}/documentos/migrar-legacy")
def api_migrar_legacy(caso_id: int, caso=Depends(require_caso_access)):
    """
    Migra el documento legacy (campos documento_url/nombre/tipo/texto en casos)
    al nuevo sistema de multi-documentos (tabla caso_documentos).
    El archivo en R2 NO se mueve — solo se crea la referencia en BD.
    """
    key = caso.get("documento_url")
    if not key:
        raise HTTPException(status_code=404, detail="El caso no tiene documento legacy para migrar")

    # Evitar duplicados: no migrar si ya hay docs en el nuevo sistema
    docs_existentes = listar_documentos_caso(caso_id)
    if docs_existentes:
        raise HTTPException(
            status_code=409,
            detail="El caso ya tiene documentos en el nuevo sistema. Elimínalos primero si quieres re-migrar."
        )

    nombre = caso.get("documento_nombre") or "documento"
    tipo_archivo = caso.get("documento_tipo") or "application/octet-stream"

    # Cifrar el texto legacy si existe
    texto_relevante_enc = ""
    doc_texto = (caso.get("documento_texto") or "").strip()
    if doc_texto:
        try:
            texto_relevante_enc = compress_encrypt(doc_texto)
        except Exception:
            pass  # Si falla el cifrado, continuar sin texto

    # Crear registro en caso_documentos
    doc = crear_documento_caso(
        caso_id=caso_id,
        nombre=nombre,
        tipo_archivo=tipo_archivo,
        key_r2=key,
        resumen_json="",
        texto_relevante=texto_relevante_enc,
    )

    # Limpiar campos legacy del caso
    actualizar_caso(caso_id, {
        "documento_url": None,
        "documento_nombre": None,
        "documento_tipo": None,
        "documento_texto": None,
    })

    # Invalidar cache BM25
    _doc_chunks_cache.pop(caso_id, None)

    return {
        "id": doc["id"],
        "caso_id": doc["caso_id"],
        "nombre": doc["nombre"],
        "tipo_archivo": doc["tipo_archivo"],
        "fecha_subida": doc["fecha_subida"],
    }


# ─────────────────────────────────────────────
# Endpoint — Extracción de documento con Claude
# ─────────────────────────────────────────────

@router.post("/api/casos/extraer-documento")
async def api_extraer_documento(archivo: UploadFile = File(...), request: Request = None, user=Depends(require_auth)):
    """
    Recibe un PDF o DOCX, extrae los datos del caso usando Claude API.
    Devuelve los campos encontrados y la lista de campos requeridos faltantes.
    """
    MAX_SIZE_MB = 10
    contenido = await archivo.read()

    if len(contenido) > MAX_SIZE_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"El archivo supera los {MAX_SIZE_MB}MB permitidos.")

    ext = (archivo.filename or "").lower().rsplit(".", 1)[-1]
    if ext not in ("pdf", "doc", "docx", "jpg", "jpeg", "png", "webp"):
        raise HTTPException(
            status_code=415,
            detail="Formato no soportado. Solo se aceptan PDF, DOCX, JPG, PNG y WEBP."
        )

    try:
        resultado = extraer_datos_documento(
            contenido_bytes=contenido,
            nombre_archivo=archivo.filename or "documento",
            content_type=archivo.content_type or "",
        )
        return resultado
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        print(f"[Extracción] ❌ Error inesperado: {e}")
        raise HTTPException(status_code=500, detail="Error al procesar el documento con IA.")

# ─────────────────────────────────────────────
# Endpoint — Consejo procesal por caso
# ─────────────────────────────────────────────

@router.get("/api/casos/{caso_id}/consejo")
def api_consejo_procesal(caso_id: int, caso=Depends(require_caso_access)):
    """
    Dado un caso registrado, devuelve:
    - La siguiente etapa procesal
    - El plazo legal aplicable
    - La fecha límite sugerida
    - Los documentos que hay que preparar
    - La norma que lo sustenta
    """
    consejo = generar_consejo_procesal(caso)
    return consejo

# ─────────────────────────────────────────────
# Helpers — Contexto multi-documentos para chat
# ─────────────────────────────────────────────

def _chunk_text(text: str, chunk_size: int = 300, overlap: int = 30) -> list[str]:
    """Divide texto en chunks de chunk_size palabras con overlap."""
    words = text.split()
    if len(words) <= chunk_size:
        return [text]
    chunks = []
    step = chunk_size - overlap
    for i in range(0, len(words), step):
        chunk = " ".join(words[i:i + chunk_size])
        chunks.append(chunk)
        if i + chunk_size >= len(words):
            break
    return chunks


def _bm25_search(corpus: list[str], query: str, top_k: int = 3) -> list[str]:
    """BM25 sobre una lista de textos. Retorna los top_k más relevantes."""
    from rank_bm25 import BM25Okapi
    if not corpus or not query:
        return corpus[:top_k]
    tokenized = [doc.lower().split() for doc in corpus]
    bm25 = BM25Okapi(tokenized)
    scores = bm25.get_scores(query.lower().split())
    ranked = sorted(range(len(corpus)), key=lambda i: scores[i], reverse=True)
    return [corpus[i] for i in ranked[:top_k] if scores[i] > 0]


def _obtener_contexto_documentos(caso_id: int, pregunta: str) -> str:
    """
    Construye el contexto de documentos para el chat:
    - Resúmenes estructurados de todos los docs (siempre incluidos, compactos)
    - Fragmentos BM25-relevantes del texto de los docs (según la pregunta)
    Usa cache module-level para evitar descifrado/descompresión repetida.
    """
    documentos = listar_documentos_caso(caso_id)
    if not documentos:
        return ""

    doc_count = len(documentos)
    cached = _doc_chunks_cache.get(caso_id)

    summaries = []

    # Reutilizar chunks cacheados si el número de documentos no cambió
    if cached is not None and cached[0] == doc_count:
        all_chunks = cached[1]
        # Aún necesitamos construir los summaries (son baratos: solo leen resumen_json)
        for doc in documentos:
            nombre = doc.get("nombre", "documento")
            if doc.get("resumen_json"):
                try:
                    resumen = json.loads(decrypt_decompress(doc["resumen_json"]))
                    tipo = resumen.get("tipo_documento", "")
                    hechos = resumen.get("hechos_clave", "")
                    pretension = resumen.get("pretension", "")
                    partes = resumen.get("partes", {})
                    partes_str = ", ".join(f"{k}: {v}" for k, v in partes.items() if v)
                    pruebas = "; ".join(resumen.get("pruebas_evidencia", [])[:5])
                    fechas = "; ".join(
                        f"{f.get('fecha')} ({f.get('descripcion')})"
                        for f in resumen.get("fechas_importantes", [])[:3]
                    )
                    resolucion = resumen.get("resolucion_fallo") or ""
                    summary_lines = [f"[{nombre}] Tipo: {tipo}"]
                    if partes_str:
                        summary_lines.append(f"Partes: {partes_str}")
                    if hechos:
                        summary_lines.append(f"Hechos: {hechos[:500]}")
                    if pretension:
                        summary_lines.append(f"Pretensión: {pretension}")
                    if pruebas:
                        summary_lines.append(f"Pruebas: {pruebas}")
                    if fechas:
                        summary_lines.append(f"Fechas: {fechas}")
                    if resolucion:
                        summary_lines.append(f"Resolución: {resolucion[:300]}")
                    summaries.append("\n".join(summary_lines))
                except Exception:
                    summaries.append(f"[{nombre}]: documento adjunto")
    else:
        # Cache miss: descifrar/descomprimir todo y guardar chunks en cache
        all_chunks = []
        for doc in documentos:
            nombre = doc.get("nombre", "documento")

            # Resumen estructurado
            if doc.get("resumen_json"):
                try:
                    resumen = json.loads(decrypt_decompress(doc["resumen_json"]))
                    tipo = resumen.get("tipo_documento", "")
                    hechos = resumen.get("hechos_clave", "")
                    pretension = resumen.get("pretension", "")
                    partes = resumen.get("partes", {})
                    partes_str = ", ".join(f"{k}: {v}" for k, v in partes.items() if v)
                    pruebas = "; ".join(resumen.get("pruebas_evidencia", [])[:5])
                    fechas = "; ".join(
                        f"{f.get('fecha')} ({f.get('descripcion')})"
                        for f in resumen.get("fechas_importantes", [])[:3]
                    )
                    resolucion = resumen.get("resolucion_fallo") or ""
                    summary_lines = [f"[{nombre}] Tipo: {tipo}"]
                    if partes_str:
                        summary_lines.append(f"Partes: {partes_str}")
                    if hechos:
                        summary_lines.append(f"Hechos: {hechos[:500]}")
                    if pretension:
                        summary_lines.append(f"Pretensión: {pretension}")
                    if pruebas:
                        summary_lines.append(f"Pruebas: {pruebas}")
                    if fechas:
                        summary_lines.append(f"Fechas: {fechas}")
                    if resolucion:
                        summary_lines.append(f"Resolución: {resolucion[:300]}")
                    summaries.append("\n".join(summary_lines))
                except Exception:
                    summaries.append(f"[{nombre}]: documento adjunto")

            # Texto para BM25
            if doc.get("texto_relevante"):
                try:
                    texto = decrypt_decompress(doc["texto_relevante"])
                    chunks = _chunk_text(texto, chunk_size=250, overlap=25)
                    all_chunks.extend(chunks)
                except Exception:
                    pass

        # Guardar chunks en cache
        _doc_chunks_cache[caso_id] = (doc_count, all_chunks)

    # BM25 sobre chunks (siempre fresco: rápido con chunks ya en memoria)
    relevant_chunks = _bm25_search(all_chunks, pregunta, top_k=4) if all_chunks else []

    parts = []
    if summaries:
        parts.append("DOCUMENTOS DEL CASO:\n" + "\n\n".join(summaries))
    if relevant_chunks:
        parts.append("FRAGMENTOS RELEVANTES DE DOCUMENTOS:\n" + "\n---\n".join(relevant_chunks))

    return "\n\n".join(parts)


# ─────────────────────────────────────────────
# Endpoint — Chat con el caso (IA para el abogado)
# ─────────────────────────────────────────────

@router.post("/api/casos/{caso_id}/chat")
async def api_chat_caso(
    caso_id: int,
    data: ChatRequest,
    caso=Depends(require_caso_access),
):
    """
    El abogado hace una pregunta sobre el caso y Claude responde con contexto completo:
    - Datos del caso
    - Texto del documento subido (si existe)
    - Consejo procesal (siguiente etapa, plazos, documentos)
    - Normativa relevante (BM25)
    """
    from anthropic import AsyncAnthropic

    pregunta = data.pregunta.strip()
    if not pregunta:
        raise HTTPException(status_code=422, detail="La pregunta no puede estar vacía")

    # 1. Consejo procesal estructurado
    consejo = generar_consejo_procesal(caso)

    # 2. Normativa relevante (BM25 sobre la pregunta + tipo de caso)
    query_rag = f"{pregunta} {caso.get('tipo_caso', '')} {caso.get('notas', '')}"
    articulos = buscar_normativa(query_rag, top_k=5)
    bloque_normativa = formatear_para_prompt(articulos)

    # 3. Construir contexto del caso
    def val(campo: str) -> str:
        v = caso.get(campo) or ""
        return v.strip() if isinstance(v, str) else str(v)

    ctx_caso = f"""DATOS DEL CASO:
- Cliente: {val('nombre_cliente')}
- Expediente: {val('expediente') or 'No registrado'}
- Tipo: {val('tipo_caso')}
- Estado: {val('estado')}
- Próxima fecha: {val('proxima_fecha') or 'No definida'}
- Próxima acción: {val('proxima_accion') or 'No definida'}
- Documentos pendientes: {val('documentos_pendientes') or 'Ninguno'}
- Notas internas: {val('notas') or 'Sin notas'}"""

    # 4. Contexto de documentos (multi-doc con BM25)
    bloque_doc = _obtener_contexto_documentos(caso_id, pregunta)

    # Backward compat: si no hay docs nuevos pero hay documento_texto legacy
    if not bloque_doc:
        doc_texto = (caso.get("documento_texto") or "").strip()
        if doc_texto:
            truncado = doc_texto[:6000]
            if len(doc_texto) > 6000:
                truncado += "\n[... documento truncado ...]"
            bloque_doc = f"DOCUMENTO DEL CASO (texto extraído):\n{truncado}"

    # 5. Bloque de consejo procesal
    bloque_consejo = ""
    if consejo.get("tiene_consejo"):
        bloque_consejo = f"""
ESTADO PROCESAL ACTUAL:
- Proceso: {consejo.get('tipo_proceso', '')}
- Etapa actual: {consejo.get('etapa_actual', '')}
- Siguiente etapa: {consejo.get('siguiente_etapa', '')}
- Descripción: {consejo.get('siguiente_descripcion', '')}
- Plazo legal: {consejo.get('plazo_descripcion', '')}
- Fecha límite sugerida: {consejo.get('proxima_fecha_sugerida', 'No calculada')}
- Documentos a preparar: {', '.join(consejo.get('documentos_requeridos', [])) or 'No especificados'}
- Norma aplicable: {consejo.get('norma', '')}"""
        if consejo.get("advertencia"):
            bloque_consejo += f"\n- ⚠️ {consejo['advertencia']}"

    # 6. System prompt en dos partes para prompt caching
    static_system = CHAT_CASO_SYSTEM

    dynamic_context = f"""{ctx_caso}
{bloque_consejo}
{bloque_doc}
{bloque_normativa}"""

    # 7. Llamar a Claude con prompt caching en el contexto del caso
    anthropic_client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    import asyncio as _asyncio
    response = None
    ultimo_error = None
    for intento in range(3):
        try:
            response = await anthropic_client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=[
                    {"type": "text", "text": static_system},
                    {
                        "type": "text",
                        "text": dynamic_context,
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
                messages=[{"role": "user", "content": pregunta}],
            )
            break  # Éxito — salir del loop
        except HTTPException:
            raise  # No reintentar errores de validación
        except Exception as e:
            ultimo_error = e
            if intento < 2:
                await _asyncio.sleep(2 ** intento)  # 1s, 2s backoff
                continue
            raise HTTPException(
                status_code=500,
                detail="La IA no está disponible en este momento. Intenta en unos segundos."
            )

    if not response or not response.content:
        raise HTTPException(status_code=500, detail="La IA no devolvió respuesta")
    respuesta = response.content[0].text

    # Guardar historial
    guardar_mensaje_chat(caso_id, "user", data.pregunta)
    guardar_mensaje_chat(caso_id, "assistant", respuesta)

    return {"respuesta": respuesta}

@router.post("/api/casos/{caso_id}/agente")
async def api_agente_legal(
    caso_id: int,
    data: AgentRequest,
    caso=Depends(require_caso_access),
):
    """Agente Legal unificado: analizar, asesorar, redactar, normativa."""
    if data.accion not in ACCIONES_VALIDAS:
        raise HTTPException(status_code=400, detail=f"Acción no válida: {data.accion}")

    # Para "analizar" verificar que hay documentos (nuevo sistema o legacy)
    if data.accion == "analizar":
        docs = listar_documentos_caso(caso_id)
        tiene_legacy = bool(caso.get("documento_texto") or caso.get("documento_url"))
        if not docs and not tiene_legacy:
            raise HTTPException(status_code=422, detail="Este caso no tiene documentos. Sube un archivo primero.")

    try:
        resultado = await ejecutar_agente(caso_id, data.accion, data.parametros, caso)
        return resultado
    except RuntimeError as e:
        raise HTTPException(status_code=504, detail=str(e))


@router.get("/api/casos/{caso_id}/chat/historial")
async def api_chat_historial(caso_id: int, caso=Depends(require_caso_access)):
    """Retorna el historial de mensajes del chat de un caso."""
    mensajes = listar_mensajes_chat(caso_id)
    return {"mensajes": mensajes}


@router.delete("/api/casos/{caso_id}/chat/historial")
async def api_chat_limpiar(caso_id: int, caso=Depends(require_caso_access)):
    """Elimina el historial de chat de un caso."""
    limpiar_chat_caso(caso_id)
    return {"ok": True}


# ─────────────────────────────────────────────
# Endpoints API REST — Abogados
# ─────────────────────────────────────────────

class AbogadoCreate(BaseModel):
    nombre: str
    email: Optional[str] = None
    telefono: Optional[str] = None
    whatsapp_numero: Optional[str] = None
    colegiatura: Optional[str] = None
    especialidades: Optional[List[str]] = []
    estudio_id: Optional[int] = None
    activo: Optional[bool] = True

class AbogadoUpdate(BaseModel):
    nombre: Optional[str] = None
    email: Optional[str] = None
    telefono: Optional[str] = None
    whatsapp_numero: Optional[str] = None
    colegiatura: Optional[str] = None
    especialidades: Optional[List[str]] = None
    estudio_id: Optional[int] = None
    activo: Optional[bool] = None

def _es_admin(user: dict) -> bool:
    return (user or {}).get("rol") == "admin"


def _require_abogado_owner(abogado_id: int, user: dict) -> dict:
    """Lookup abogado y verifica que pertenezca al usuario autenticado.

    Multi-tenant guard: previene que el usuario A modifique los datos del
    usuario B sólo conociendo el abogado_id. Admin tiene acceso global.
    Lanza 404 si no existe (mismo código que ownership-mismatch para no
    revelar IDs ajenos).
    """
    abogado = obtener_abogado(abogado_id)
    if not abogado:
        raise HTTPException(status_code=404, detail="Abogado no encontrado")
    if not _es_admin(user):
        owner_email = (abogado.get("email") or "").lower()
        user_email = (user.get("email") or "").lower()
        if owner_email != user_email:
            raise HTTPException(status_code=404, detail="Abogado no encontrado")
    return abogado


@router.get("/api/abogados")
def api_listar_abogados(request: Request, solo_activos: bool = False, user=Depends(require_auth)):
    if _es_admin(user):
        return listar_abogados(solo_activos=solo_activos)
    return listar_abogados_por_email(user.get("email", ""), solo_activos=solo_activos)

@router.post("/api/abogados", status_code=201)
def api_crear_abogado(data: AbogadoCreate, request: Request, user=Depends(require_auth)):
    payload = data.dict()
    # Forzar el email al del usuario autenticado para que el abogado quede
    # ligado a la cuenta correcta. El admin sí puede crear abogados con
    # cualquier email (uso interno).
    if not _es_admin(user):
        payload["email"] = user.get("email", "")
    return crear_abogado(payload)

@router.get("/api/abogados/{abogado_id}")
def api_obtener_abogado(abogado_id: int, request: Request, user=Depends(require_auth)):
    return _require_abogado_owner(abogado_id, user)

@router.put("/api/abogados/{abogado_id}")
def api_actualizar_abogado(abogado_id: int, data: AbogadoUpdate, request: Request, user=Depends(require_auth)):
    _require_abogado_owner(abogado_id, user)
    payload = data.dict(exclude_none=True)
    # No permitir que un abogado cambie su email para "secuestrar" la cuenta
    # de otro usuario. Admin sí puede.
    if not _es_admin(user) and "email" in payload:
        payload.pop("email", None)
    return actualizar_abogado(abogado_id, payload)

@router.delete("/api/abogados/{abogado_id}")
def api_eliminar_abogado(abogado_id: int, request: Request, user=Depends(require_auth)):
    _require_abogado_owner(abogado_id, user)
    eliminar_abogado(abogado_id)
    return {"ok": True, "mensaje": "Abogado desactivado"}


# ─────────────────────────────────────────────
# Configuración del canal Whapi por abogado (multi-tenant)
# ─────────────────────────────────────────────

class WhapiConfig(BaseModel):
    whapi_token: str
    whatsapp_numero: Optional[str] = None  # opcional — se puede deducir del token


@router.post("/api/abogados/{abogado_id}/whapi/verificar")
async def api_verificar_whapi(abogado_id: int, data: WhapiConfig, request: Request, user=Depends(require_auth)):
    """Verifica que el token Whapi sea válido y devuelve el channel_id + número.

    No persiste todavía — el usuario revisa el resultado y luego confirma con POST .../whapi.
    """
    _require_abogado_owner(abogado_id, user)
    from agent.providers.whapi import verificar_token_whapi
    info = await verificar_token_whapi(data.whapi_token)
    if not info or not info.get("channel_id"):
        raise HTTPException(status_code=400, detail="Token Whapi inválido o canal no disponible (revisa en whapi.cloud)")
    return info


@router.post("/api/abogados/{abogado_id}/whapi")
async def api_guardar_whapi(abogado_id: int, data: WhapiConfig, request: Request, user=Depends(require_auth)):
    """Verifica el token y guarda la configuración Whapi del abogado.

    También retorna la URL de webhook que el usuario debe configurar en Whapi.
    """
    _require_abogado_owner(abogado_id, user)
    from agent.providers.whapi import verificar_token_whapi
    info = await verificar_token_whapi(data.whapi_token)
    if not info or not info.get("channel_id"):
        raise HTTPException(status_code=400, detail="Token Whapi inválido. Verifica el token en whapi.cloud")

    update_data = {
        "whapi_token":      data.whapi_token,
        "whapi_channel_id": info["channel_id"],
    }
    if data.whatsapp_numero:
        update_data["whatsapp_numero"] = data.whatsapp_numero
    elif info.get("phone"):
        update_data["whatsapp_numero"] = info["phone"]

    abogado_actualizado = actualizar_abogado(abogado_id, update_data)

    base_url = os.getenv("PUBLIC_API_URL", "https://katia-jorkat-production.up.railway.app").rstrip("/")
    webhook_url = f"{base_url}/webhook/abogado/{abogado_id}"

    return {
        "ok": True,
        "abogado": abogado_actualizado,
        "channel_info": info,
        "webhook_url": webhook_url,
        "instrucciones": (
            "Copia esta URL de webhook a Whapi: dashboard del canal → Settings → Webhooks → "
            "pega la URL en 'Endpoint' y activa el evento 'messages.post'."
        ),
    }


@router.delete("/api/abogados/{abogado_id}/whapi")
def api_desconectar_whapi(abogado_id: int, request: Request, user=Depends(require_auth)):
    """Desconecta el canal Whapi del abogado (limpia token y channel_id)."""
    _require_abogado_owner(abogado_id, user)
    actualizar_abogado(abogado_id, {"whapi_token": None, "whapi_channel_id": None})
    return {"ok": True, "mensaje": "Canal Whapi desconectado"}


@router.post("/api/abogados/{abogado_id}/whapi/refresh")
async def api_refrescar_whapi(abogado_id: int, request: Request, user=Depends(require_auth)):
    """Re-verifica el canal Whapi usando el token guardado y actualiza phone/channel_id.

    Caso de uso: cuando el abogado re-pairea su canal con un teléfono distinto,
    el token y channel_id NO cambian, pero el phone vinculado sí. Esta operación
    actualiza Minka para reflejar el nuevo phone sin pedir al abogado que pegue
    el token de nuevo.
    """
    abogado = _require_abogado_owner(abogado_id, user)
    token = abogado.get("whapi_token")
    if not token:
        raise HTTPException(status_code=400, detail="No hay canal Whapi conectado. Conecta uno primero.")

    from agent.providers.whapi import verificar_token_whapi
    info = await verificar_token_whapi(token)
    if not info or not info.get("channel_id"):
        raise HTTPException(status_code=400, detail="El canal Whapi no responde. Verifica que esté activo en whapi.cloud.")

    update_data = {"whapi_channel_id": info["channel_id"]}
    if info.get("phone"):
        update_data["whatsapp_numero"] = info["phone"]
    abogado_actualizado = actualizar_abogado(abogado_id, update_data)

    return {
        "ok": True,
        "abogado": abogado_actualizado,
        "channel_info": info,
        "phone_anterior": abogado.get("whatsapp_numero"),
        "phone_actualizado": info.get("phone"),
        "cambio_detectado": abogado.get("whatsapp_numero") != info.get("phone"),
    }

# ─────────────────────────────────────────────
# Endpoints API REST — Estudios Jurídicos
# ─────────────────────────────────────────────

class EstudioCreate(BaseModel):
    nombre: str
    ruc: Optional[str] = None
    direccion: Optional[str] = None
    plan: Optional[str] = "starter"

class EstudioUpdate(BaseModel):
    nombre: Optional[str] = None
    ruc: Optional[str] = None
    direccion: Optional[str] = None
    plan: Optional[str] = None

@router.get("/api/estudios")
def api_listar_estudios(request: Request, user=Depends(require_auth)):
    return listar_estudios()

@router.post("/api/estudios", status_code=201)
def api_crear_estudio(data: EstudioCreate, request: Request, user=Depends(require_auth)):
    return crear_estudio(data.dict())

@router.get("/api/estudios/{estudio_id}")
def api_obtener_estudio(estudio_id: int, request: Request, user=Depends(require_auth)):
    estudio = obtener_estudio(estudio_id)
    if not estudio:
        raise HTTPException(status_code=404, detail="Estudio no encontrado")
    return estudio

@router.put("/api/estudios/{estudio_id}")
def api_actualizar_estudio(estudio_id: int, data: EstudioUpdate, request: Request, user=Depends(require_auth)):
    if not obtener_estudio(estudio_id):
        raise HTTPException(status_code=404, detail="Estudio no encontrado")
    return actualizar_estudio(estudio_id, data.dict(exclude_none=True))

# ─────────────────────────────────────────────
# Modelos y Endpoints — Calendario de Eventos
# ─────────────────────────────────────────────

class EventoCreate(BaseModel):
    titulo: str
    fecha_hora: str
    tipo: Optional[str] = "audiencia"
    caso_id: Optional[int] = None
    abogado_id: Optional[int] = None
    recordatorio_dias: Optional[int] = 1
    notas: Optional[str] = None

class EventoUpdate(BaseModel):
    titulo: Optional[str] = None
    fecha_hora: Optional[str] = None
    tipo: Optional[str] = None
    caso_id: Optional[int] = None
    abogado_id: Optional[int] = None
    recordatorio_dias: Optional[int] = None
    notas: Optional[str] = None

@router.get("/api/eventos")
def api_listar_eventos(
    request: Request,
    fecha_desde: Optional[str] = None,
    fecha_hasta: Optional[str] = None,
    user=Depends(require_auth),
):
    return listar_eventos(fecha_desde=fecha_desde, fecha_hasta=fecha_hasta)

@router.post("/api/eventos", status_code=201)
def api_crear_evento(data: EventoCreate, request: Request, user=Depends(require_auth)):
    return crear_evento(data.dict())

@router.get("/api/eventos/{evento_id}")
def api_obtener_evento(evento_id: int, request: Request, user=Depends(require_auth)):
    evento = obtener_evento(evento_id)
    if not evento:
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    return evento

@router.put("/api/eventos/{evento_id}")
def api_actualizar_evento(evento_id: int, data: EventoUpdate, request: Request, user=Depends(require_auth)):
    if not obtener_evento(evento_id):
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    return actualizar_evento(evento_id, data.dict(exclude_none=True))

@router.delete("/api/eventos/{evento_id}")
def api_eliminar_evento(evento_id: int, request: Request, user=Depends(require_auth)):
    if not obtener_evento(evento_id):
        raise HTTPException(status_code=404, detail="Evento no encontrado")
    eliminar_evento(evento_id)
    return {"ok": True, "mensaje": "Evento eliminado"}

# ─────────────────────────────────────────────
# Endpoints — Calculadora de Plazos
# ─────────────────────────────────────────────

class CalcularPlazoRequest(BaseModel):
    fecha_inicio: str           # YYYY-MM-DD
    dias: int                   # número de días del plazo
    tipo: str = "habiles"       # "habiles" | "calendario"

@router.post("/api/calcular-plazo")
def api_calcular_plazo(data: CalcularPlazoRequest, request: Request, user=Depends(require_auth)):
    from agent.deadline_calculator import calcular_plazo_completo
    return calcular_plazo_completo(data.fecha_inicio, data.dias, data.tipo)

@router.get("/api/feriados")
def api_feriados(
    request: Request,
    anio: Optional[int] = None,
    user=Depends(require_auth),
):
    from agent.deadline_calculator import cargar_feriados, get_nombre_feriado
    fechas = sorted(cargar_feriados())
    if anio:
        fechas = [f for f in fechas if f.startswith(str(anio))]
    feriados = [{"fecha": f, "nombre": get_nombre_feriado(f)} for f in fechas]
    return {"feriados": feriados, "total": len(feriados)}

# ─────────────────────────────────────────────
# Búsqueda de normativa (RAG)
# ─────────────────────────────────────────────

class NormativaRequest(BaseModel):
    query: str
    codigos: Optional[List[str]] = None
    top_k: int = 5


# ─────────────────────────────────────────────
# Feedback loop — correcciones del abogado
# ─────────────────────────────────────────────

@router.get("/api/corrections")
def api_listar_corrections(
    request: Request,
    campo: Optional[str] = None,
    tipo_caso: Optional[str] = None,
    limit: int = 200,
    user=Depends(require_auth),
):
    """Lista correcciones del abogado autenticado respecto a la extracción IA.

    Solo retorna correcciones de casos que pertenecen al abogado autenticado (multi-tenant).
    """
    import sqlite3 as _sqlite
    abogado_id = user.get("id") if user else None
    conn = _sqlite.connect(DB_PATH)
    conn.row_factory = _sqlite.Row
    cursor = conn.cursor()
    conditions, params = ["c.abogado_id = ?"], [abogado_id]
    if campo:
        conditions.append("c.campo = ?"); params.append(campo)
    if tipo_caso:
        conditions.append("c.tipo_caso = ?"); params.append(tipo_caso)
    where = f"WHERE {' AND '.join(conditions)}"
    cursor.execute(f"""
        SELECT c.*, ca.nombre_cliente, ca.expediente
        FROM corrections c
        LEFT JOIN casos ca ON c.caso_id = ca.id
        {where}
        ORDER BY c.created_at DESC
        LIMIT ?
    """, params + [limit])
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/api/corrections/stats")
def api_corrections_stats(request: Request, user=Depends(require_auth)):
    """Estadísticas agrupadas de correcciones del abogado autenticado."""
    import sqlite3 as _sqlite
    abogado_id = user.get("id") if user else None
    conn = _sqlite.connect(DB_PATH)
    conn.row_factory = _sqlite.Row
    cursor = conn.cursor()
    cursor.execute("""
        SELECT campo, tipo_caso,
               COUNT(*) as total,
               SUM(CASE WHEN valor_claude IS NULL THEN 1 ELSE 0 END) as adiciones,
               SUM(CASE WHEN valor_claude IS NOT NULL THEN 1 ELSE 0 END) as correcciones
        FROM corrections
        WHERE abogado_id = ?
        GROUP BY campo, tipo_caso
        ORDER BY total DESC
    """, (abogado_id,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.post("/api/normativa/buscar")
def api_buscar_normativa(data: NormativaRequest, request: Request, user=Depends(require_auth)):
    """Busca artículos legales relevantes usando BM25 sobre la base de normativa."""
    if not data.query or not data.query.strip():
        raise HTTPException(status_code=422, detail="El campo 'query' es obligatorio")

    resultados = buscar_normativa(
        query=data.query.strip(),
        codigos=data.codigos,
        top_k=min(data.top_k, 10),
    )

    return {
        "articulos": resultados,
        "total": len(resultados),
        "query": data.query.strip(),
    }


# ─────────────────────────────────────────────
# Admin diagnostics
# ─────────────────────────────────────────────

@router.get("/api/admin/diagnostico-whapi")
def api_diagnostico_whapi(user=Depends(require_auth)):
    """Solo admin. Snapshot de la tabla abogados para detectar split-brain de
    identidad multi-tenant que rompe el routing del webhook Whapi.

    Devuelve:
    - emails_duplicados: filas de abogados con el mismo email (debería ser []).
    - channels_duplicados: filas con el mismo whapi_channel_id (debería ser []).
    - schema_email_unique: si la columna email tiene constraint UNIQUE en la BD real.
    - abogados: snapshot completo (sin token, solo flag tiene_token).
    """
    if not _es_admin(user):
        raise HTTPException(status_code=403, detail="Solo admin")

    import sqlite3
    from agent.lawyers_db import DB_PATH

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("""
        SELECT email, COUNT(*) as cantidad FROM abogados
        GROUP BY email HAVING COUNT(*) > 1
    """)
    emails_duplicados = [dict(r) for r in cursor.fetchall()]

    cursor.execute("""
        SELECT whapi_channel_id, COUNT(*) as cantidad FROM abogados
        WHERE whapi_channel_id IS NOT NULL AND whapi_channel_id != ''
        GROUP BY whapi_channel_id HAVING COUNT(*) > 1
    """)
    channels_duplicados = [dict(r) for r in cursor.fetchall()]

    # Detectar si email tiene UNIQUE en la BD real (CREATE TABLE IF NOT EXISTS
    # no aplica UNIQUE si la tabla ya existía sin el constraint).
    cursor.execute("PRAGMA index_list(abogados)")
    indices = [dict(r) for r in cursor.fetchall()]
    schema_email_unique = False
    for idx in indices:
        cursor.execute(f"PRAGMA index_info({idx['name']!r})")
        cols = [r["name"] for r in cursor.fetchall()]
        if cols == ["email"] and idx.get("unique") == 1:
            schema_email_unique = True
            break

    cursor.execute("""
        SELECT id, email, nombre, whatsapp_numero, whapi_channel_id, modo_atencion,
               CASE WHEN whapi_token IS NOT NULL AND whapi_token != ''
                    THEN 1 ELSE 0 END as tiene_token,
               activo, fecha_creacion
        FROM abogados ORDER BY id
    """)
    abogados = [dict(r) for r in cursor.fetchall()]

    conn.close()

    return {
        "emails_duplicados": emails_duplicados,
        "channels_duplicados": channels_duplicados,
        "schema_email_unique": schema_email_unique,
        "schema_indices": indices,
        "abogados": abogados,
        "total_abogados": len(abogados),
    }


@router.get("/api/admin/health-check")
def api_health_check(user=Depends(require_auth)):
    """Solo admin. Smoke test de servicios y dependencias del backend.
    Verifica BD, corpus legal, Whapi, Anthropic, R2, y devuelve counts.
    Útil para validar el estado del deploy sin ejecutar requests externos."""
    if not _es_admin(user):
        raise HTTPException(status_code=403, detail="Solo admin")

    import sqlite3
    from agent.lawyers_db import DB_PATH as LAWYERS_DB

    health: dict = {"ok": True, "checks": {}, "counts": {}}

    # BD: contar filas de las tablas principales
    try:
        conn = sqlite3.connect(LAWYERS_DB)
        cursor = conn.cursor()
        for tabla in ["usuarios", "abogados", "estudios", "casos",
                      "caso_documentos", "chat_mensajes", "eventos_calendario",
                      "corrections", "mensajes"]:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {tabla}")
                health["counts"][tabla] = cursor.fetchone()[0]
            except sqlite3.OperationalError:
                health["counts"][tabla] = "no existe"
        conn.close()
        health["checks"]["sqlite"] = "ok"
    except Exception as e:
        health["checks"]["sqlite"] = f"error: {e}"
        health["ok"] = False

    # Corpus legal (BM25)
    try:
        from agent.rag import buscar_normativa
        articulos = buscar_normativa("contrato", top_k=1)
        health["checks"]["corpus_bm25"] = (
            f"ok ({len(articulos)} resultado/s en query de prueba)"
            if articulos else "vacío o no cargado"
        )
    except Exception as e:
        health["checks"]["corpus_bm25"] = f"error: {e}"
        health["ok"] = False

    # Anthropic API key
    health["checks"]["anthropic_key"] = (
        "set" if os.getenv("ANTHROPIC_API_KEY") else "MISSING"
    )
    if not os.getenv("ANTHROPIC_API_KEY"):
        health["ok"] = False

    # R2 (Cloudflare)
    r2_keys = ["R2_ACCOUNT_ID", "R2_ACCESS_KEY", "R2_SECRET_KEY", "R2_BUCKET"]
    r2_set = [k for k in r2_keys if os.getenv(k)]
    health["checks"]["r2"] = f"{len(r2_set)}/{len(r2_keys)} env vars set"
    if len(r2_set) < len(r2_keys):
        health["ok"] = False

    # JWT secret
    health["checks"]["jwt_secret"] = (
        "set" if os.getenv("JWT_SECRET_KEY") else "MISSING"
    )
    if not os.getenv("JWT_SECRET_KEY"):
        health["ok"] = False

    # WHAPI_WEBHOOK_TOKEN (opcional pero recomendado)
    health["checks"]["whapi_webhook_token"] = (
        "set" if os.getenv("WHAPI_WEBHOOK_TOKEN") else "no configurado (warning)"
    )

    # Email del admin actual
    health["admin_email"] = user.get("email")

    return health


@router.post("/api/admin/reset-data-prueba")
def api_reset_data_prueba(user=Depends(require_auth)):
    """⚠️ DESTRUCTIVO. Solo admin. Borra TODA la data de prueba: casos,
    documentos, eventos, mensajes, conversaciones, corrections. Borra todos
    los abogados, estudios y usuarios EXCEPTO el admin que ejecuta esto.
    Limpia el Whapi configurado del admin (token, channel_id, número) para
    que reconecte desde cero. NO toca el corpus legal.

    Devuelve un dict con qué borró por tabla.

    Safety: requiere rol=admin Y exige que el admin esté presente en la BD.
    """
    if not _es_admin(user):
        raise HTTPException(status_code=403, detail="Solo admin")

    admin_email = (user.get("email") or "").lower()
    if not admin_email:
        raise HTTPException(status_code=400, detail="JWT sin email — no es seguro borrar")

    import sqlite3
    from agent.lawyers_db import DB_PATH as LAWYERS_DB

    conn = sqlite3.connect(LAWYERS_DB)
    cursor = conn.cursor()

    # Encontrar el ID del admin antes de borrar nada
    cursor.execute("SELECT id FROM usuarios WHERE LOWER(email) = ? AND activo = 1", (admin_email,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        raise HTTPException(
            status_code=400,
            detail=f"Admin con email {admin_email} no encontrado en usuarios. Aborto."
        )
    admin_user_id = row[0]

    cursor.execute("SELECT id, estudio_id FROM abogados WHERE LOWER(email) = ?", (admin_email,))
    row = cursor.fetchone()
    admin_abogado_id = row[0] if row else None
    admin_estudio_id = row[1] if row else None

    borrados = {}

    def _delete(sql: str, params: tuple = (), label: str = ""):
        try:
            cursor.execute(sql, params)
            borrados[label] = cursor.rowcount
        except sqlite3.OperationalError as e:
            borrados[label] = f"error: {e}"

    # Orden: hijos primero (FKs)
    _delete("DELETE FROM caso_documentos", (), "caso_documentos")
    _delete("DELETE FROM chat_mensajes", (), "chat_mensajes")
    _delete("DELETE FROM corrections", (), "corrections")
    _delete("DELETE FROM casos", (), "casos")
    _delete("DELETE FROM eventos_calendario", (), "eventos_calendario")
    _delete("DELETE FROM mensajes", (), "mensajes_legacy_wa")  # SQLAlchemy async memory

    if admin_abogado_id:
        _delete("DELETE FROM abogados WHERE id != ?", (admin_abogado_id,), "abogados")
    else:
        _delete("DELETE FROM abogados", (), "abogados")

    if admin_estudio_id:
        _delete("DELETE FROM estudios WHERE id != ?", (admin_estudio_id,), "estudios")
    else:
        _delete("DELETE FROM estudios", (), "estudios")

    _delete("DELETE FROM usuarios WHERE id != ?", (admin_user_id,), "usuarios")

    # Limpiar Whapi del admin para reconexión limpia
    if admin_abogado_id:
        try:
            cursor.execute(
                "UPDATE abogados SET whapi_token = NULL, whapi_channel_id = NULL, "
                "whatsapp_numero = NULL WHERE id = ?",
                (admin_abogado_id,),
            )
            borrados["whapi_admin_reseteado"] = cursor.rowcount
        except sqlite3.OperationalError as e:
            borrados["whapi_admin_reseteado"] = f"error: {e}"

    conn.commit()
    conn.close()

    return {
        "ok": True,
        "preservado": {
            "admin_user_id": admin_user_id,
            "admin_email": admin_email,
            "admin_abogado_id": admin_abogado_id,
            "admin_estudio_id": admin_estudio_id,
        },
        "borrados": borrados,
        "siguiente_paso": (
            "Logueate de nuevo en el frontend, andá a Configuración → "
            "Integración WhatsApp → reconectá tu canal Whapi (esta vez se va "
            "a guardar el channel_id correcto). Después pegá la URL nueva "
            "del webhook en whapi.cloud."
        ),
    }


# ─────────────────────────────────────────────
# Dashboard (sirve dashboard.html)
# ─────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse("<h1>Dashboard no encontrado</h1>", status_code=404)
