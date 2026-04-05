# lawyer_commands.py — Comandos WhatsApp para el abogado
# Minka — Asistente Legal AI
#
# Cuando un abogado escribe por WhatsApp, puede usar comandos especiales
# en lugar de pasar por el bot de Claude.
#
# Comandos disponibles:
#   ayuda                          → lista de comandos
#   estado <expediente>            → info completa de un caso
#   casos hoy                      → casos con proxima_fecha = hoy
#   casos pendientes               → casos con estado pendiente_documento
#   buscar <nombre>                → buscar casos por nombre de cliente
#   actualizar <expediente> <estado> → cambiar estado de un caso
#   notificar <expediente>         → enviar WhatsApp al cliente del caso

import re
import logging
from datetime import date
from agent.lawyers_db import obtener_abogado_por_whatsapp
from agent.cases_db import (
    listar_casos,
    buscar_por_telefono,
    actualizar_caso,
)

logger = logging.getLogger("agentkit")

ESTADOS_VALIDOS = {
    "nuevo", "en_tramite", "en_audiencia",
    "pendiente_documento", "en_revision", "en_apelacion",
    "resuelto", "archivado",
}

ESTADOS_LABELS = {
    "nuevo":               "Nuevo",
    "en_tramite":          "En trámite",
    "en_audiencia":        "En audiencia",
    "pendiente_documento": "Pendiente de documento",
    "en_revision":         "En revisión",
    "en_apelacion":        "En apelación",
    "resuelto":            "Resuelto",
    "archivado":           "Archivado",
}

MENSAJE_AYUDA = """*Comandos disponibles para Minka:*

📋 *estado* _expediente_
  → Info completa del caso (ej: estado EXP-2026-0142)

📅 *casos hoy*
  → Casos con fecha importante hoy

📄 *casos pendientes*
  → Casos con documentos pendientes

🔍 *buscar* _nombre_
  → Buscar por nombre de cliente

✏️ *actualizar* _expediente_ _estado_
  → Cambiar estado del caso
  Estados válidos: nuevo, en_tramite, en_audiencia, pendiente_documento, en_revision, en_apelacion, resuelto, archivado

📲 *notificar* _expediente_
  → Enviar actualización WhatsApp al cliente

❓ *ayuda* → Este menú"""


def es_abogado(telefono: str) -> bool:
    """Retorna True si el número de teléfono pertenece a un abogado registrado."""
    abogado = obtener_abogado_por_whatsapp(telefono)
    return abogado is not None


def _buscar_caso_por_expediente(expediente: str) -> dict | None:
    """Busca un caso por número de expediente (case-insensitive)."""
    expediente_norm = expediente.strip().upper()
    casos = listar_casos()
    for caso in casos:
        exp = (caso.get("expediente") or "").strip().upper()
        if exp == expediente_norm:
            return caso
    return None


def _formatear_caso(caso: dict) -> str:
    """Formatea un caso para respuesta WhatsApp."""
    estado = ESTADOS_LABELS.get(caso.get("estado", ""), caso.get("estado", ""))
    lineas = [
        f"*{caso.get('nombre_cliente', '—')}*",
        f"Expediente: {caso.get('expediente') or 'Sin expediente'}",
        f"Tipo: {caso.get('tipo_caso') or '—'}",
        f"Estado: {estado}",
    ]
    if caso.get("proxima_fecha"):
        lineas.append(f"Próxima fecha: {caso['proxima_fecha']}")
    if caso.get("proxima_accion"):
        lineas.append(f"Próxima acción: {caso['proxima_accion']}")
    if caso.get("documentos_pendientes"):
        lineas.append(f"Docs. pendientes: {caso['documentos_pendientes']}")
    return "\n".join(lineas)


async def procesar_comando_abogado(texto: str, telefono_abogado: str) -> str:
    """
    Parsea y ejecuta el comando enviado por el abogado.
    Retorna la respuesta como string (se enviará por WhatsApp).
    """
    texto_norm = texto.strip()
    lower = texto_norm.lower()

    # ── ayuda ──────────────────────────────────────────────────────────
    if lower in ("ayuda", "help", "?", "hola", "comandos"):
        return MENSAJE_AYUDA

    # ── estado <expediente> ────────────────────────────────────────────
    m = re.match(r"^estado\s+(.+)$", lower)
    if m:
        exp = texto_norm[len("estado "):].strip()
        caso = _buscar_caso_por_expediente(exp)
        if not caso:
            return f"No encontré ningún caso con expediente *{exp}*."
        return _formatear_caso(caso)

    # ── casos hoy ─────────────────────────────────────────────────────
    if lower in ("casos hoy", "hoy"):
        hoy = date.today().isoformat()
        casos = listar_casos()
        urgentes = [
            c for c in casos
            if (c.get("proxima_fecha") or "").startswith(hoy)
            and c.get("estado") not in ("resuelto", "archivado")
        ]
        if not urgentes:
            return f"No hay casos con fecha para hoy ({hoy})."
        lines = [f"*Casos para hoy ({hoy}):*\n"]
        for c in urgentes:
            lines.append(f"• {c.get('expediente') or 'Sin exp.'} — {c.get('nombre_cliente')} [{ESTADOS_LABELS.get(c.get('estado',''), c.get('estado',''))}]")
        return "\n".join(lines)

    # ── casos pendientes ───────────────────────────────────────────────
    if re.match(r"^casos?\s+pendientes?$", lower):
        casos = listar_casos(filtro_estado="pendiente_documento")
        if not casos:
            return "No hay casos con documentos pendientes."
        lines = [f"*Casos pendientes de documento ({len(casos)}):*\n"]
        for c in casos:
            lines.append(f"• {c.get('expediente') or 'Sin exp.'} — {c.get('nombre_cliente')}")
            if c.get("documentos_pendientes"):
                lines.append(f"  📎 {c['documentos_pendientes']}")
        return "\n".join(lines)

    # ── buscar <nombre> ───────────────────────────────────────────────
    m = re.match(r"^buscar\s+(.+)$", lower)
    if m:
        query = m.group(1).strip()
        casos = listar_casos()
        encontrados = [
            c for c in casos
            if query in (c.get("nombre_cliente") or "").lower()
            or query in (c.get("expediente") or "").lower()
        ]
        if not encontrados:
            return f"No encontré casos para \"{query}\"."
        lines = [f"*Resultados para \"{query}\" ({len(encontrados)}):*\n"]
        for c in encontrados[:10]:  # máximo 10 resultados
            estado = ESTADOS_LABELS.get(c.get("estado", ""), c.get("estado", ""))
            lines.append(f"• {c.get('expediente') or 'Sin exp.'} — {c.get('nombre_cliente')} [{estado}]")
        if len(encontrados) > 10:
            lines.append(f"  ... y {len(encontrados) - 10} más.")
        return "\n".join(lines)

    # ── actualizar <expediente> <estado> ──────────────────────────────
    m = re.match(r"^actualizar\s+(\S+)\s+(\S+)$", lower)
    if m:
        exp = texto_norm.split()[1]
        nuevo_estado = m.group(2).strip()
        if nuevo_estado not in ESTADOS_VALIDOS:
            estados_str = ", ".join(sorted(ESTADOS_VALIDOS))
            return f"Estado \"{nuevo_estado}\" no válido.\nEstados aceptados: {estados_str}"
        caso = _buscar_caso_por_expediente(exp)
        if not caso:
            return f"No encontré ningún caso con expediente *{exp}*."
        caso_actualizado = actualizar_caso(caso["id"], {"estado": nuevo_estado})
        estado_label = ESTADOS_LABELS.get(nuevo_estado, nuevo_estado)
        return (
            f"Caso *{exp}* actualizado.\n"
            f"Cliente: {caso_actualizado.get('nombre_cliente')}\n"
            f"Nuevo estado: {estado_label}"
        )

    # ── notificar <expediente> ─────────────────────────────────────────
    m = re.match(r"^notificar\s+(.+)$", lower)
    if m:
        exp = texto_norm[len("notificar "):].strip()
        caso = _buscar_caso_por_expediente(exp)
        if not caso:
            return f"No encontré ningún caso con expediente *{exp}*."
        # Importar aquí para evitar circular imports
        from agent.dashboard_api import enviar_notificacion_whatsapp
        enviado = await enviar_notificacion_whatsapp(caso)
        if enviado:
            return f"Notificación enviada a *{caso.get('nombre_cliente')}* ({caso.get('telefono')})."
        return f"No se pudo enviar la notificación. Verifica que WHAPI_TOKEN esté configurado."

    # ── comando no reconocido ──────────────────────────────────────────
    return (
        f"Comando no reconocido: \"{texto_norm}\"\n\n"
        "Escribe *ayuda* para ver los comandos disponibles."
    )
