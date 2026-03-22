import os
import httpx
from fastapi import APIRouter, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional
from agent.cases_db import (
    listar_casos,
    obtener_caso,
    crear_caso,
    actualizar_caso,
    eliminar_caso,
)
from agent.document_extractor import extraer_datos_documento

router = APIRouter()

WHAPI_TOKEN = os.getenv("WHAPI_TOKEN")
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

class CaseUpdate(BaseModel):
    nombre_cliente: Optional[str] = None
    expediente: Optional[str] = None
    tipo_caso: Optional[str] = None
    estado: Optional[str] = None
    proxima_fecha: Optional[str] = None
    proxima_accion: Optional[str] = None
    documentos_pendientes: Optional[str] = None
    notas: Optional[str] = None
    abogado_asignado: Optional[str] = None
    notificar_cliente: Optional[bool] = True

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
def api_listar_casos(estado: Optional[str] = None, buscar: Optional[str] = None):
    casos = listar_casos(filtro_estado=estado)
    if buscar:
        q = buscar.lower()
        casos = [c for c in casos if
                 q in (c.get("nombre_cliente") or "").lower() or
                 q in (c.get("expediente") or "").lower() or
                 q in (c.get("telefono") or "").lower()]
    return casos

@router.get("/api/casos/stats")
def api_stats():
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
def api_obtener_caso(caso_id: int):
    caso = obtener_caso(caso_id)
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    return caso

@router.post("/api/casos", status_code=201)
def api_crear_caso(data: CaseCreate):
    return crear_caso(data.dict())

@router.put("/api/casos/{caso_id}")
async def api_actualizar_caso(caso_id: int, data: CaseUpdate):
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

@router.delete("/api/casos/{caso_id}")
def api_eliminar_caso(caso_id: int):
    caso = obtener_caso(caso_id)
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    eliminar_caso(caso_id)
    return {"ok": True, "mensaje": "Caso eliminado"}

# ─────────────────────────────────────────────
# Endpoint — Extracción de documento con Claude
# ─────────────────────────────────────────────

@router.post("/api/casos/extraer-documento")
async def api_extraer_documento(archivo: UploadFile = File(...)):
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
# Dashboard (sirve dashboard.html)
# ─────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse("<h1>Dashboard no encontrado</h1>", status_code=404)
