# agent/main.py — Servidor FastAPI + Webhook de WhatsApp
# Minka — Asistente Legal AI para abogados peruanos

"""
Servidor principal del agente Minka.
Funciona con cualquier proveedor (Whapi, Meta, Twilio) gracias a la capa de providers.
Incluye el dashboard web para que el abogado gestione casos.
"""

import os
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
    admin_email = os.getenv("ADMIN_EMAIL", "daniel@simplifai.pe")
    admin_password = os.getenv("ADMIN_PASSWORD", "minka2026")
    if not usuario_existe(admin_email):
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
    yield


app = FastAPI(
    title="Minka — Asistente Legal AI",
    version="1.0.0",
    lifespan=lifespan
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


@app.on_event("startup")
async def start_scheduler():
    scheduler.add_job(enviar_alertas_eventos, "cron", hour=8, minute=0)
    scheduler.start()
    logger.info("[Alertas] Scheduler iniciado — alertas diarias a las 8:00 AM Lima")


@app.on_event("shutdown")
async def stop_scheduler():
    scheduler.shutdown()


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
            await proveedor.enviar_mensaje(msg.telefono, respuesta)

            logger.info(f"Respuesta a {msg.telefono}: {respuesta}")

        return {"status": "ok"}

    except Exception as e:
        logger.error(f"Error en webhook: {e}")
        raise HTTPException(status_code=500, detail=str(e))
