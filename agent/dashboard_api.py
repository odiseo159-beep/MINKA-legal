import os
import httpx
from fastapi import APIRouter, HTTPException, UploadFile, File, Request, Depends
from fastapi.responses import HTMLResponse
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
# Dashboard (sirve dashboard.html)
# ─────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    html_path = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return HTMLResponse("<h1>Dashboard no encontrado</h1>", status_code=404)
