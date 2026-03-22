# agent/tools.py — Herramientas del agente Minka — Asistente Legal
# Minka: Asistente de IA para estudios jurídicos

"""
Herramientas de utilidad general para el agente Minka.
Cubren: carga de configuración, horario de atención y búsqueda en knowledge.
Las herramientas de casos legales están en case_tools.py.
"""

import os
import yaml
import logging
from datetime import datetime

logger = logging.getLogger("minka")


def cargar_info_negocio() -> dict:
    """Carga la información del estudio jurídico desde business.yaml."""
    try:
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("config/business.yaml no encontrado")
        return {}


def obtener_horario() -> dict:
    """Retorna el horario de atención del estudio y si está disponible ahora."""
    info = cargar_info_negocio()
    horario = info.get("negocio", {}).get("horario", "Lunes a Viernes 9am a 6pm")

    ahora = datetime.now()
    dia_semana = ahora.weekday()  # 0=lunes, 6=domingo
    hora = ahora.hour

    # Lunes a Viernes: 9am a 6pm
    if 0 <= dia_semana <= 4:
        esta_abierto = 9 <= hora < 18
    # Sábado: 9am a 1pm
    elif dia_semana == 5:
        esta_abierto = 9 <= hora < 13
    # Domingo: cerrado
    else:
        esta_abierto = False

    return {
        "horario": horario,
        "esta_abierto": esta_abierto,
    }


def buscar_en_knowledge(consulta: str) -> str:
    """
    Busca información relevante en los archivos de /knowledge.
    Retorna el contenido más relevante encontrado.
    """
    resultados = []
    knowledge_dir = "knowledge"

    if not os.path.exists(knowledge_dir):
        return "No hay archivos de conocimiento disponibles."

    for archivo in os.listdir(knowledge_dir):
        ruta = os.path.join(knowledge_dir, archivo)
        if archivo.startswith(".") or not os.path.isfile(ruta):
            continue
        try:
            with open(ruta, "r", encoding="utf-8") as f:
                contenido = f.read()
                if consulta.lower() in contenido.lower():
                    resultados.append(f"[{archivo}]: {contenido[:500]}")
        except (UnicodeDecodeError, IOError):
            continue

    if resultados:
        return "\n---\n".join(resultados)
    return "No encontré información específica sobre eso en mis archivos."
