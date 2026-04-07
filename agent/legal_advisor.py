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
    # Laboral
    "laboral":                  "laboral",
    "trabajo":                  "laboral",
    "despido":                  "laboral",
    "beneficios sociales":      "laboral",
    "indemnizacion":            "laboral",
    "indemnización":            "laboral",
    # Penal — estafa (proceso genérico penal)
    "penal":                    "penal",
    "estafa":                   "penal",
    "fraude":                   "penal",
    "delito":                   "penal",
    "denuncia":                 "penal",
    # Penal — robo
    "robo":                     "penal_robo",
    "robo agravado":            "penal_robo",
    "asalto":                   "penal_robo",
    # Penal — hurto
    "hurto":                    "penal_hurto",
    "hurto agravado":           "penal_hurto",
    "robo menor":               "penal_hurto",
    # Penal — apropiación ilícita
    "apropiacion":              "penal_apropiacion",
    "apropiación":              "penal_apropiacion",
    "apropiacion ilicita":      "penal_apropiacion",
    "apropiación ilícita":      "penal_apropiacion",
    "depositario":              "penal_apropiacion",
    # Penal — omisión asistencia familiar
    "omision familiar":         "penal_omision_familiar",
    "omisión familiar":         "penal_omision_familiar",
    "incumplimiento alimentos": "penal_omision_familiar",
    "no paga pension":          "penal_omision_familiar",
    "no paga pensión":          "penal_omision_familiar",
    # Penal — lesiones
    "lesiones":                 "penal_lesiones",
    "golpes":                   "penal_lesiones",
    "lesiones graves":          "penal_lesiones",
    "lesiones leves":           "penal_lesiones",
    # Penal — violencia familiar
    "violencia familiar":       "penal_violencia_familiar",
    "violencia doméstica":      "penal_violencia_familiar",
    "violencia domestica":      "penal_violencia_familiar",
    "agresion familiar":        "penal_violencia_familiar",
    "agresión familiar":        "penal_violencia_familiar",
    "ley 30364":                "penal_violencia_familiar",
    # Penal — feminicidio
    "feminicidio":              "penal_feminicidio",
    "tentativa feminicidio":    "penal_feminicidio",
    # Penal — violación sexual
    "violacion":                "penal_violacion",
    "violación":                "penal_violacion",
    "violacion sexual":         "penal_violacion",
    "violación sexual":         "penal_violacion",
    "abuso sexual":             "penal_violacion",
    # Penal — homicidio
    "homicidio":                "penal_homicidio",
    "asesinato":                "penal_homicidio",
    "homicidio calificado":     "penal_homicidio",
    "homicidio simple":         "penal_homicidio",
    # Familia — alimentos
    "alimentos":                "familia_alimentos",
    "familia":                  "familia_alimentos",
    "pension":                  "familia_alimentos",
    "pensión":                  "familia_alimentos",
    "pension alimenticia":      "familia_alimentos",
    "pensión alimenticia":      "familia_alimentos",
    # Familia — divorcio
    "divorcio":                 "familia_divorcio",
    "separacion":               "familia_divorcio",
    "separación":               "familia_divorcio",
    "separacion de cuerpos":    "familia_divorcio",
    # Familia — tenencia
    "tenencia":                 "familia_tenencia",
    "custodia":                 "familia_tenencia",
    "regimen de visitas":       "familia_tenencia",
    "régimen de visitas":       "familia_tenencia",
    # Civil — desalojo
    "civil":                    "civil_desalojo",
    "desalojo":                 "civil_desalojo",
    "arrendamiento":            "civil_desalojo",
    "falta de pago":            "civil_desalojo",
    "cobro":                    "civil_desalojo",
    # Civil — sucesiones
    "sucesion":                 "civil_sucesiones",
    "sucesión":                 "civil_sucesiones",
    "herencia":                 "civil_sucesiones",
    "herederos":                "civil_sucesiones",
    "declaratoria herederos":   "civil_sucesiones",
    "testamento":               "civil_sucesiones",
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
    # Penal — robo agravado
    "penal_robo": {
        "nuevo":               "denuncia_robo",
        "en_tramite":          "diligencias_preliminares_robo",
        "pendiente_documento": "formalizacion_investigacion_robo",
        "en_revision":         "etapa_intermedia_robo",
        "en_audiencia":        "juicio_oral_robo",
        "en_apelacion":        "apelacion_sentencia_robo",
        "resuelto":            "sentencia_robo",
        "archivado":           "ejecucion_sentencia_robo",
    },
    # Penal — hurto
    "penal_hurto": {
        "nuevo":               "denuncia_hurto",
        "en_tramite":          "diligencias_preliminares_hurto",
        "pendiente_documento": "formalizacion_investigacion_hurto",
        "en_revision":         "etapa_intermedia_hurto",
        "en_audiencia":        "juicio_oral_hurto",
        "en_apelacion":        "apelacion_sentencia_hurto",
        "resuelto":            "sentencia_hurto",
        "archivado":           "sentencia_hurto",
    },
    # Penal — apropiación ilícita
    "penal_apropiacion": {
        "nuevo":               "denuncia_apropiacion",
        "en_tramite":          "diligencias_preliminares_apropiacion",
        "pendiente_documento": "formalizacion_investigacion_apropiacion",
        "en_revision":         "etapa_intermedia_apropiacion",
        "en_audiencia":        "juicio_oral_apropiacion",
        "resuelto":            "sentencia_apropiacion",
        "archivado":           "sentencia_apropiacion",
        "en_apelacion":        "sentencia_apropiacion",
    },
    # Penal — omisión asistencia familiar
    "penal_omision_familiar": {
        "nuevo":               "requisito_previo_sentencia_civil",
        "en_tramite":          "denuncia_omision_familiar",
        "pendiente_documento": "proceso_inmediato_omision",
        "en_audiencia":        "audiencia_unica_juicio_omision",
        "resuelto":            "sentencia_omision_familiar",
        "archivado":           "ejecucion_pago_omision",
        "en_revision":         "proceso_inmediato_omision",
        "en_apelacion":        "sentencia_omision_familiar",
    },
    # Penal — lesiones
    "penal_lesiones": {
        "nuevo":               "denuncia_lesiones",
        "en_tramite":          "diligencias_preliminares_lesiones",
        "pendiente_documento": "formalizacion_investigacion_lesiones",
        "en_revision":         "etapa_intermedia_lesiones",
        "en_audiencia":        "juicio_oral_lesiones",
        "resuelto":            "sentencia_lesiones",
        "archivado":           "sentencia_lesiones",
        "en_apelacion":        "sentencia_lesiones",
    },
    # Penal — violencia familiar (Ley 30364)
    "penal_violencia_familiar": {
        "nuevo":               "denuncia_violencia_familiar",
        "en_tramite":          "medidas_de_proteccion_vf",
        "pendiente_documento": "proceso_penal_paralelo_vf",
        "en_revision":         "formalizacion_proceso_penal_vf",
        "en_audiencia":        "juicio_oral_vf",
        "resuelto":            "seguimiento_medidas_vf",
        "archivado":           "seguimiento_medidas_vf",
        "en_apelacion":        "seguimiento_medidas_vf",
    },
    # Penal — feminicidio
    "penal_feminicidio": {
        "nuevo":               "flagrancia_medidas_urgentes_feminicidio",
        "en_tramite":          "investigacion_preparatoria_feminicidio",
        "pendiente_documento": "prision_preventiva_feminicidio",
        "en_revision":         "etapa_intermedia_feminicidio",
        "en_audiencia":        "juicio_oral_feminicidio",
        "resuelto":            "sentencia_feminicidio",
        "archivado":           "sentencia_feminicidio",
        "en_apelacion":        "sentencia_feminicidio",
    },
    # Penal — violación sexual
    "penal_violacion": {
        "nuevo":               "denuncia_violacion",
        "en_tramite":          "diligencias_sin_revictimizacion",
        "pendiente_documento": "formalizacion_investigacion_violacion",
        "en_revision":         "etapa_intermedia_violacion",
        "en_audiencia":        "juicio_oral_violacion",
        "resuelto":            "sentencia_violacion",
        "archivado":           "sentencia_violacion",
        "en_apelacion":        "sentencia_violacion",
    },
    # Penal — homicidio
    "penal_homicidio": {
        "nuevo":               "escena_del_crimen_homicidio",
        "en_tramite":          "detencion_formalizacion_homicidio",
        "pendiente_documento": "investigacion_compleja_homicidio",
        "en_revision":         "etapa_intermedia_homicidio",
        "en_audiencia":        "juicio_oral_homicidio",
        "resuelto":            "sentencia_homicidio",
        "archivado":           "sentencia_homicidio",
        "en_apelacion":        "sentencia_homicidio",
    },
    # Familia — divorcio
    "familia_divorcio": {
        "nuevo":               "evaluacion_causal_divorcio",
        "en_tramite":          "admision_traslado_divorcio",
        "pendiente_documento": "demanda_divorcio",
        "en_revision":         "audiencia_conciliacion_divorcio",
        "en_audiencia":        "audiencia_pruebas_divorcio",
        "en_apelacion":        "sentencia_divorcio",
        "resuelto":            "sentencia_divorcio",
        "archivado":           "liquidacion_sociedad_conyugal",
    },
    # Familia — tenencia
    "familia_tenencia": {
        "nuevo":               "demanda_tenencia",
        "en_tramite":          "informe_social_psicologico_tenencia",
        "en_audiencia":        "audiencia_tenencia",
        "resuelto":            "sentencia_tenencia",
        "archivado":           "variacion_tenencia",
        "pendiente_documento": "informe_social_psicologico_tenencia",
        "en_revision":         "audiencia_tenencia",
        "en_apelacion":        "sentencia_tenencia",
    },
    # Civil — sucesiones
    "civil_sucesiones": {
        "nuevo":               "evaluacion_via_sucesion",
        "en_tramite":          "solicitud_sucesion_intestada",
        "pendiente_documento": "publicacion_edictos_sucesion",
        "en_audiencia":        "declaracion_herederos",
        "en_revision":         "inscripcion_sunarp_sucesion",
        "resuelto":            "declaracion_herederos",
        "archivado":           "particion_bienes_sucesion",
        "en_apelacion":        "declaracion_herederos",
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
