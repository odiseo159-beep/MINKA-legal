# agent/providers/whapi.py — Adaptador para Whapi.cloud
# Generado por AgentKit

import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")


class ProveedorWhapi(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando Whapi.cloud (REST API simple)."""

    def __init__(self):
        self.token = os.getenv("WHAPI_TOKEN")
        self.url_envio = "https://gate.whapi.cloud/messages/text"

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload de Whapi.cloud, validando el token de webhook si está configurado."""
        webhook_token = os.getenv("WHAPI_WEBHOOK_TOKEN", "")
        if webhook_token:
            import hmac as _hmac
            provided = request.headers.get("X-Whapi-Token", "")
            if not provided or not _hmac.compare_digest(webhook_token, provided):
                from fastapi import HTTPException
                raise HTTPException(status_code=403, detail="Invalid webhook token")
        else:
            logger.warning("[WHAPI] WHAPI_WEBHOOK_TOKEN no configurado — webhook sin validación de firma")

        body = await request.json()
        mensajes = []
        for msg in body.get("messages", []):
            # Whapi envía chat_id como "51912345678@s.whatsapp.net" — limpiar el sufijo
            chat_id = msg.get("chat_id", "")
            telefono = chat_id.split("@")[0] if "@" in chat_id else chat_id
            logger.info(f"[WHAPI] chat_id recibido: '{chat_id}' → teléfono limpio: '{telefono}'")
            mensajes.append(MensajeEntrante(
                telefono=telefono,
                texto=msg.get("text", {}).get("body", ""),
                mensaje_id=msg.get("id", ""),
                es_propio=msg.get("from_me", False),
            ))
        return mensajes

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
                    return False
            except httpx.TimeoutException:
                logger.warning(f"Timeout Whapi intento {intento+1}/3 para {telefono}")
            except Exception as e:
                logger.error(f"Error enviando via Whapi: {e}")
                return False
        logger.error(f"Whapi: 3 intentos fallidos para {telefono}")
        return False
