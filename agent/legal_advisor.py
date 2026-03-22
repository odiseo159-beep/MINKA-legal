# legal_advisor.py
# Motor de asesoría procesal para Minka
# Usa la base de conocimiento real de los 4 procesos peruanos

import json
import os
from datetime import date, timedelta

# Feriados Perú 2025-2026
FERIADOS_PERU = {
    date(2025, 1, 1),  date(2025, 4, 17), date(2025, 4, 18),
    date(2025, 5, 1),  date(2025, 6, 29), date(2025, 7, 28),
    date(2025, 7, 29), date(2025, 8, 30), date(2025, 10, 8),
    date(2025, 11, 1), date(2025, 12, 8), date(2025, 12, 25),
    date(2026, 1, 1),  date(2026, 4, 2),  date(2026, 4, 3),
    date(2026, 5, 1),  date(2026, 6, 29), date(2026, 7, 28),
    date(2026, 7, 29), date(2026, 8, 30), date(2026, 10, 8),
    date(2026, 11, 1), date(2026, 12, 8), date(2026, 12, 25),
}

# Mapeo tipo_caso (texto libre) → clave en procesos_legales.json
TIPO_CASO_MAP = {
    "laboral":             "laboral",
    "trabajo":             "laboral",
    "despido":             "laboral",
    "beneficios sociales": "laboral",
    "indemnizacion":       "laboral",
    "indemnización":       "laboral",
    "penal":               "penal",
    "estafa":              "penal",
    "robo":                "penal",
    "hurto":               "penal",
    "delito":              "penal",
    "denuncia":            "penal",
    "alimentos":           "familia_alimentos",
    "familia":             "familia_alimentos",
    "pension":             "familia_alimentos",
    "pensión":             "familia_alimentos",
    "divorcio":            "familia_alimentos",
    "tenencia":            "familia_alimentos",
    "civil":               "civil_desalojo",
    "desalojo":            "civil_desalojo",
    "arrendamiento":       "civil_desalojo",
    "falta de pago":       "civil_desalojo",
    "cobro":               "civil_desalojo",
}

# Mapeo estado Minka → id de etapa procesal
# Usa la etapa más conservadora (la más temprana posible)
ESTADO_A_ETAPA = {
    # Laboral
    "nuevo":               "presentacion_de_la_demanda",
    "en_tramite":          "admision_de_la_demanda",
    "en_audiencia":        "audiencia_de_conciliacion",
    "pendiente_documento": "calificacion_de_la_demanda",
    "en_revision":         "contestacion_de_la_demanda",
    "en_apelacion":        "recurso_de_apelacion",
    "resuelto":            "sentencia",
    "archivado":           "ejecucion_de_sentencia",
}

# Por tipo de proceso, override específico del mapeo estado→etapa
ESTADO_A_ETAPA_POR_PROCESO = {
    "penal": {
        "nuevo":               "denuncia_penal",
        "en_tramite":          "diligencias_preliminares",
        "en_audiencia":        "juicio_oral",
        "pendiente_documento": "formalizacion_de_la_investigacion_preparatoria",
        "en_revision":         "conclusion_de_la_investigacion_preparatoria",
        "en_apelacion":        "recurso_de_apelacion_de_sentencia",
        "resuelto":            "sentencia",
        "archivado":           "ejecucion_de_sentencia",
    },
    "familia_alimentos": {
        "nuevo":               "presentacion_de_la_demanda",
        "en_tramite":          "admision_de_la_demanda_y_asignacion_anticipada",
        "en_audiencia":        "audiencia_unica",
        "pendiente_documento": "calificacion_de_la_demanda",
        "en_revision":         "contestacion_de_la_demanda",
        "en_apelacion":        "recurso_de_apelacion",
        "resuelto":            "sentencia",
        "archivado":           "ejecucion_de_sentencia",
    },
    "civil_desalojo": {
        "nuevo":               "presentacion_de_la_demanda",
        "en_tramite":          "admision_de_la_demanda",
        "en_audiencia":        "audiencia_unica",
        "pendiente_documento": "subsanacion_de_la_demanda",
        "en_revision":         "contestacion_de_la_demanda",
        "en_apelacion":        "recurso_de_apelacion",
        "resuelto":            "sentencia",
        "archivado":           "ejecucion_de_sentencia_y_lanzamiento",
    },
}


def _cargar_knowledge() -> dict:
    base = os.path.join(os.path.dirname(__file__), "..", "knowledge", "procesos_legales.json")
    path = os.path.normpath(base)
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _detectar_tipo_proceso(tipo_caso: str) -> str | None:
    if not tipo_caso:
        return None
    tc = tipo_caso.lower().strip()
    for keyword, proceso in TIPO_CASO_MAP.items():
        if keyword in tc:
            return proceso
    return None


def _sumar_dias_habiles(desde: date, dias: int) -> date:
    resultado = desde
    sumados = 0
    while sumados < dias:
        resultado += timedelta(days=1)
        if resultado.weekday() < 5 and resultado not in FERIADOS_PERU:
            sumados += 1
    return resultado


def _sumar_dias_calendario(desde: date, dias: int) -> date:
    return desde + timedelta(days=dias)


def generar_consejo_procesal(caso: dict) -> dict:
    """
    Dado un caso de Minka, devuelve el consejo procesal completo:
    - Siguiente etapa
    - Plazo legal
    - Fecha límite sugerida
    - Documentos a preparar
    - Norma aplicable
    """
    tipo_caso    = caso.get("tipo_caso", "")
    estado       = caso.get("estado", "nuevo")
    proxima_fecha_actual = caso.get("proxima_fecha")

    tipo_proceso = _detectar_tipo_proceso(tipo_caso)
    if not tipo_proceso:
        return {
            "tiene_consejo": False,
            "motivo": f"Tipo de caso '{tipo_caso}' no reconocido. Tipos soportados: Laboral, Penal, Alimentos, Civil/Desalojo."
        }

    knowledge = _cargar_knowledge()
    if not knowledge:
        return {"tiene_consejo": False, "motivo": "Base de conocimiento no disponible."}

    proceso = knowledge.get("procesos", {}).get(tipo_proceso)
    if not proceso:
        return {"tiene_consejo": False, "motivo": f"Proceso '{tipo_proceso}' no encontrado en la base de conocimiento."}

    # Obtener ID de etapa actual según tipo de proceso
    mapa_estado = ESTADO_A_ETAPA_POR_PROCESO.get(tipo_proceso, ESTADO_A_ETAPA)
    etapa_actual_id = mapa_estado.get(estado, "presentacion_de_la_demanda")

    # Buscar etapa actual y la siguiente en la cadena
    etapas = {e["id"]: e for e in proceso["etapas"]}
    etapa_actual = etapas.get(etapa_actual_id)
    if not etapa_actual:
        return {"tiene_consejo": False, "motivo": "No se pudo identificar la etapa actual del proceso."}

    siguiente_id = etapa_actual.get("etapa_siguiente")
    etapa_siguiente = etapas.get(siguiente_id) if siguiente_id else None

    if not etapa_siguiente:
        return {
            "tiene_consejo": True,
            "tipo_proceso":  proceso["nombre"],
            "etapa_actual":  etapa_actual["etapa"],
            "mensaje":       "El proceso ha llegado a su etapa final. No hay etapas procesales pendientes.",
            "documentos_requeridos": [],
        }

    # Calcular fecha límite
    desde = date.today()
    if proxima_fecha_actual:
        try:
            desde = date.fromisoformat(proxima_fecha_actual)
        except ValueError:
            pass

    fecha_sugerida = None
    if etapa_siguiente.get("plazo_dias"):
        tipo_p = etapa_siguiente.get("tipo_plazo", "hábiles")
        if tipo_p == "hábiles":
            fecha_sugerida = _sumar_dias_habiles(desde, etapa_siguiente["plazo_dias"])
        else:
            fecha_sugerida = _sumar_dias_calendario(desde, etapa_siguiente["plazo_dias"])

    advertencia = None
    if fecha_sugerida and fecha_sugerida < date.today():
        advertencia = "⚠️ El plazo calculado ya venció. Verificar urgentemente el estado del expediente."

    plazo_desc = ""
    if etapa_siguiente.get("plazo_dias"):
        plazo_desc = f"{etapa_siguiente['plazo_dias']} días {etapa_siguiente.get('tipo_plazo', 'hábiles')}"

    return {
        "tiene_consejo":             True,
        "tipo_proceso":              proceso["nombre"],
        "norma_base":                proceso.get("norma_base", ""),
        "etapa_actual_id":           etapa_actual_id,
        "etapa_actual":              etapa_actual["etapa"],
        "siguiente_etapa_id":        siguiente_id,
        "siguiente_etapa":           etapa_siguiente["etapa"],
        "siguiente_descripcion":     etapa_siguiente["descripcion"],
        "plazo_descripcion":         plazo_desc,
        "proxima_fecha_sugerida":    fecha_sugerida.isoformat() if fecha_sugerida else None,
        "documentos_requeridos":     etapa_siguiente.get("documentos_requeridos", []),
        "norma":                     etapa_siguiente.get("norma", ""),
        "notas":                     etapa_siguiente.get("notas", ""),
        "advertencia":               advertencia,
    }
