# lawyer_chat.py — Bot conversacional para abogados vía WhatsApp
#
# Modelo nuevo (post-pivot): un único canal Whapi de la empresa. Los abogados
# escriben en lenguaje natural al número de Minka y reciben respuestas con
# contexto de SUS casos. Distinto del bot legacy que atendía a CLIENTES sobre
# casos individuales.
#
# Flujo:
#   1. _process_webhook identifica al abogado por su número entrante
#      (obtener_abogado_por_whatsapp).
#   2. Si es abogado registrado → responder_a_abogado() acá.
#   3. Si no → mensaje informativo (no acá, en main.py).
#
# El LLM tiene como contexto un resumen de los casos del abogado (metadata,
# no contenido de documentos para no soplar tokens). Si falla por timeout o
# error de API, fallback a comandos rígidos de lawyer_commands.py.

import os
import logging
import anthropic
from agent.cases_db import listar_casos
from agent.lawyer_commands import procesar_comando_abogado, ESTADOS_LABELS

logger = logging.getLogger("agentkit")

_HISTORIAL_MAX_TURNOS = 10  # últimos 10 mensajes (5 turnos) en el contexto


SYSTEM_PROMPT_BASE = """Eres Minka, asistente AI para abogados peruanos especializado en gestión de casos legales.

Estás conversando por WhatsApp con un abogado sobre SUS casos. Tu tarea:
1. Responder preguntas concretas sobre el estado, fechas, plazos, partes, documentos pendientes de cada caso.
2. Recordarle plazos próximos cuando lo consulte.
3. Si te pide actualizar el estado de un caso o notificar al cliente, decile que use los comandos: "actualizar [expediente] [estado]" o "notificar [expediente]".

Formato de respuesta (CRÍTICO — es WhatsApp):
- Texto plano, sin markdown
- NO uses #, ##, **, *, --, ---, |, > ni emojis decorativos
- Para enumerar usa números: 1. 2. 3.
- Sé conciso y directo, máximo 3-4 oraciones por respuesta a menos que pida detalle
- Para fechas usá el formato dd/mm/aaaa
- Para artículos legales usá texto plano: "Art. 357 del CPC"
- Si la pregunta es ambigua (varios casos posibles), pedí que precise el expediente o nombre del cliente

Si el abogado pregunta algo que NO está en sus casos (consulta general sobre derecho peruano, etc.), responde brevemente desde tu conocimiento pero aclará que es información general, no de un caso suyo en particular.

NUNCA inventes datos de casos. Si no aparece en la información que te paso, decí "ese dato no figura en el caso".
"""


def _resumen_caso_para_prompt(caso: dict) -> str:
    """Resumen one-liner de un caso, para inyectar en el system prompt."""
    estado_label = ESTADOS_LABELS.get(caso.get("estado") or "", caso.get("estado") or "—")
    partes = [
        f"Cliente: {caso.get('nombre_cliente') or '—'}",
        f"Exp: {caso.get('expediente') or 'Sin expediente'}",
        f"Tipo: {caso.get('tipo_caso') or '—'}",
        f"Estado: {estado_label}",
    ]
    if caso.get("proxima_fecha"):
        partes.append(f"Próxima fecha: {caso['proxima_fecha']}")
    if caso.get("proxima_accion"):
        partes.append(f"Próxima acción: {caso['proxima_accion']}")
    if caso.get("documentos_pendientes"):
        partes.append(f"Docs pendientes: {caso['documentos_pendientes']}")
    if caso.get("notas"):
        # Truncar notas largas
        notas = caso["notas"]
        if len(notas) > 200:
            notas = notas[:200] + "..."
        partes.append(f"Notas: {notas}")
    return " | ".join(partes)


def _contexto_casos_del_abogado(abogado_id: int) -> str:
    """Construye un bloque de texto con los casos activos del abogado.
    Solo casos no resueltos/archivados. Máximo 30 casos para no exceder tokens."""
    casos = listar_casos(abogado_id=abogado_id)

    activos = [
        c for c in casos
        if c.get("estado") not in ("resuelto", "archivado")
    ]

    if not activos:
        return "(El abogado no tiene casos activos registrados)."

    if len(activos) > 30:
        activos = activos[:30]
        nota = f"\n(Mostrando 30 de {len(casos)} casos activos. Si pregunta por uno fuera de esta lista, pedir expediente.)"
    else:
        nota = ""

    lineas = [f"{i+1}. {_resumen_caso_para_prompt(c)}" for i, c in enumerate(activos)]
    return "\n".join(lineas) + nota


async def responder_a_abogado(
    texto: str,
    abogado: dict,
    historial: list[dict],
) -> str:
    """Genera respuesta al abogado usando Claude con contexto de sus casos.

    Si el LLM falla (timeout, error de API, etc.), hace fallback a los comandos
    rígidos de lawyer_commands.py para que el abogado nunca quede sin respuesta.
    """
    # Comandos rígidos de alta prioridad — los procesamos antes del LLM porque
    # el abogado los puede haber memorizado y la respuesta es determinística
    # (más rápida y barata). Solo "ayuda", "actualizar X Y" y "notificar X" — el
    # resto de comandos rígidos quedan como fallback.
    texto_lower = texto.strip().lower()
    if texto_lower in ("ayuda", "help", "?", "comandos"):
        return await procesar_comando_abogado(texto, abogado.get("whatsapp_numero", ""))
    if texto_lower.startswith("actualizar ") or texto_lower.startswith("notificar "):
        return await procesar_comando_abogado(texto, abogado.get("whatsapp_numero", ""))

    # LLM primario: usar Claude con contexto de los casos del abogado
    try:
        client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
        nombre = abogado.get("nombre") or "Abogado"
        contexto_casos = _contexto_casos_del_abogado(abogado["id"])
        system = (
            SYSTEM_PROMPT_BASE
            + f"\n\nEstás hablando con: {nombre}.\n\n"
            + "CASOS ACTIVOS DEL ABOGADO:\n"
            + contexto_casos
        )

        # Historial: tomamos los últimos N mensajes de la conversación WA
        # con este número (tabla `mensajes` legacy de SQLAlchemy).
        mensajes_api: list[dict] = []
        for h in historial[-_HISTORIAL_MAX_TURNOS:]:
            role = h.get("role")
            content = h.get("content") or ""
            if role in ("user", "assistant") and content:
                mensajes_api.append({"role": role, "content": content})
        mensajes_api.append({"role": "user", "content": texto})

        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            messages=mensajes_api,
        )
        respuesta = response.content[0].text if response.content else ""
        if not respuesta.strip():
            raise RuntimeError("Respuesta vacía del modelo")

        logger.info(
            f"[LAWYER_CHAT] ab{abogado['id']} ({response.usage.input_tokens} in / "
            f"{response.usage.output_tokens} out tokens)"
        )
        return respuesta

    except Exception as e:
        logger.warning(
            f"[LAWYER_CHAT] LLM falló para ab{abogado.get('id')}: {e}. "
            f"Fallback a comandos rígidos."
        )
        # Fallback: comandos rígidos. Si tampoco matchea, devolver mensaje útil.
        respuesta_comando = await procesar_comando_abogado(
            texto, abogado.get("whatsapp_numero", "")
        )
        # procesar_comando_abogado devuelve "Comando no reconocido..." si no
        # matchea. Reemplazamos por algo más amigable.
        if "no reconocido" in respuesta_comando.lower():
            return (
                "Tuve un problema técnico para responder. Probá de nuevo en un "
                "momento. Mientras tanto, podés escribir \"ayuda\" para ver los "
                "comandos disponibles."
            )
        return respuesta_comando
