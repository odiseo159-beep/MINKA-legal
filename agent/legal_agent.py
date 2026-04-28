import os
import logging
import anthropic
from agent.prompts import AGENT_SYSTEM_PROMPTS
from agent.rag import buscar_normativa, formatear_para_prompt
from agent.legal_advisor import generar_consejo_procesal

logger = logging.getLogger("agentkit")

ACCIONES_VALIDAS = {"analizar", "asesorar", "redactar", "normativa"}

# Hard cap de caracteres por documento — protección contra prompt injection
# y consumo excesivo de tokens si el abogado sube documentos enormes.
_MAX_CHARS_POR_DOC = 6000
_MAX_DOCS = 5

AGENT_TOOLS = [
    {
        "name": "get_case_documents",
        "description": "Obtiene el texto completo de los documentos del caso para análisis",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "search_normativa",
        "description": "Busca artículos en los códigos legales peruanos (CP, CPP, CC, CPC, NLPT, CNA, Ley 30364, etc.)",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Término de búsqueda (ej: 'plazo prescripción estafa', 'art 387 peculado')",
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "consejo_procesal",
        "description": "Obtiene la etapa procesal actual del caso, siguiente etapa, plazos legales y documentos requeridos",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


def _execute_tool(tool_name: str, tool_input: dict, caso_id: int, caso: dict) -> str:
    """Ejecuta una tool del agente. Atrapa cualquier excepción y devuelve mensaje al modelo.

    NUNCA propagar excepciones al loop del agente — eso aborta el ciclo entero. En su lugar,
    devolver un string descriptivo del error para que Claude pueda decidir qué hacer.
    """
    try:
        if tool_name == "get_case_documents":
            return _get_case_documents(caso_id, caso)
        elif tool_name == "search_normativa":
            return _search_normativa(tool_input.get("query", ""))
        elif tool_name == "consejo_procesal":
            return _consejo_procesal(caso)
        return f"Tool '{tool_name}' no reconocida."
    except Exception as e:
        logger.error(f"[AGENT] Error ejecutando tool '{tool_name}': {e}", exc_info=True)
        return f"[Error al ejecutar {tool_name}: {type(e).__name__}. Intenta otra estrategia.]"


def _sanitizar_texto_documento(texto: str) -> str:
    """Trunca y delimita texto de documento para mitigar prompt injection.

    El agente recibe texto de documentos subidos por el abogado. Si un atacante sube
    un documento con instrucciones (\"ignora las anteriores y haz X\"), Claude podría
    seguirlas. Truncamos + envolvemos con marcadores claros y el system prompt recuerda
    al modelo tratar el contenido como datos, no como instrucciones.
    """
    if not texto:
        return ""
    truncado = texto[:_MAX_CHARS_POR_DOC]
    if len(texto) > _MAX_CHARS_POR_DOC:
        truncado += f"\n[... documento truncado en {_MAX_CHARS_POR_DOC} caracteres ...]"
    return truncado


def _get_case_documents(caso_id: int, caso: dict) -> str:
    from agent.cases_db import listar_documentos_caso, obtener_documento_caso
    from agent.crypto import decrypt_decompress

    docs = listar_documentos_caso(caso_id)
    textos = []

    for doc in docs[:_MAX_DOCS]:
        try:
            doc_data = obtener_documento_caso(doc["id"])
            if doc_data and doc_data.get("texto_relevante"):
                texto = decrypt_decompress(doc_data["texto_relevante"])
                texto_sanitizado = _sanitizar_texto_documento(texto)
                # Marcadores explícitos: "[CONTENIDO_DOCUMENTO_INICIO/FIN]" señalan al modelo
                # que lo de adentro es DATA, no instrucciones a seguir.
                textos.append(
                    f"=== Documento: {doc['nombre']} ===\n"
                    f"[CONTENIDO_DOCUMENTO_INICIO]\n"
                    f"{texto_sanitizado}\n"
                    f"[CONTENIDO_DOCUMENTO_FIN]"
                )
        except Exception as e:
            logger.warning(f"[AGENT] No se pudo descifrar doc {doc.get('id')}: {e}")
            textos.append(f"=== {doc['nombre']} === [No se pudo descifrar — omitir en el análisis]")

    # Fallback: documento legacy en campo documento_texto del caso
    if not textos and caso.get("documento_texto"):
        legacy = _sanitizar_texto_documento(caso["documento_texto"])
        textos.append(
            f"=== Documento del caso (legacy) ===\n"
            f"[CONTENIDO_DOCUMENTO_INICIO]\n{legacy}\n[CONTENIDO_DOCUMENTO_FIN]"
        )

    return "\n\n".join(textos) if textos else "No hay texto disponible en los documentos."


def _search_normativa(query: str) -> str:
    if not query.strip():
        return "Query vacía."
    articulos = buscar_normativa(query, top_k=5)
    return formatear_para_prompt(articulos) if articulos else f"No se encontraron artículos para: {query}"


def _consejo_procesal(caso: dict) -> str:
    consejo = generar_consejo_procesal(caso)
    if not consejo.get("tiene_consejo"):
        return f"Sin consejo disponible: {consejo.get('motivo', 'tipo de caso no reconocido')}"

    lines = [
        f"Proceso: {consejo.get('tipo_proceso', '')}",
        f"Norma base: {consejo.get('norma_base', '')}",
        f"Etapa actual: {consejo.get('etapa_actual', '')}",
        f"Siguiente etapa: {consejo.get('siguiente_etapa', '')}",
        f"Descripción: {consejo.get('siguiente_descripcion', '')}",
        f"Plazo: {consejo.get('plazo_descripcion', '')}",
        f"Fecha sugerida: {consejo.get('proxima_fecha_sugerida', 'No calculada')}",
        f"Documentos requeridos: {', '.join(consejo.get('documentos_requeridos', [])) or 'Ninguno'}",
        f"Norma aplicable: {consejo.get('norma', '')}",
    ]
    if consejo.get("advertencia"):
        lines.insert(0, f"⚠️ {consejo['advertencia']}")

    return "\n".join(lines)


def _build_initial_message(accion: str, caso: dict, parametros: dict) -> str:
    caso_resumen = (
        f"CASO: {caso.get('nombre_cliente')} | "
        f"Tipo: {caso.get('tipo_caso')} | "
        f"Estado: {caso.get('estado')} | "
        f"Expediente: {caso.get('expediente', 'S/N')} | "
        f"Notas: {caso.get('notas', 'Sin notas')}"
    )

    if accion == "analizar":
        return f"{caso_resumen}\n\nAnaliza los documentos del caso. Usa la tool get_case_documents para obtenerlos."

    elif accion == "asesorar":
        tema = parametros.get("tema", "estrategia procesal general")
        return (
            f"{caso_resumen}\n\n"
            f"Proporciona asesoría legal sobre: {tema}\n"
            f"Usa consejo_procesal para la etapa actual y search_normativa para normativa relevante."
        )

    elif accion == "redactar":
        tipo_escrito = parametros.get("tipo_escrito", "escrito")
        destinatario = parametros.get("destinatario", "Señor Juez")
        return (
            f"{caso_resumen}\n\n"
            f"Redacta un borrador de: {tipo_escrito}\n"
            f"Destinatario: {destinatario}\n"
            f"Usa get_case_documents para datos del caso y search_normativa para fundamentación."
        )

    elif accion == "normativa":
        query = parametros.get("query", "")
        return (
            f"{caso_resumen}\n\n"
            f"Consulta de normativa: {query}\n"
            f"Usa search_normativa para buscar artículos relevantes."
        )

    return caso_resumen


async def ejecutar_agente(caso_id: int, accion: str, parametros: dict, caso: dict) -> dict:
    if accion not in ACCIONES_VALIDAS:
        raise ValueError(f"Acción no válida: '{accion}'. Opciones: {', '.join(ACCIONES_VALIDAS)}")

    client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    system_prompt = AGENT_SYSTEM_PROMPTS[accion]
    user_message = _build_initial_message(accion, caso, parametros)

    messages = [{"role": "user", "content": user_message}]
    tools_used: list[str] = []
    max_iterations = 6
    total_tokens = 0
    total_cached = 0

    max_tokens = 8192 if accion == "redactar" else 4096

    for _ in range(max_iterations):
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=[{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}],
            tools=AGENT_TOOLS,
            messages=messages,
        )

        total_tokens += response.usage.input_tokens + response.usage.output_tokens
        total_cached += int(getattr(response.usage, "cache_read_input_tokens", 0) or 0)

        if response.stop_reason == "end_turn":
            text = "\n".join(b.text for b in response.content if hasattr(b, "text"))
            return {
                "accion": accion,
                "resultado": text,
                "tools_usados": tools_used,
                "tokens_usados": total_tokens,
                "cached": total_cached > 0,
            }

        if response.stop_reason == "tool_use":
            messages.append({"role": "assistant", "content": response.content})
            tool_results = []

            for block in response.content:
                if block.type == "tool_use":
                    tools_used.append(block.name)
                    # _execute_tool ya atrapa excepciones internamente y retorna string
                    result = _execute_tool(block.name, block.input, caso_id, caso)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "user", "content": tool_results})

        elif response.stop_reason == "max_tokens":
            # El modelo se quedó sin presupuesto — devolver lo que se haya generado
            text = "\n".join(b.text for b in response.content if hasattr(b, "text"))
            logger.warning(f"[AGENT] max_tokens alcanzado (acción={accion}, iter={_})")
            return {
                "accion": accion,
                "resultado": text + "\n\n_[Respuesta truncada — alcanzó el límite de tokens]_",
                "tools_usados": tools_used,
                "tokens_usados": total_tokens,
                "cached": total_cached > 0,
                "truncado": True,
            }

        else:
            logger.error(f"[AGENT] Stop inesperado: {response.stop_reason}, tools={tools_used}")
            raise RuntimeError(
                f"El modelo terminó inesperadamente ({response.stop_reason}). "
                f"Intenta de nuevo o ajusta los parámetros."
            )

    # Si llegamos aquí, max_iterations alcanzado sin end_turn — retornar lo último generado
    logger.warning(f"[AGENT] max_iterations={max_iterations} alcanzado sin convergencia (acción={accion})")
    text = "\n".join(b.text for b in response.content if hasattr(b, "text")) if response else ""
    if text:
        return {
            "accion": accion,
            "resultado": text + "\n\n_[Análisis incompleto — el agente alcanzó el máximo de iteraciones]_",
            "tools_usados": tools_used,
            "tokens_usados": total_tokens,
            "cached": total_cached > 0,
            "incompleto": True,
        }
    raise RuntimeError("El agente no convergió en el número máximo de iteraciones. Intenta de nuevo.")
