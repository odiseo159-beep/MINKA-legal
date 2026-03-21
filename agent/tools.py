# agent/tools.py — Herramientas del agente Katia — Jorkat Soluciones Sanitarias
# Generado por AgentKit

"""
Herramientas específicas del negocio Jorkat.
Cubren el ciclo completo de venta: diagnóstico, cotización, pedido,
confirmación de pago y soporte post-venta.
"""

import os
import yaml
import logging
from datetime import datetime

logger = logging.getLogger("agentkit")


def cargar_info_negocio() -> dict:
    """Carga la información del negocio desde business.yaml."""
    try:
        with open("config/business.yaml", "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        logger.error("config/business.yaml no encontrado")
        return {}


def obtener_horario() -> dict:
    """Retorna el horario de atención del negocio y si está abierto ahora."""
    info = cargar_info_negocio()
    horario = info.get("negocio", {}).get("horario", "Lunes a Viernes 8am a 10pm, Sábados 8am a 6pm")

    ahora = datetime.now()
    dia_semana = ahora.weekday()  # 0=lunes, 6=domingo
    hora = ahora.hour

    # Lunes a Viernes: 8am a 10pm
    if 0 <= dia_semana <= 4:
        esta_abierto = 8 <= hora < 22
    # Sábado: 8am a 6pm
    elif dia_semana == 5:
        esta_abierto = 8 <= hora < 18
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


# ══════════════════════════════════════════════════════════════
# HERRAMIENTAS DEL CICLO DE VENTA JORKAT
# ══════════════════════════════════════════════════════════════

# Catálogo de productos con precios
CATALOGO = {
    # Sanitarios nacionales (soles, inc. IGV)
    "sanitario_estandar":              {"nombre": "Sanitario Modelo Estándar",                          "precio": 1750,  "moneda": "PEN"},
    "sanitario_ejecutivo_gravedad":    {"nombre": "Sanitario Ejecutivo con Lavamanos de Gravedad",       "precio": 2100,  "moneda": "PEN"},
    "sanitario_ejecutivo_bomba":       {"nombre": "Sanitario Ejecutivo con Lavamanos de Bomba de Pie",   "precio": 2300,  "moneda": "PEN"},
    "sanitario_taza_movil":            {"nombre": "Sanitario Modelo Taza Móvil",                        "precio": 1650,  "moneda": "PEN"},
    "sanitario_conexion_red":          {"nombre": "Sanitario Modelo Conexión a Red",                    "precio": 1800,  "moneda": "PEN"},
    "sanitario_valvula_evacuacion":    {"nombre": "Sanitario Estándar con Válvula de Evacuación",       "precio": 1850,  "moneda": "PEN"},
    "ducha_portatil":                  {"nombre": "Ducha Portátil",                                     "precio": 1700,  "moneda": "PEN"},
    "caseta_vigilancia":               {"nombre": "Caseta de Vigilancia",                               "precio": 1800,  "moneda": "PEN"},
    "lavamanos_multiple_grande":       {"nombre": "Lavamanos Múltiple Grande (160L)",                   "precio": 1400,  "moneda": "PEN"},
    "lavamanos_multiple_chico":        {"nombre": "Lavamanos Múltiple Chico (80L)",                     "precio": 1200,  "moneda": "PEN"},
    "lavamanos_1_cano":                {"nombre": "Lavamanos de 1 Caño (60L)",                          "precio": 900,   "moneda": "PEN"},
    # Importados (dólares + IGV)
    "polyjohn_pjn3":                   {"nombre": "Sanitario Importado PolyJohn PJN3",                  "precio": 800,   "moneda": "USD"},
    "lavamanos_interno_pro12":         {"nombre": "Lavamanos Interno Pro 12",                           "precio": 200,   "moneda": "USD"},
    "polyjohn_vip_pjp4":               {"nombre": "Sanitario Importado PolyJohn VIP PJP4",             "precio": 1100,  "moneda": "USD"},
}


def generar_cotizacion(items: list[dict]) -> str:
    """
    Genera una cotización en texto formateado.

    Args:
        items: Lista de dicts con keys 'producto_id' y 'cantidad'
               Ejemplo: [{"producto_id": "sanitario_estandar", "cantidad": 3}]

    Returns:
        Texto formateado de la cotización
    """
    lineas = ["📋 COTIZACIÓN JORKAT\n"]
    total_pen = 0
    total_usd = 0

    for item in items:
        producto = CATALOGO.get(item.get("producto_id", ""))
        if not producto:
            continue
        cantidad = item.get("cantidad", 1)
        subtotal = producto["precio"] * cantidad
        moneda = producto["moneda"]
        simbolo = "S/" if moneda == "PEN" else "$"

        lineas.append(f"• {producto['nombre']}")
        lineas.append(f"  {cantidad} und × {simbolo} {producto['precio']:,} = {simbolo} {subtotal:,}")

        if moneda == "PEN":
            total_pen += subtotal
        else:
            total_usd += subtotal

    lineas.append("")
    if total_pen > 0:
        lineas.append(f"Total en soles: S/ {total_pen:,} (Inc. IGV)")
    if total_usd > 0:
        lineas.append(f"Total en dólares: $ {total_usd:,} + IGV")
    lineas.append("")
    lineas.append("✅ Precio incluye entrega en Lima Metropolitana.")
    lineas.append("📦 Stock disponible para entrega inmediata.")

    return "\n".join(lineas)


def recomendar_cantidad_sanitarios(personas: int, tipo: str = "evento", horas: int = 4) -> dict:
    """
    Calcula la cantidad recomendada de sanitarios según las personas y el tipo de uso.

    Args:
        personas: Número de personas o trabajadores
        tipo: "evento" o "obra"
        horas: Duración del evento en horas (solo aplica para eventos)

    Returns:
        Dict con cantidades recomendadas
    """
    if tipo == "obra":
        # Normativa peruana: 1 sanitario por cada 20-25 trabajadores
        sanitarios = max(1, round(personas / 20))
        lavamanos = max(1, round(sanitarios / 2))
        return {
            "sanitarios": sanitarios,
            "lavamanos": lavamanos,
            "nota": f"Para {personas} trabajadores según normativa peruana (1 por cada 20-25 trabajadores)."
        }
    else:
        # Eventos: 1 por cada 50 (cortos) o 1 por cada 35-40 (largos)
        ratio = 35 if horas >= 4 else 50
        sanitarios = max(1, round(personas / ratio))
        lavamanos = max(1, round(sanitarios / 2))
        return {
            "sanitarios": sanitarios,
            "lavamanos": lavamanos,
            "nota": f"Para {personas} personas en evento de {horas}h (1 por cada {ratio} personas)."
        }


def registrar_pedido(telefono: str, datos_facturacion: dict, items: list[dict], direccion_entrega: str) -> str:
    """
    Registra un pedido en un archivo de log interno.
    En producción, esto debería integrar con un sistema de gestión de pedidos.

    Args:
        telefono: Número del cliente
        datos_facturacion: Dict con ruc/dni, nombre/razon_social, tipo_comprobante
        items: Lista de productos del pedido
        direccion_entrega: Dirección de entrega

    Returns:
        Número de pedido generado
    """
    numero_pedido = f"JK{datetime.now().strftime('%Y%m%d%H%M%S')}"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Construir línea de log del pedido
    log_linea = (
        f"[{timestamp}] PEDIDO {numero_pedido} | "
        f"Tel: {telefono} | "
        f"Facturación: {datos_facturacion} | "
        f"Items: {items} | "
        f"Entrega: {direccion_entrega}\n"
    )

    # Guardar en archivo de log (el equipo humano revisa esto para emitir comprobante)
    os.makedirs("logs", exist_ok=True)
    with open("logs/pedidos.log", "a", encoding="utf-8") as f:
        f.write(log_linea)

    logger.info(f"Pedido registrado: {numero_pedido} — Tel: {telefono}")
    return numero_pedido


def confirmar_pago(numero_pedido: str, telefono: str) -> str:
    """
    Registra la confirmación de recepción de pago.
    Notifica al equipo interno para emisión de comprobante y despacho.

    Args:
        numero_pedido: Número de pedido a confirmar
        telefono: Número del cliente

    Returns:
        Mensaje de confirmación para el cliente
    """
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_linea = (
        f"[{timestamp}] PAGO CONFIRMADO | "
        f"Pedido: {numero_pedido} | "
        f"Tel: {telefono} | "
        f"ACCIÓN: Emitir comprobante + coordinar despacho\n"
    )

    os.makedirs("logs", exist_ok=True)
    with open("logs/pagos.log", "a", encoding="utf-8") as f:
        f.write(log_linea)

    logger.info(f"Pago confirmado para pedido {numero_pedido} — notificado al equipo")
    return numero_pedido
