# agent/providers/whapi.py — Adaptador para Whapi.cloud
# Generado por AgentKit

import os
import logging
import httpx
from dataclasses import dataclass
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")

WHAPI_BASE_URL = "https://gate.whapi.cloud"


@dataclass
class PayloadWebhook:
    """Payload completo del webhook de Whapi: identifica el canal de origen
    además de los mensajes. El channel_id viene como código tipo 'WOLVRN-WR3WE'
    en el body del POST de Whapi y es la fuente de verdad para identificar
    qué tenant recibe el webhook."""
    channel_id: str | None
    mensajes: list[MensajeEntrante]


async def enviar_via_whapi(telefono: str, mensaje: str, token: str) -> bool:
    """Envía un mensaje WhatsApp usando el token específico de un canal Whapi.

    Se usa para multi-tenancy: cada abogado tiene su propio canal/token.
    Hasta 3 reintentos. Devuelve True si Whapi acepta (200).
    """
    if not token:
        logger.warning("[WHAPI] enviar_via_whapi llamado sin token — abort")
        return False
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    to = telefono if "@" in telefono else f"{telefono}@s.whatsapp.net"
    for intento in range(3):
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                r = await client.post(
                    f"{WHAPI_BASE_URL}/messages/text",
                    json={"to": to, "body": mensaje},
                    headers=headers,
                )
                if r.status_code == 200:
                    return True
                logger.error(f"[WHAPI] Error enviar_via_whapi: {r.status_code} — {r.text[:200]}")
                if intento < 2:
                    continue
                return False
        except httpx.TimeoutException:
            logger.warning(f"[WHAPI] Timeout intento {intento+1}/3 a {telefono}")
            continue
        except Exception as e:
            logger.error(f"[WHAPI] Error enviando: {e}")
            return False
    return False


async def verificar_token_whapi(token: str) -> dict | None:
    """Verifica que un token Whapi sea válido y obtiene el channel_id + número.

    Devuelve {"channel_id": str, "phone": str, "name": str} si el token es válido.
    None si el token está mal, el canal no existe, o /health no devuelve channel.id.

    IMPORTANTE: NO usar fallback a user.id como channel_id. user.id es el JID del
    WhatsApp (formato '51XXXXXXXX@s.whatsapp.net'), pero el webhook entrante de
    Whapi trae channel.id como código de canal (formato 'WOLVRN-WR3WE'). Mezclar
    los dos rompe el routing por content del webhook (lookup por whapi_channel_id
    en obtener_abogado_por_canal).
    """
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(
                f"{WHAPI_BASE_URL}/health",
                headers={"Authorization": f"Bearer {token}"},
            )
            if r.status_code != 200:
                logger.warning(
                    f"[WHAPI] /health devolvió {r.status_code} — token probablemente inválido"
                )
                return None
            data = r.json()
            user = data.get("user", {}) or {}
            channel = data.get("channel", {}) or {}
            channel_id = channel.get("id") or ""
            if not channel_id:
                # Diagnóstico: imprimir keys del data para entender qué viene
                # cuando channel.id no está poblado (canal sin conectar, formato
                # de respuesta inesperado, etc.). No imprimir el token.
                logger.error(
                    f"[WHAPI] /health no devolvió channel.id. "
                    f"data keys={list(data.keys())} | "
                    f"channel keys={list(channel.keys())} | "
                    f"user keys={list(user.keys())}"
                )
                return None
            return {
                "channel_id": channel_id,
                "phone":      user.get("id", "").split("@")[0] if "@" in user.get("id", "") else user.get("id", ""),
                "name":       user.get("name", ""),
                "status":     data.get("status", {}).get("text", "unknown"),
            }
    except Exception as e:
        logger.error(f"[WHAPI] verificar_token_whapi error: {e}")
        return None


class ProveedorWhapi(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando Whapi.cloud (REST API simple)."""

    def __init__(self):
        self.token = os.getenv("WHAPI_TOKEN")
        self.url_envio = "https://gate.whapi.cloud/messages/text"

    async def _validar_y_leer_body(self, request: Request) -> dict:
        """Valida la firma del webhook (si WHAPI_WEBHOOK_TOKEN está configurado)
        y devuelve el body parseado. Centralizado para que parsear_webhook y
        parsear_webhook_completo lo compartan."""
        webhook_token = os.getenv("WHAPI_WEBHOOK_TOKEN", "")
        if webhook_token:
            import hmac as _hmac
            provided = request.headers.get("X-Whapi-Token", "")
            if not provided or not _hmac.compare_digest(webhook_token, provided):
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail="Invalid webhook token")
        else:
            logger.warning("[WHAPI] WHAPI_WEBHOOK_TOKEN no configurado — webhook sin validación de firma")
        return await request.json()

    def _extraer_payload(self, body: dict) -> PayloadWebhook:
        """Convierte un body ya parseado de Whapi en PayloadWebhook.
        Separado de la lectura del request para que sea testeable y para que
        _process_webhook pueda inspeccionar el channel_id del body."""
        channel_id = body.get("channel_id") or None
        logger.info(
            f"[WHAPI] body.channel_id={channel_id!r} | event={body.get('event')!r} | "
            f"mensajes={len(body.get('messages', []))}"
        )
        mensajes: list[MensajeEntrante] = []
        for msg in body.get("messages", []):
            chat_id = msg.get("chat_id", "")
            # Whapi marca chats grupales con sufijo '@g.us' (ej: '120363...@g.us').
            # El bot NUNCA debe responder a grupos — los abogados los usan para
            # familia/colegas y una respuesta automática del bot sería spam.
            es_grupo = "@g.us" in chat_id
            telefono = chat_id.split("@")[0] if "@" in chat_id else chat_id
            logger.info(
                f"[WHAPI] chat_id recibido: '{chat_id}' → "
                f"teléfono limpio: '{telefono}' | es_grupo={es_grupo}"
            )
            mensajes.append(MensajeEntrante(
                telefono=telefono,
                texto=msg.get("text", {}).get("body", ""),
                mensaje_id=msg.get("id", ""),
                es_propio=msg.get("from_me", False),
                es_grupo=es_grupo,
            ))
        return PayloadWebhook(channel_id=channel_id, mensajes=mensajes)

    async def parsear_webhook_completo(self, request: Request) -> PayloadWebhook:
        """Como parsear_webhook pero también devuelve el channel_id del body —
        usado por el routing por content en _process_webhook."""
        body = await self._validar_y_leer_body(request)
        return self._extraer_payload(body)

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Compat con la interfaz ProveedorWhatsApp — solo retorna mensajes."""
        return (await self.parsear_webhook_completo(request)).mensajes

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía mensaje via Whapi.cloud."""
        if not self.token:
            logger.warning("WHAPI_TOKEN no configurado — mensaje no enviado")
            return False
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        # Whapi requiere formato "51999888777@s.whatsapp.net"
        to = telefono if "@" in telefono else f"{telefono}@s.whatsapp.net"
        for intento in range(3):  # hasta 3 intentos
            try:
                async with httpx.AsyncClient(timeout=45.0) as client:
                    r = await client.post(
                        self.url_envio,
                        json={"to": to, "body": mensaje},
                        headers=headers,
                    )
                    if r.status_code == 200:
                        return True
                    logger.error(f"Error Whapi: {r.status_code} — {r.text}")
                    if intento < 2:
                        continue
                    return False
            except httpx.TimeoutException:
                logger.warning(f"Timeout Whapi intento {intento+1}/3 para {telefono}")
                continue
            except Exception as e:
                logger.error(f"Error enviando via Whapi: {e}")
                return False
        logger.error(f"Whapi: 3 intentos fallidos para {telefono}")
        return False
