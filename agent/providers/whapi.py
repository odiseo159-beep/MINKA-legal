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
        """Parsea el payload de Whapi.cloud."""
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
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    self.url_envio,
                    json={"to": telefono, "body": mensaje},
                    headers=headers,
                )
                if r.status_code != 200:
                    logger.error(f"Error Whapi: {r.status_code} — {r.text}")
                return r.status_code == 200
        except httpx.TimeoutException:
            logger.error(f"Timeout enviando mensaje a {telefono} via Whapi")
            return False
        except Exception as e:
            logger.error(f"Error enviando mensaje via Whapi: {e}")
            return False
