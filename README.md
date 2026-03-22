# Minka — Asistente Legal AI por WhatsApp

> *Minka: trabajo colectivo en quechua. Abogado + IA + Cliente, unidos.*

**Minka** es un asistente de IA para estudios jurídicos que mantiene informados a los clientes sobre el estado de sus casos legales, 24/7 por WhatsApp. El abogado solo actualiza. Minka se encarga del resto.

---

## El problema

El **72% de los clientes de abogados** sienten que no reciben suficiente comunicación sobre su caso. El abogado está en audiencia, el cliente escribe sin respuesta. Minka resuelve esto.

---

## ¿Qué hace Minka?

### Para el cliente (por WhatsApp)
- Consulta el estado de su caso 24/7
- Recibe respuestas inmediatas y personalizadas
- Sabe qué documentos le faltan presentar
- Recibe notificaciones automáticas cuando su caso es actualizado

### Para el abogado (dashboard web)
- Registra y gestiona casos con CRUD completo
- Sube expedientes en PDF o Word → Claude AI extrae los datos automáticamente
- Cambia el estado del caso con un clic → el cliente recibe WhatsApp al instante
- Consulta el consejo procesal: próxima etapa, plazo legal, documentos a preparar y norma aplicable

---

## Stack técnico

| Capa | Tecnología |
|------|-----------|
| IA | Claude AI (Anthropic) — `claude-sonnet-4-6` |
| Backend | FastAPI + Uvicorn (Python 3.11) |
| Mensajería | WhatsApp vía Whapi.cloud |
| Base de datos | SQLite con volumen persistente |
| Deploy | Docker + Railway |
| Dashboard | HTML/JS estático (Vercel) |

---

## Arquitectura

```
Cliente WhatsApp
      ↓
Whapi.cloud → POST /webhook/messages
      ↓
FastAPI (main.py)
      ↓
providers/whapi.py  →  normaliza mensaje
memory.py           →  historial por cliente
case_tools.py       →  busca caso por teléfono (SQLite)
brain.py            →  Claude AI genera respuesta
legal_advisor.py    →  consejo procesal (base de conocimiento)
      ↓
Respuesta → WhatsApp → Cliente
```

---

## Estructura del proyecto

```
minka-legal/
├── agent/
│   ├── main.py                  # Servidor FastAPI
│   ├── brain.py                 # Conexión con Claude AI
│   ├── memory.py                # Historial de conversaciones (SQLite)
│   ├── cases_db.py              # CRUD de casos legales
│   ├── case_tools.py            # Lookup de casos por teléfono
│   ├── dashboard_api.py         # API REST + notificaciones proactivas
│   ├── document_extractor.py    # Extracción de datos de PDF/DOCX con Claude
│   ├── legal_advisor.py         # Motor de consejo procesal
│   └── static/
│       └── dashboard.html       # Dashboard del abogado
│
├── config/
│   ├── business.yaml            # Datos del estudio jurídico
│   └── prompts.yaml             # System prompt de Minka
│
├── knowledge/
│   ├── asistente_legal_knowledge.txt   # Conocimiento general del bot
│   └── procesos_legales.json           # Base de conocimiento procesal (4 procesos)
│
├── dashboard/
│   └── index.html               # Dashboard para Vercel
│
├── tests/
│   └── test_local.py            # Simulador de chat en terminal
│
├── Dockerfile
├── requirements.txt
└── .env.example
```

---

## Instalación y desarrollo local

### 1. Clonar el repositorio
```bash
git clone https://github.com/odiseo159-beep/MINKA-legal.git
cd minka-legal
```

### 2. Crear entorno virtual e instalar dependencias
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configurar variables de entorno
```bash
cp .env.example .env
# Editar .env con tus credenciales
```

Variables requeridas:
```env
ANTHROPIC_API_KEY=sk-ant-...
WHAPI_TOKEN=tu_token_whapi
WHAPI_API_URL=https://gate.whapi.cloud
DATABASE_PATH=./data/minka.db
```

### 4. Iniciar el servidor
```bash
uvicorn agent.main:app --reload --port 8000
```

### 5. Probar el bot en terminal
```bash
python tests/test_local.py
```

---

## Variables de entorno en Railway

| Variable | Descripción |
|----------|-------------|
| `ANTHROPIC_API_KEY` | API key de Anthropic (Claude) |
| `WHAPI_TOKEN` | Token de Whapi.cloud |
| `WHAPI_API_URL` | URL de la API de Whapi |
| `DATABASE_PATH` | `/app/data/minka.db` |

---

## Base de conocimiento procesal

Minka incluye conocimiento legal para 4 tipos de proceso peruano:

| Proceso | Norma base | Etapas |
|---------|-----------|--------|
| Proceso Ordinario Laboral | Ley N° 29497 | 12 |
| Proceso Penal — Estafa | CPP D.Leg. 957 | 17 |
| Proceso Único de Alimentos | CNA + CPC | 10 |
| Proceso Sumarísimo de Desalojo | CPC Arts. 546° y 585° | 11 |

El motor en `legal_advisor.py` calcula automáticamente:
- La siguiente etapa procesal
- El plazo en días hábiles (con feriados peruanos 2025-2026)
- Los documentos a preparar
- La norma legal aplicable

---

## Reglas del bot

- ✅ Identifica al cliente **solo por número de teléfono**
- ✅ Responde solo con información **registrada en la base de datos**
- ✅ Envía **notificaciones proactivas** cuando el abogado actualiza un caso
- ❌ **Nunca** inventa información
- ❌ **Nunca** da asesoría legal — solo informa el estado
- ❌ **Nunca** comparte notas internas del abogado

---

## Deploy

El proyecto hace deploy automático en Railway al hacer push a `main`:

```bash
git add -A
git commit -m "descripción del cambio"
git push
```

El dashboard en Vercel también se actualiza automáticamente desde la carpeta `/dashboard`.

---

## Autor

**Daniel** — Fundador de [SimplifAI](https://simplifai.pe)

Abogado de formación, ahora enfocado en soluciones de IA para el sector legal peruano.

---

*Minka — Trabajo colectivo para la justicia.*
