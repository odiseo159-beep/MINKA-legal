# dashboard_api.py — Endpoints API para el dashboard del abogado
# Importar y registrar estas rutas en main.py

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from agent.cases_db import (
    init_cases_db, crear_caso, obtener_caso, buscar_por_telefono,
    listar_casos, actualizar_caso, eliminar_caso
)
import os

router = APIRouter()


# ============================================================
# API REST — CRUD de Casos
# ============================================================

@router.get("/api/casos")
async def api_listar_casos(estado: str = None):
    """Lista todos los casos, opcionalmente filtrados por estado."""
    casos = listar_casos(filtro_estado=estado)
    return {"casos": casos, "total": len(casos)}


@router.get("/api/casos/{caso_id}")
async def api_obtener_caso(caso_id: int):
    """Obtiene un caso específico por ID."""
    caso = obtener_caso(caso_id)
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    return caso


@router.post("/api/casos")
async def api_crear_caso(request: Request):
    """Crea un nuevo caso."""
    data = await request.json()
    
    # Validaciones básicas
    if not data.get("nombre_cliente"):
        raise HTTPException(status_code=400, detail="El nombre del cliente es obligatorio")
    if not data.get("telefono"):
        raise HTTPException(status_code=400, detail="El teléfono es obligatorio")
    
    caso = crear_caso(data)
    return {"mensaje": "Caso creado exitosamente", "caso": caso}


@router.put("/api/casos/{caso_id}")
async def api_actualizar_caso(caso_id: int, request: Request):
    """Actualiza un caso existente."""
    data = await request.json()
    caso = actualizar_caso(caso_id, data)
    if not caso:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    return {"mensaje": "Caso actualizado", "caso": caso}


@router.delete("/api/casos/{caso_id}")
async def api_eliminar_caso(caso_id: int):
    """Elimina un caso."""
    eliminado = eliminar_caso(caso_id)
    if not eliminado:
        raise HTTPException(status_code=404, detail="Caso no encontrado")
    return {"mensaje": "Caso eliminado"}


@router.get("/api/casos/buscar/{telefono}")
async def api_buscar_por_telefono(telefono: str):
    """Busca casos por número de teléfono."""
    casos = buscar_por_telefono(telefono)
    return {"casos": casos, "total": len(casos)}


# ============================================================
# Dashboard HTML — Interfaz del abogado
# ============================================================

@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    """Sirve el dashboard HTML del abogado."""
    dashboard_path = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>Dashboard no encontrado</h1>", status_code=404)
