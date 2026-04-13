import os
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
)
from agent.lawyers_db import (
    listar_abogados,
    obtener_abogado,
    crear_abogado,
    actualizar_abogado,
    eliminar_abogado,
    listar_estudios,
    obtener_estudio,
    crear_estudio,
    actualizar_estudio,
)
from agent.document_extractor import extraer_datos_documento
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

router = APIRouter()

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
    if not WHAPI_TOKEN:
        print("[Notificación] WHAPI_TOKEN no configurado, omitiendo.")
        return False

    telefono_wa  = _tel_whatsapp(caso.get("telefono", ""))
    nombre       = caso.get("nombre_cliente", "cliente")
    estado       = caso.get("estado", "")
    estado_label = ESTADOS_LABELS.get(estado, estado)
    expediente   = caso.get("expediente") or ""
    proxima_fecha   = caso.get("proxima_fecha") or ""
    proxima_accion  = caso.get("proxima_accion") or ""
    documentos      = caso.get("documentos_pendientes") or ""

    lineas = [
        f"👋 Hola {nombre}, le escribimos del estudio jurídico.",
        "",
        "*Su caso ha sido actualizado:*",
        f"📁 Expediente: {expediente}" if expediente else None,
        f"📊 Estado: {estado_label}",
    ]
    if proxima_fecha:
        lineas.append(f"📅 Próxima fecha: {proxima_fecha}")
    if proxima_accion:
        lineas.append(f"▶️ Próxima acción: {proxima_accion}")
    if documentos:
        lineas.append(f"📎 Documentos pendientes: {documentos}")
    lineas += ["", "Si tiene consultas, puede escribirme aquí mismo. 🤖 _Minka_"]

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
                    "Authorization": f"Bearer {WHAPI_TOKEN}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            ok = response.status_code in (200, 201)
            print(f"[Notificación] {'✅' if ok else '❌'} {response.status_code} → {telefono_wa}")
            return ok
    except Exception as e:
        print(f"[Notificación] ❌ Excepción: {e}")
        return False

# ─────────────────────────────────────────────
# Endpoints API REST — Casos
# ─────────────────────────────────────────────

@router.get("/api/casos")
def api_listar_casos(request: Request, estado: Optional[str] = None, buscar: Optional[str] = None, user=Depends(require_auth)):
    casos = listar_casos(filtro_estado=estado)
    if buscar:
        q = buscar.lower()
        casos = [c for c in casos if
                 q in (c.get("nombre_cliente") or "").lower() or
                 q in (c.get("expediente") or "").lower() or
                 q in (c.get("telefono") or "").lower()]
    return casos

@router.get("/api/casos/stats")
def api_stats(request: Request, user=Depends(require_auth)):
    casos = listar_casos()
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
def api_obtener_caso(caso_id: int, request: Request, user=Depends(require_auth)):
    caso = obtener_caso(caso_id)
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    return caso

@router.post("/api/casos", status_code=201)
def api_crear_caso(data: CaseCreate, request: Request, user=Depends(require_auth)):
    return crear_caso(data.dict())

@router.put("/api/casos/{caso_id}")
async def api_actualizar_caso(caso_id: int, data: CaseUpdate, request: Request, user=Depends(require_auth)):
    caso_existente = obtener_caso(caso_id)
    if not caso_existente:
        raise HTTPException(status_code=404, detail="Caso no encontrado")

    notificar   = data.notificar_cliente
    update_data = data.dict(exclude_none=True, exclude={"notificar_cliente"})
    caso_actualizado = actualizar_caso(caso_id, update_data)

    notificacion_enviada = False
    if notificar and caso_actualizado:
        notificacion_enviada = await enviar_notificacion_whatsapp(caso_actualizado)

    return {**caso_actualizado, "_notificacion_enviada": notificacion_enviada}

@router.post("/api/casos/{caso_id}/notificar")
async def api_notificar_caso(caso_id: int, request: Request, user=Depends(require_auth)):
    """Envía notificación WhatsApp al cliente con el estado actual del caso."""
    caso = obtener_caso(caso_id)
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    enviado = await enviar_notificacion_whatsapp(caso)
    if not enviado:
        raise HTTPException(status_code=500, detail="No se pudo enviar la notificación. Verifica WHAPI_TOKEN.")
    return {"ok": True, "mensaje": f"Notificación enviada a {caso.get('nombre_cliente', 'cliente')}"}

@router.delete("/api/casos/{caso_id}")
def api_eliminar_caso(caso_id: int, request: Request, user=Depends(require_auth)):
    caso = obtener_caso(caso_id)
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    eliminar_caso(caso_id)
    return {"ok": True, "mensaje": "Caso eliminado"}

# ─────────────────────────────────────────────
# Endpoints — Almacenamiento de documentos (R2)
# ─────────────────────────────────────────────

@router.post("/api/casos/{caso_id}/documento")
async def api_subir_documento(caso_id: int, archivo: UploadFile = File(...), request: Request = None, user=Depends(require_auth)):
    """
    Sube el archivo original del caso a Cloudflare R2 y guarda la referencia en BD.
    Requiere variables de entorno: R2_ACCOUNT_ID, R2_ACCESS_KEY, R2_SECRET_KEY, R2_BUCKET.
    """
    if not r2_configured():
        raise HTTPException(status_code=503, detail="El almacenamiento de documentos no está configurado. Contacta al administrador.")

    caso = obtener_caso(caso_id)
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")

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
def api_obtener_url_documento(caso_id: int, request: Request, user=Depends(require_auth)):
    """
    Genera una URL firmada temporal (1 hora) para descargar el documento del caso.
    """
    if not r2_configured():
        raise HTTPException(status_code=503, detail="El almacenamiento de documentos no está configurado.")

    caso = obtener_caso(caso_id)
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")

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
def api_eliminar_documento(caso_id: int, request: Request, user=Depends(require_auth)):
    """
    Elimina el documento almacenado del caso (de R2 y de la BD).
    """
    if not r2_configured():
        raise HTTPException(status_code=503, detail="El almacenamiento de documentos no está configurado.")

    caso = obtener_caso(caso_id)
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")

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
    if ext not in ("pdf", "doc", "docx"):
        raise HTTPException(
            status_code=415,
            detail="Formato no soportado. Solo se aceptan archivos PDF y DOCX."
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
def api_consejo_procesal(caso_id: int, request: Request, user=Depends(require_auth)):
    """
    Dado un caso registrado, devuelve:
    - La siguiente etapa procesal
    - El plazo legal aplicable
    - La fecha límite sugerida
    - Los documentos que hay que preparar
    - La norma que lo sustenta
    """
    caso = obtener_caso(caso_id)
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")

    consejo = generar_consejo_procesal(caso)
    return consejo

# ─────────────────────────────────────────────
# Endpoint — Chat con el caso (IA para el abogado)
# ─────────────────────────────────────────────

@router.post("/api/casos/{caso_id}/chat")
async def api_chat_caso(caso_id: int, data: ChatRequest, request: Request, user=Depends(require_auth)):
    """
    El abogado hace una pregunta sobre el caso y Claude responde con contexto completo:
    - Datos del caso
    - Texto del documento subido (si existe)
    - Consejo procesal (siguiente etapa, plazos, documentos)
    - Normativa relevante (BM25)
    """
    import os
    from anthropic import AsyncAnthropic

    caso = obtener_caso(caso_id)
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")

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

    # 4. Texto del documento (truncado a 6000 chars para no saturar el contexto)
    doc_texto = val("documento_texto")
    bloque_doc = ""
    if doc_texto:
        truncado = doc_texto[:6000]
        if len(doc_texto) > 6000:
            truncado += "\n[... documento truncado ...]"
        bloque_doc = f"\nDOCUMENTO DEL CASO (texto extraído):\n{truncado}"

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

    # 6. System prompt final
    system_prompt = f"""Eres Minka, asistente de IA para abogados peruanos. Tu función es responder preguntas del abogado sobre su caso de forma precisa, práctica y fundamentada en el derecho peruano.

{ctx_caso}
{bloque_consejo}
{bloque_doc}
{bloque_normativa}

Instrucciones de formato (MUY IMPORTANTE):
- Responde en texto plano, sin markdown de ningún tipo
- Prohibido usar #, ##, **, *, --, ---, |, >, emojis ni símbolos decorativos
- Usa párrafos separados por línea en blanco para organizar la respuesta
- Si necesitas enumerar, usa números simples: 1. 2. 3.
- Sé conciso y directo, sin introducciones largas ni resúmenes al final
- Cita artículos legales en texto plano: "Art. 196 del CP" o "Art. 334 del CPP"
- Si hay advertencia de plazo vencido, mencionarla al inicio
- No inventes información que no esté en el contexto"""

    # 7. Llamar a Claude
    anthropic_client = AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    try:
        response = await anthropic_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": pregunta}],
        )
        respuesta = response.content[0].text
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al consultar IA: {str(e)}")

    return {"respuesta": respuesta}

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

@router.get("/api/abogados")
def api_listar_abogados(request: Request, solo_activos: bool = False, user=Depends(require_auth)):
    return listar_abogados(solo_activos=solo_activos)

@router.post("/api/abogados", status_code=201)
def api_crear_abogado(data: AbogadoCreate, request: Request, user=Depends(require_auth)):
    return crear_abogado(data.dict())

@router.get("/api/abogados/{abogado_id}")
def api_obtener_abogado(abogado_id: int, request: Request, user=Depends(require_auth)):
    abogado = obtener_abogado(abogado_id)
    if not abogado:
        raise HTTPException(status_code=404, detail="Abogado no encontrado")
    return abogado

@router.put("/api/abogados/{abogado_id}")
def api_actualizar_abogado(abogado_id: int, data: AbogadoUpdate, request: Request, user=Depends(require_auth)):
    abogado = obtener_abogado(abogado_id)
    if not abogado:
        raise HTTPException(status_code=404, detail="Abogado no encontrado")
    return actualizar_abogado(abogado_id, data.dict(exclude_none=True))

@router.delete("/api/abogados/{abogado_id}")
def api_eliminar_abogado(abogado_id: int, request: Request, user=Depends(require_auth)):
    if not obtener_abogado(abogado_id):
        raise HTTPException(status_code=404, detail="Abogado no encontrado")
    eliminar_abogado(abogado_id)
    return {"ok": True, "mensaje": "Abogado desactivado"}

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
    fecha_inicio: str   # YYYY-MM-DD
    plazo_dias: int

@router.post("/api/calcular-plazo")
def api_calcular_plazo(data: CalcularPlazoRequest, request: Request, user=Depends(require_auth)):
    fecha_vencimiento = calcular_vencimiento(data.fecha_inicio, data.plazo_dias)
    restantes = dias_restantes_habiles(fecha_vencimiento)
    return {
        "fecha_inicio": data.fecha_inicio,
        "plazo_dias": data.plazo_dias,
        "fecha_vencimiento": fecha_vencimiento,
        "dias_restantes": restantes,
    }

@router.get("/api/feriados")
def api_feriados(
    request: Request,
    anio: Optional[int] = None,
    user=Depends(require_auth),
):
    from agent.deadline_calculator import cargar_feriados
    feriados = sorted(cargar_feriados())
    if anio:
        feriados = [f for f in feriados if f.startswith(str(anio))]
    return {"feriados": feriados, "total": len(feriados)}

# ─────────────────────────────────────────────
# Búsqueda de normativa (RAG)
# ─────────────────────────────────────────────

class NormativaRequest(BaseModel):
    query: str
    codigos: Optional[List[str]] = None
    top_k: int = 5


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
# Dashboard (sirve dashboard.html)
# ─────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse("<h1>Dashboard no encontrado</h1>", status_code=404)
