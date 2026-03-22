# document_extractor.py
# Extrae datos de un expediente legal (PDF o DOCX) usando Claude API
# Devuelve los campos encontrados + lista de campos requeridos faltantes

import os
import base64
import json
import re
import anthropic

CAMPOS_EXTRAIBLES = [
    "nombre_cliente", "telefono", "expediente", "tipo_caso",
    "abogado_asignado", "documentos_pendientes",
    "proxima_fecha", "proxima_accion",
]

CAMPOS_REQUERIDOS = ["nombre_cliente", "telefono"]

PROMPT_EXTRACCION = """Eres un asistente especializado en extracción de datos de documentos legales peruanos.

Analiza el documento adjunto y extrae ÚNICAMENTE los siguientes campos si los encuentras de forma EXPLÍCITA:

- nombre_cliente: Nombre completo del cliente, demandante o denunciante principal
- telefono: Número de teléfono del cliente (solo los 9 dígitos, sin +51 ni 51)
- expediente: Número de expediente judicial o carpeta fiscal (ej: 01234-2025-0-1801-JR-LA-09)
- tipo_caso: Materia o tipo del proceso (ej: Laboral, Penal - Estafa, Alimentos, Civil - Desalojo)
- abogado_asignado: Nombre del abogado o letrado que patrocina al cliente
- documentos_pendientes: Documentos que el juzgado o fiscalía ha requerido presentar o subsanar
- proxima_fecha: Fecha de la próxima audiencia, diligencia o plazo que aparezca EXPLÍCITAMENTE en el documento. Formato YYYY-MM-DD. Solo si está escrita con claridad (ej: "15 de abril de 2026" → "2026-04-15")
- proxima_accion: Descripción breve de la próxima acción procesal mencionada en el documento (ej: "Audiencia de Conciliación", "Contestar demanda", "Subsanar demanda en 5 días hábiles")

REGLAS IMPORTANTES:
1. Solo extrae datos EXPLÍCITAMENTE escritos en el documento. NO inventes ni inferras plazos.
2. Para proxima_fecha: usa SOLO fechas ya fijadas/programadas que aparezcan en el texto. Si hay varias, usa la más próxima futura.
3. Para proxima_accion: describe la acción mencionada en el documento, no la que tú creas que debería hacerse.
4. Para el teléfono, devuelve solo los 9 dígitos (sin +51 ni 51).
5. Si un campo no está en el documento, omítelo completamente del JSON.
6. Devuelve ÚNICAMENTE un objeto JSON válido, sin texto adicional, sin markdown, sin explicaciones.

Formato exacto (incluye solo los campos que encontraste):
{"nombre_cliente": "...", "telefono": "...", "expediente": "...", "tipo_caso": "...", "abogado_asignado": "...", "documentos_pendientes": "...", "proxima_fecha": "YYYY-MM-DD", "proxima_accion": "..."}

Si no encuentras ningún campo, devuelve: {}
"""


def _leer_docx_como_texto(contenido_bytes: bytes) -> str:
    try:
        import docx
        import io
        doc = docx.Document(io.BytesIO(contenido_bytes))
        parrafos = [p.text for p in doc.paragraphs if p.text.strip()]
        for tabla in doc.tables:
            for fila in tabla.rows:
                for celda in fila.cells:
                    if celda.text.strip():
                        parrafos.append(celda.text.strip())
        return "\n".join(parrafos)
    except Exception as e:
        raise ValueError(f"No se pudo leer el archivo DOCX: {e}")


def extraer_datos_documento(contenido_bytes: bytes, nombre_archivo: str, content_type: str) -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY no configurada")

    cliente = anthropic.Anthropic(api_key=api_key)
    extension = nombre_archivo.lower().rsplit(".", 1)[-1] if "." in nombre_archivo else ""

    # PDF → documents API nativo de Claude
    if extension == "pdf" or "pdf" in content_type:
        b64 = base64.standard_b64encode(contenido_bytes).decode("utf-8")
        mensaje = cliente.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
                    },
                    {"type": "text", "text": PROMPT_EXTRACCION},
                ],
            }],
        )

    # DOCX → extraer texto y mandar como texto plano
    elif extension in ("docx", "doc") or "word" in content_type:
        texto = _leer_docx_como_texto(contenido_bytes)
        if not texto.strip():
            raise ValueError("El documento Word está vacío o no tiene texto extraíble.")
        mensaje = cliente.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": f"{PROMPT_EXTRACCION}\n\n--- CONTENIDO DEL DOCUMENTO ---\n{texto[:8000]}",
            }],
        )
    else:
        raise ValueError(f"Formato no soportado: '{extension}'. Solo se aceptan PDF y DOCX.")

    # Parsear respuesta JSON
    texto_respuesta = mensaje.content[0].text.strip()

    if "```" in texto_respuesta:
        texto_respuesta = texto_respuesta.split("```")[1]
        if texto_respuesta.startswith("json"):
            texto_respuesta = texto_respuesta[4:]

    try:
        campos_raw = json.loads(texto_respuesta)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", texto_respuesta, re.DOTALL)
        campos_raw = json.loads(match.group()) if match else {}

    # Filtrar solo campos permitidos y limpiar vacíos
    campos = {
        k: str(v).strip()
        for k, v in campos_raw.items()
        if k in CAMPOS_EXTRAIBLES and v and str(v).strip()
    }

    faltantes = [c for c in CAMPOS_REQUERIDOS if c not in campos or not campos[c]]

    advertencias = []
    if faltantes:
        labels = {"nombre_cliente": "Nombre del cliente", "telefono": "Teléfono"}
        advertencias.append(
            f"Campos obligatorios no encontrados: {', '.join(labels.get(f, f) for f in faltantes)}. Debe completarlos manualmente."
        )
    advertencias.append("Próxima fecha y próxima acción deben completarse manualmente (requieren criterio jurídico).")

    return {
        "campos": campos,
        "faltantes": faltantes,
        "advertencias": advertencias,
        "archivo": nombre_archivo,
        "campos_encontrados": len(campos),
    }
