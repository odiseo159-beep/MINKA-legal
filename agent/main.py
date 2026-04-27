# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Minka — Asistente Legal AI para abogados peruanos

"""
Servidor principal del agente Minka.
Funciona con cualquier proveedor (Whapi, Meta, Twilio) gracias a la capa de providers.
Incluye el dashboard web para que el abogado gestione casos.
"""

import os
import hmac
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from agent.brain import generar_respuesta
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial
from agent.providers import obtener_proveedor
from agent.cases_db import init_cases_db
from agent.dashboard_api import router as dashboard_router
from agent.users_db import init_users_db, usuario_existe, crear_usuario
from agent.auth import hash_password
from agent.auth_api import router as auth_router
from agent.lawyers_db import init_lawyers_db
from agent.lawyer_commands import es_abogado, procesar_comando_abogado
from agent.events_db import init_events_db, eventos_proximos, marcar_notificado
from agent.lawyers_db import listar_abogados
from agent.cases_db import listar_casos
from agent.deadline_calculator import dias_restantes_habiles
from apscheduler.schedulers.asyncio import AsyncIOScheduler

load_dotenv()

# Configuración de logging según entorno
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

# Proveedor de WhatsApp (se configura en .env con WHATSAPP_PROVIDER)
proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa las bases de datos al arrancar el servidor."""
    await inicializar_db()
    init_cases_db()
    init_users_db()
    init_lawyers_db()
    init_events_db()
    # Crear usuario admin inicial si no existe
    admin_email = os.getenv("ADMIN_EMAIL", "")
    admin_password = os.getenv("ADMIN_PASSWORD", "")
    if not admin_email or not admin_password:
        logger.critical(
            "[SECURITY] ADMIN_EMAIL o ADMIN_PASSWORD no configuradas. "
            "No se creará el usuario admin automáticamente. "
            "Configura estas variables en Railway."
        )
    elif not usuario_existe(admin_email):
        crear_usuario(
            email=admin_email,
            password_hash=hash_password(admin_password),
            nombre="Daniel",
            rol="admin",
        )
        logger.info(f"Usuario admin creado: {admin_email}")
    logger.info("Base de datos inicializada (conversaciones + casos + usuarios)")
    logger.info(f"Servidor Minka Legal AI corriendo en puerto {PORT}")
    logger.info(f"Proveedor de WhatsApp: {proveedor.__class__.__name__}")
    logger.info(f"Dashboard disponible en: http://localhost:{PORT}/dashboard")
    # Diagnóstico: verificar WHAPI_TOKEN en tiempo de ejecución
    whapi_tok = os.getenv("WHAPI_TOKEN", "")
    if whapi_tok:
        logger.info(f"[DIAG] WHAPI_TOKEN: {whapi_tok[:4]}...{whapi_tok[-4:]} (len={len(whapi_tok)})")
    else:
        logger.warning("[DIAG] WHAPI_TOKEN no configurado o vacío")

    # Scheduler — agregar jobs e iniciar ANTES del yield
    scheduler.add_job(enviar_alertas_eventos, "cron", hour=8, minute=0)
    scheduler.add_job(enviar_alertas_plazos,  "cron", hour=8, minute=5)
    scheduler.start()
    logger.info("[Alertas] Scheduler iniciado — alertas diarias a las 8:00 AM Lima")

    yield

    # Shutdown — después del yield
    scheduler.shutdown()


_is_dev = ENVIRONMENT == "development"
app = FastAPI(
    title="Minka — Asistente Legal AI",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if _is_dev else None,
    redoc_url="/redoc" if _is_dev else None,
    openapi_url="/openapi.json" if _is_dev else None,
)

# ─────────────────────────────────────────────
# Scheduler — Alertas diarias de eventos
# ─────────────────────────────────────────────

scheduler = AsyncIOScheduler(timezone="America/Lima")


async def enviar_alertas_eventos():
    """Tarea diaria: envía WhatsApp al abogado para eventos próximos."""
    logger.info("[Alertas] Revisando eventos próximos...")
    eventos = eventos_proximos(dias=7)
    for evento in eventos:
        from datetime import datetime
        fecha_evento = datetime.fromisoformat(evento["fecha_hora"])
        dias_hasta = (fecha_evento.date() - datetime.today().date()).days
        # Solo alertar exactamente 1, 3 o 7 días antes
        recordatorio = evento.get("recordatorio_dias", 1)
        if dias_hasta not in (1, 3, 7) or dias_hasta > recordatorio:
            continue
        # Buscar número WhatsApp del abogado
        abogado_id = evento.get("abogado_id")
        if not abogado_id:
            continue
        from agent.lawyers_db import obtener_abogado
        abogado = obtener_abogado(abogado_id)
        if not abogado or not abogado.get("whatsapp_numero"):
            continue
        # Construir mensaje
        tipo = evento.get("tipo", "evento")
        titulo = evento.get("titulo", "")
        fecha_str = fecha_evento.strftime("%d/%m/%Y a las %H:%M")
        mensaje = (
            f"*Recordatorio Minka* ⏰\n\n"
            f"Tienes un {tipo} en *{dias_hasta} día{'s' if dias_hasta != 1 else ''}*:\n"
            f"📋 {titulo}\n"
            f"📅 {fecha_str}\n"
        )
        if evento.get("notas"):
            mensaje += f"📝 {evento['notas']}\n"
        # Enviar via Whapi
        from agent.dashboard_api import _tel_whatsapp, WHAPI_TOKEN, WHAPI_API_URL
        import httpx
        if WHAPI_TOKEN:
            tel = _tel_whatsapp(abogado["whatsapp_numero"])
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        f"{WHAPI_API_URL}/messages/text",
                        headers={
                            "Authorization": f"Bearer {WHAPI_TOKEN}",
                            "Content-Type": "application/json",
                        },
                        json={"to": f"{tel}@s.whatsapp.net", "body": mensaje},
                    )
                    marcar_notificado(evento["id"])
                    logger.info(f"[Alertas] Enviado a abogado {abogado['nombre']} para evento '{titulo}'")
            except Exception as e:
                logger.error(f"[Alertas] Error enviando alerta: {e}")


async def enviar_alertas_plazos():
    """
    Tarea diaria: revisa proxima_fecha de todos los casos activos y notifica
    al abogado cuando restan exactamente 1, 3 o 7 días hábiles.
    """
    from datetime import datetime
    from agent.lawyers_db import obtener_abogado
    from agent.dashboard_api import _tel_whatsapp, WHAPI_TOKEN, WHAPI_API_URL
    import httpx

    logger.info("[Plazos] Revisando plazos de casos activos...")

    estados_activos = {"nuevo", "en_tramite", "en_audiencia", "pendiente_documento",
                       "en_revision", "en_apelacion"}
    casos = listar_casos()
    alertas_enviadas = 0

    for caso in casos:
        # Solo casos activos con proxima_fecha definida
        if caso.get("estado") not in estados_activos:
            continue
        proxima_fecha = caso.get("proxima_fecha", "")
        if not proxima_fecha:
            continue

        try:
            dias = dias_restantes_habiles(proxima_fecha[:10])  # solo YYYY-MM-DD
        except Exception:
            continue

        # Alertar exactamente 1, 3 o 7 días hábiles antes
        if dias not in (1, 3, 7):
            continue

        # Buscar abogado: primero por abogado_id, luego por abogado_asignado (texto)
        abogado = None
        abogado_id = caso.get("abogado_id")
        if abogado_id:
            abogado = obtener_abogado(abogado_id)
        if not abogado:
            # Fallback: buscar abogados registrados y ver si alguno coincide con abogado_asignado
            nombre_asignado = caso.get("abogado_asignado", "").strip()
            if nombre_asignado:
                for ab in listar_abogados(solo_activos=True):
                    if ab.get("nombre", "").lower() == nombre_asignado.lower():
                        abogado = ab
                        break

        if not abogado or not abogado.get("whatsapp_numero"):
            logger.debug(f"[Plazos] Caso {caso['id']} sin abogado con WhatsApp — omitido")
            continue

        # Construir mensaje
        cliente  = caso.get("nombre_cliente", "el cliente")
        exp      = caso.get("expediente", "")
        accion   = caso.get("proxima_accion", "")
        fecha_str = proxima_fecha[:10]
        try:
            fecha_fmt = datetime.strptime(fecha_str, "%Y-%m-%d").strftime("%d/%m/%Y")
        except Exception:
            fecha_fmt = fecha_str

        dia_label = "día hábil" if dias == 1 else "días hábiles"
        mensaje = (
            f"*Alerta de Plazo — Minka* ⚖️\n\n"
            f"El caso de *{cliente}*"
            + (f" (Exp. {exp})" if exp else "")
            + f" vence en *{dias} {dia_label}*.\n\n"
            f"📅 Fecha: {fecha_fmt}\n"
        )
        if accion:
            mensaje += f"📋 Acción: {accion}\n"
        mensaje += "\nRevisa el caso en el dashboard de Minka."

        if not WHAPI_TOKEN:
            logger.info(f"[Plazos] WHAPI_TOKEN no configurado — alerta para {abogado['nombre']} no enviada")
            continue

        tel = _tel_whatsapp(abogado["whatsapp_numero"])
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"{WHAPI_API_URL}/messages/text",
                    headers={
                        "Authorization": f"Bearer {WHAPI_TOKEN}",
                        "Content-Type": "application/json",
                    },
                    json={"to": f"{tel}@s.whatsapp.net", "body": mensaje},
                )
            if r.status_code == 200:
                alertas_enviadas += 1
                logger.info(f"[Plazos] Alerta enviada a {abogado['nombre']} — caso {caso['id']} ({dias}d)")
            else:
                logger.error(f"[Plazos] Error Whapi {r.status_code} para caso {caso['id']}")
        except Exception as e:
            logger.error(f"[Plazos] Error enviando alerta para caso {caso['id']}: {e}")

    logger.info(f"[Plazos] Revision completa — {alertas_enviadas} alertas enviadas")


_cors_origins = ["https://minka-front.vercel.app"]
if os.getenv("ENVIRONMENT", "production") == "development":
    _cors_origins += ["http://localhost:3000", "http://localhost:3001", "http://localhost:3002"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Registrar rutas de auth y dashboard
app.include_router(auth_router)
app.include_router(dashboard_router)

# Montar archivos estáticos del dashboard
static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
async def health_check():
    """Endpoint de salud para Railway/monitoreo."""
    return {"status": "ok", "service": "minka-legal"}


def _check_debug_token(request: Request) -> None:
    """Verifica el token de debug. Lanza 403 si no coincide o si DEBUG_TOKEN no está configurado."""
    debug_token = os.getenv("DEBUG_TOKEN", "")
    if not debug_token:
        raise HTTPException(status_code=404, detail="Not found")
    provided = request.headers.get("X-Debug-Token", "")
    if not provided or not hmac.compare_digest(debug_token, provided):
        raise HTTPException(status_code=403, detail="Forbidden")


@app.get("/debug-lookup")
async def debug_lookup(request: Request, telefono: str = ""):
    """
    Diagnóstico: simula exactamente lo que hace el webhook para buscar el caso de un teléfono.
    Uso: GET /debug-lookup?telefono=51940592068
    """
    _check_debug_token(request)
    from agent.cases_db import buscar_por_telefono, normalizar_telefono
    if not telefono:
        return {"info": "Agrega ?telefono=51940592068"}
    normalizado = normalizar_telefono(telefono)
    casos = buscar_por_telefono(telefono)
    return {
        "telefono_recibido": telefono,
        "telefono_normalizado": normalizado,
        "casos_encontrados": len(casos),
        "casos": [{"id": c["id"], "telefono_db": c["telefono"], "nombre": c["nombre_cliente"]} for c in casos],
    }


@app.get("/test-whapi")
async def test_whapi(request: Request, telefono: str = ""):
    """
    Diagnóstico: intenta enviar un mensaje de prueba via Whapi.
    Uso: GET /test-whapi?telefono=51940592068
    Muestra el token activo y el resultado del envío.
    """
    _check_debug_token(request)
    import httpx
    whapi_tok = os.getenv("WHAPI_TOKEN", "")
    tok_display = f"{whapi_tok[:4]}...{whapi_tok[-4:]}" if len(whapi_tok) >= 8 else "(vacío)"
    if not whapi_tok:
        return {"error": "WHAPI_TOKEN no configurado", "token": tok_display}
    if not telefono:
        return {"token": tok_display, "info": "Agrega ?telefono=51XXXXXXXXX para enviar prueba"}
    to = telefono if "@" in telefono else f"{telefono}@s.whatsapp.net"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(
                "https://gate.whapi.cloud/messages/text",
                headers={"Authorization": f"Bearer {whapi_tok}", "Content-Type": "application/json"},
                json={"to": to, "body": "🔧 Test Minka desde Railway"},
            )
        return {"token": tok_display, "to": to, "status": r.status_code, "response": r.json()}
    except Exception as e:
        return {"token": tok_display, "to": to, "error": str(e)}


@app.get("/webhook")
async def webhook_verificacion(request: Request):
    """Verificación GET del webhook (requerido por Meta Cloud API, no-op para otros)."""
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}


@app.post("/webhook")
@app.post("/webhook/messages")
async def webhook_handler(request: Request):
    """
    Recibe mensajes de WhatsApp via el proveedor configurado.
    Procesa el mensaje, genera respuesta con Claude y la envía de vuelta.
    """
    try:
        # Parsear webhook — el proveedor normaliza el formato
        mensajes = await proveedor.parsear_webhook(request)

        for msg in mensajes:
            # Ignorar mensajes propios o vacíos
            if msg.es_propio or not msg.texto:
                continue

            logger.info(f"[WEBHOOK] Teléfono recibido: '{msg.telefono}' | Mensaje: {msg.texto}")

            # Obtener historial ANTES de guardar el mensaje actual
            # (brain.py agrega el mensaje actual, evitando duplicados)
            historial = await obtener_historial(msg.telefono)

            # Si es el abogado, procesar como comando; si es cliente, usar Claude
            if es_abogado(msg.telefono):
                logger.info(f"[WEBHOOK] Mensaje de abogado detectado: {msg.telefono}")
                respuesta = await procesar_comando_abogado(msg.texto, msg.telefono)
            else:
                # Generar respuesta con Claude (inyecta contexto del caso del cliente)
                respuesta = await generar_respuesta(msg.texto, historial, msg.telefono)

            # Guardar mensaje del usuario Y respuesta del agente en memoria
            await guardar_mensaje(msg.telefono, "user", msg.texto)
            await guardar_mensaje(msg.telefono, "assistant", respuesta)

            # Enviar respuesta por WhatsApp via el proveedor
            enviado = await proveedor.enviar_mensaje(msg.telefono, respuesta)
            if not enviado:
                logger.warning(f"[WEBHOOK] Mensaje generado pero no enviado a {msg.telefono} (WhatsApp no disponible en local)")

            logger.info(f"Respuesta a {msg.telefono}: {respuesta.encode('ascii', errors='replace').decode()}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
