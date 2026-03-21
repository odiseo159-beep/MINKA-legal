# GUÍA DE INTEGRACIÓN — Asistente Legal AI
# Instrucciones para integrar el dashboard y la consulta de casos al bot existente

## ARCHIVOS NUEVOS A AGREGAR

```
agent/
├── cases_db.py          ← Modelo de datos (tabla de casos en SQLite)
├── case_tools.py        ← Herramientas del bot para consultar casos
├── dashboard_api.py     ← Endpoints API + ruta del dashboard
└── static/
    └── dashboard.html   ← Interfaz web del abogado

knowledge/
└── asistente_legal_knowledge.txt  ← Knowledge del bot legal
```

## CAMBIOS EN ARCHIVOS EXISTENTES

### 1. main.py — Registrar las nuevas rutas y la base de datos

Agregar estos imports al inicio:
```python
from fastapi.staticfiles import StaticFiles
from agent.cases_db import init_cases_db
from agent.dashboard_api import router as dashboard_router
```

En la función de startup (o donde se inicializa la app), agregar:
```python
init_cases_db()
```

Registrar el router del dashboard:
```python
app.include_router(dashboard_router)
```

Montar archivos estáticos (si se necesitan assets adicionales):
```python
app.mount("/static", StaticFiles(directory="agent/static"), name="static")
```

### 2. brain.py — Inyectar contexto del caso en el prompt

En la función que genera la respuesta con Claude, ANTES de enviar el mensaje,
buscar la información del caso del cliente y agregarla al system prompt:

```python
from agent.case_tools import generar_contexto_para_bot

# Obtener el teléfono del cliente del mensaje entrante
telefono_cliente = mensaje.telefono  # o como esté estructurado

# Generar contexto del caso
contexto_caso = generar_contexto_para_bot(telefono_cliente)

# Agregar al system prompt
system_prompt = system_prompt_base + "\n\n" + contexto_caso
```

### 3. requirements.txt — No se necesitan dependencias nuevas
Todo usa SQLite (incluido en Python) y FastAPI (ya instalado).

### 4. knowledge/ — Reemplazar archivos
Reemplazar los archivos de knowledge de Jorkat con asistente_legal_knowledge.txt

### 5. config/prompts.yaml — Actualizar el system prompt
Cambiar la personalidad de "Katia de Jorkat" a la del asistente legal.

## FLUJO COMPLETO

```
ABOGADO                                    CLIENTE
   |                                          |
   | Entra a /dashboard                       |
   | Agrega caso con teléfono del cliente     |
   |                                          |
   |              [SQLite: tabla casos]        |
   |                                          |
   |                                    Escribe por WhatsApp
   |                                          |
   |                          Whapi.cloud → /webhook
   |                                          |
   |                          brain.py busca caso por teléfono
   |                          case_tools.py → SQLite
   |                                          |
   |                          Claude genera respuesta con info del caso
   |                                          |
   |                          Respuesta → WhatsApp → Cliente
```

## SEGURIDAD DEL DASHBOARD

Para la hackathon, el dashboard está abierto (sin autenticación).
Para producción, agregar:
- Autenticación básica (usuario/contraseña)
- O proteger con un token en la URL: /dashboard?token=xyz
- O usar un middleware de autenticación en FastAPI

## PRUEBAS RÁPIDAS

1. Iniciar el servidor: `uvicorn agent.main:app --reload --port 8000`
2. Abrir dashboard: http://localhost:8000/dashboard
3. Crear un caso de prueba con tu número de teléfono
4. Probar el bot en terminal: `python tests/test_local.py`
5. Escribir "¿Cómo va mi caso?" y verificar que responda con la info del caso
