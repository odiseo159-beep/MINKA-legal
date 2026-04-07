# deadline_calculator.py — Calculadora de plazos legales en días hábiles
# Minka — Asistente Legal AI para abogados peruanos
#
# Feriados cargados desde config/feriados_peru.json

import json
import os
from datetime import date, timedelta

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "feriados_peru.json")


def cargar_feriados() -> set:
    """Carga los feriados desde config/feriados_peru.json y retorna un set de strings 'YYYY-MM-DD'."""
    path = os.path.abspath(_CONFIG_PATH)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return set(data.get("feriados", []))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def es_dia_habil(fecha: date) -> bool:
    """Retorna True si la fecha es un día de lunes a viernes y no es feriado peruano."""
    if fecha.weekday() >= 5:  # 5=sábado, 6=domingo
        return False
    feriados = cargar_feriados()
    return fecha.isoformat() not in feriados


def calcular_dias_habiles(fecha_inicio: date, dias: int) -> date:
    """Avanza fecha_inicio exactamente `dias` días hábiles y retorna la fecha resultante."""
    fecha = fecha_inicio
    dias_contados = 0
    while dias_contados < dias:
        fecha += timedelta(days=1)
        if es_dia_habil(fecha):
            dias_contados += 1
    return fecha


def calcular_vencimiento(fecha_inicio_str: str, plazo_dias: int) -> str:
    """
    Dado un fecha de inicio (string 'YYYY-MM-DD') y un plazo en días hábiles,
    retorna la fecha de vencimiento como 'YYYY-MM-DD'.
    """
    fecha_inicio = date.fromisoformat(fecha_inicio_str)
    fecha_vencimiento = calcular_dias_habiles(fecha_inicio, plazo_dias)
    return fecha_vencimiento.isoformat()


def dias_restantes_habiles(fecha_vencimiento_str: str) -> int:
    """
    Retorna el número de días hábiles que restan entre hoy y la fecha de vencimiento.
    Retorna un valor negativo si la fecha ya venció.
    """
    hoy = date.today()
    vencimiento = date.fromisoformat(fecha_vencimiento_str)

    if hoy == vencimiento:
        return 0

    # Contar días hábiles entre hoy y vencimiento (puede ser negativo)
    if hoy < vencimiento:
        # Días restantes (positivo)
        cursor = hoy
        count = 0
        while cursor < vencimiento:
            cursor += timedelta(days=1)
            if es_dia_habil(cursor):
                count += 1
        return count
    else:
        # Ya venció (negativo)
        cursor = vencimiento
        count = 0
        while cursor < hoy:
            cursor += timedelta(days=1)
            if es_dia_habil(cursor):
                count += 1
        return -count
