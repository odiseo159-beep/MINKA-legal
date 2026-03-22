# case_tools.py — Herramientas del bot para consultar casos
# El bot usa estas funciones cuando un cliente pregunta por su caso

import logging
from agent.cases_db import buscar_por_telefono

logger = logging.getLogger("agentkit")


def consultar_caso_cliente(telefono: str) -> str:
    """
    Busca los casos asociados al número de teléfono del cliente.
    Retorna un resumen formateado que el bot puede usar para responder.
    """
    logger.info(f"Teléfono del remitente: {telefono}")
    casos = buscar_por_telefono(telefono)
    logger.info(f"Casos encontrados: {casos}")

    if not casos:
        return "NO_ENCONTRADO"
    
    resumen_partes = []
    
    for caso in casos:
        partes = []
        partes.append(f"Cliente: {caso['nombre_cliente']}")
        
        if caso.get('expediente'):
            partes.append(f"Expediente: {caso['expediente']}")
        
        if caso.get('tipo_caso'):
            partes.append(f"Tipo de caso: {caso['tipo_caso']}")
        
        if caso.get('estado'):
            estados_legibles = {
                "nuevo": "Recién ingresado",
                "en_tramite": "En trámite",
                "en_audiencia": "En etapa de audiencia",
                "pendiente_documento": "Pendiente de documentación",
                "en_revision": "En revisión",
                "en_apelacion": "En apelación",
                "resuelto": "Resuelto",
                "archivado": "Archivado",
            }
            estado_legible = estados_legibles.get(caso['estado'], caso['estado'])
            partes.append(f"Estado actual: {estado_legible}")
        
        if caso.get('proxima_fecha'):
            partes.append(f"Próxima fecha importante: {caso['proxima_fecha']}")
        
        if caso.get('proxima_accion'):
            partes.append(f"Próxima acción: {caso['proxima_accion']}")
        
        if caso.get('documentos_pendientes'):
            partes.append(f"Documentos pendientes: {caso['documentos_pendientes']}")
        
        if caso.get('abogado_asignado'):
            partes.append(f"Abogado asignado: {caso['abogado_asignado']}")
        
        if caso.get('notas'):
            partes.append(f"Notas: {caso['notas']}")
        
        resumen_partes.append("\n".join(partes))
    
    return "\n---\n".join(resumen_partes)


def generar_contexto_para_bot(telefono: str) -> str:
    """
    Genera el contexto que se inyecta en el prompt del bot
    cuando un cliente escribe. Esto se usa en brain.py.
    """
    info_caso = consultar_caso_cliente(telefono)
    
    if info_caso == "NO_ENCONTRADO":
        return """
[INFORMACIÓN DEL CLIENTE]
Este número de teléfono NO está registrado en el sistema.
Responde amablemente que no tienes información de un caso asociado a su número.
Sugiere que se comunique directamente con el estudio de abogados para registrarse.
"""
    
    return f"""
[INFORMACIÓN DEL CASO DEL CLIENTE]
El cliente que está escribiendo tiene los siguientes casos registrados:

{info_caso}

Usa esta información para responder sus consultas sobre el estado de su caso.
NO inventes información que no esté aquí. Si pregunta algo que no tienes, dile que 
consultarás con su abogado y le responderás a la brevedad.
"""
