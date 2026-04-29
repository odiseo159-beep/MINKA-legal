# agent/feature_flags.py — Feature flags controlados por env vars
#
# Permiten apagar funcionalidades desde Railway sin redeploy de código.

import os


def whapi_enabled() -> bool:
    """Si False, los webhooks de Whapi no procesan mensajes y los endpoints
    de configuración Whapi (verificar/guardar/refresh/desconectar) devuelven
    503. El frontend muestra un banner avisando del mantenimiento.

    Default: True (compat con deploys existentes). Setear `WHAPI_ENABLED=false`
    en Railway para apagar.
    """
    return os.getenv("WHAPI_ENABLED", "true").strip().lower() not in (
        "false",
        "0",
        "no",
        "off",
    )


def destructive_reset_enabled() -> bool:
    """Si True, el endpoint POST /api/admin/reset-data-prueba está accesible
    para hacer cleanup masivo de la BD. Pensado para iteración temprana del
    producto (modo dev). En producción con clientes reales, MANTENER OFF para
    evitar que un admin borre data legítima por error.

    Default: False. Setear `ALLOW_DESTRUCTIVE_RESET=true` en Railway sólo
    durante ventanas controladas de cleanup.
    """
    return os.getenv("ALLOW_DESTRUCTIVE_RESET", "false").strip().lower() in (
        "true",
        "1",
        "yes",
        "on",
    )


def whapi_status_message() -> str | None:
    """Mensaje opcional que el frontend muestra cuando Whapi está deshabilitado.
    Configurable vía WHAPI_DISABLED_MESSAGE para personalizar (ej. "vuelve el viernes").
    """
    if whapi_enabled():
        return None
    return os.getenv(
        "WHAPI_DISABLED_MESSAGE",
        "Integración con WhatsApp temporalmente deshabilitada por mantenimiento.",
    )
