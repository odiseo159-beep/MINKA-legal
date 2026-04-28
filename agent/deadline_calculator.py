# deadline_calculator.py — Calculadora de plazos legales en días hábiles
# Minka — Asistente Legal AI para abogados peruanos

import json
import os
from datetime import date, timedelta

_CONFIG_PATH = os.path.join(os.path.dirname(__file__), "..", "config", "feriados_peru.json")

# Nombres de feriados fijos (MM-DD) y variables (YYYY-MM-DD tiene prioridad)
_NOMBRES: dict[str, str] = {
    # Fijos anuales
    "01-01": "Año Nuevo",
    "05-01": "Día del Trabajo",
    "06-07": "Batalla de Arica",
    "06-29": "San Pedro y San Pablo",
    "07-28": "Día de la Independencia Nacional",
    "07-29": "Gran Parada y Desfile Cívico Militar",
    "08-30": "Santa Rosa de Lima",
    "10-08": "Combate de Angamos",
    "11-01": "Día de Todos los Santos",
    "12-08": "Inmaculada Concepción",
    "12-25": "Navidad",
    # Semana Santa (variable — YYYY-MM-DD)
    "2024-04-18": "Jueves Santo",
    "2024-04-19": "Viernes Santo",
    "2025-04-17": "Jueves Santo",
    "2025-04-18": "Viernes Santo",
    "2026-04-02": "Jueves Santo",
    "2026-04-03": "Viernes Santo",
    "2027-03-25": "Jueves Santo",
    "2027-03-26": "Viernes Santo",
}


def get_nombre_feriado(fecha_str: str) -> str:
    """Retorna el nombre del feriado dado 'YYYY-MM-DD'."""
    if fecha_str in _NOMBRES:
        return _NOMBRES[fecha_str]
    return _NOMBRES.get(fecha_str[5:], "Feriado nacional")


def cargar_feriados() -> set:
    """Carga los feriados desde config/feriados_peru.json y retorna un set de strings 'YYYY-MM-DD'."""
    path = os.path.abspath(_CONFIG_PATH)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        raw = data.get("feriados", [])
        # Soporta ambos formatos: lista de strings o dict {fecha: nombre}
        if isinstance(raw, dict):
            return set(raw.keys())
        return set(raw)
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def es_dia_habil(fecha: date, feriados: set | None = None) -> bool:
    """Retorna True si la fecha es lunes-viernes y no es feriado peruano."""
    if fecha.weekday() >= 5:
        return False
    f = feriados if feriados is not None else cargar_feriados()
    return fecha.isoformat() not in f


def calcular_plazo_completo(
    fecha_inicio_str: str,
    dias: int,
    tipo: str = "habiles",
) -> dict:
    """
    Calcula el plazo legal completo y retorna todos los campos que el frontend necesita.

    Args:
        fecha_inicio_str: Fecha de inicio en formato 'YYYY-MM-DD'
        dias: Número de días del plazo
        tipo: 'habiles' (excluye fines de semana y feriados) o 'calendario'

    Returns:
        dict con fecha_inicio, fecha_vencimiento, dias_solicitados, tipo,
        dias_habiles, dias_calendario, feriados_excluidos
    """
    feriados = cargar_feriados()
    inicio = date.fromisoformat(fecha_inicio_str)

    if tipo == "habiles":
        # Avanzar N días hábiles
        cursor = inicio
        contados = 0
        while contados < dias:
            cursor += timedelta(days=1)
            if es_dia_habil(cursor, feriados):
                contados += 1
        fin = cursor
    else:
        # Días calendario
        fin = inicio + timedelta(days=dias)

    # Contar días hábiles y calendario en el rango (sin incluir inicio, sí incluir fin)
    dias_calendario = (fin - inicio).days
    dias_habiles_count = 0
    feriados_excluidos = []
    cursor = inicio
    while cursor < fin:
        cursor += timedelta(days=1)
        if cursor.weekday() < 5:  # lunes-viernes
            f_str = cursor.isoformat()
            if f_str in feriados:
                feriados_excluidos.append({
                    "fecha": f_str,
                    "nombre": get_nombre_feriado(f_str),
                })
            else:
                dias_habiles_count += 1

    return {
        "fecha_inicio":       fecha_inicio_str,
        "fecha_vencimiento":  fin.isoformat(),
        "dias_solicitados":   dias,
        "tipo":               tipo,
        "dias_habiles":       dias_habiles_count,
        "dias_calendario":    dias_calendario,
        "feriados_excluidos": feriados_excluidos,
    }


def dias_restantes_habiles(fecha_vencimiento_str: str) -> int:
    """Días hábiles entre hoy y fecha_vencimiento. Negativo si ya venció."""
    hoy = date.today()
    vencimiento = date.fromisoformat(fecha_vencimiento_str)
    feriados = cargar_feriados()

    if hoy == vencimiento:
        return 0

    if hoy < vencimiento:
        cursor, count = hoy, 0
        while cursor < vencimiento:
            cursor += timedelta(days=1)
            if es_dia_habil(cursor, feriados):
                count += 1
        return count
    else:
        cursor, count = vencimiento, 0
        while cursor < hoy:
            cursor += timedelta(days=1)
            if es_dia_habil(cursor, feriados):
                count += 1
        return -count


# Backwards-compatible alias for code that calls calcular_vencimiento(str, int)
def calcular_vencimiento(fecha_inicio_str: str, plazo_dias: int) -> str:
    return calcular_plazo_completo(fecha_inicio_str, plazo_dias, "habiles")["fecha_vencimiento"]
