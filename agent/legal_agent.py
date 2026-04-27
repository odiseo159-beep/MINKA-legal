import os
import anthropic
from agent.prompts import AGENT_SYSTEM_PROMPTS
from agent.rag import buscar_normativa, formatear_para_prompt
from agent.legal_advisor import generar_consejo_procesal

ACCIONES_VALIDAS = {"analizar", "asesorar", "redactar", "normativa"}

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
    if tool_name == "get_case_documents":
        return _get_case_documents(caso_id, caso)
    elif tool_name == "search_normativa":
        return _search_normativa(tool_input.get("query", ""))
    elif tool_name == "consejo_procesal":
        return _consejo_procesal(caso)
    return "Tool no reconocida."


def _get_case_documents(caso_id: int, caso: dict) -> str:
    from agent.cases_db import listar_documentos_caso, obtener_documento_caso
    from agent.crypto import decrypt_decompress

    docs = listar_documentos_caso(caso_id)
    textos = []

    for doc in docs[:3]:
        try:
            doc_data = obtener_documento_caso(doc["id"])
            if doc_data and doc_data.get("texto_relevante"):
                texto = decrypt_decompress(doc_data["texto_relevante"])
                textos.append(f"=== {doc['nombre']} ===\n{texto[:3000]}")
        except Exception:
            textos.append(f"=== {doc['nombre']} === [No se pudo descifrar]")

    # Fallback: documento legacy en campo documento_texto del caso
    if not textos and caso.get("documento_texto"):
        textos.append(f"=== Documento del caso (legacy) ===\n{caso['documento_texto'][:3000]}")

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

    for _ in range(max_iterations):
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4096,
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
                    result = _execute_tool(block.name, block.input, caso_id, caso)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })

            messages.append({"role": "user", "content": tool_results})

        else:
            raise RuntimeError(
                f"Stop inesperado del modelo: '{response.stop_reason}'. "
                f"Herramientas usadas hasta ahora: {tools_used}"
            )

    raise RuntimeError("El agente no convergió. Intenta de nuevo.")
