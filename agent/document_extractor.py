# document_extractor.py
# Extrae datos de un expediente legal (PDF o DOCX) usando Claude API
# Devuelve los campos encontrados + lista de campos requeridos faltantes

import os
import base64
import json
import re
import anthropic

CAMPOS_EXTRAIBLES = [
    "nombre_cliente", "telefono", "expediente",
    "tipo_caso", "documentos_pendientes", "abogado_asignado",
]

CAMPOS_REQUERIDOS = ["nombre_cliente", "telefono"]

PROMPT_EXTRACCION = """Eres un asistente especializado en extracción de datos de documentos legales peruanos.

Analiza el documento adjunto y extrae ÚNICAMENTE los siguientes campos si los encuentras con certeza:

- nombre_cliente: Nombre completo del cliente o demandante/denunciante
- telefono: Número de teléfono del cliente (solo dígitos, sin prefijo +51)
- expediente: Número de expediente judicial o administrativo
- tipo_caso: Tipo o materia del caso (ej: Laboral, Civil, Penal, Familia, Administrativo)
- documentos_pendientes: Documentos que se mencionan como pendientes de presentar o adjuntar
- abogado_asignado: Nombre del abogado, letrado o defensor asignado al caso

REGLAS IMPORTANTES:
1. Solo extrae datos EXPLÍCITAMENTE en el documento. NO inventes ni inferras.
2. Si un campo no está claramente en el documento, omítelo del JSON.
3. Para el teléfono, devuelve solo los 9 dígitos (sin +51 ni 51).
4. NO extraigas fechas de audiencia ni próximas acciones — requieren criterio del abogado.
5. Devuelve ÚNICAMENTE un objeto JSON válido, sin texto adicional, sin markdown.

Formato exacto:
{"nombre_cliente": "...", "telefono": "...", "expediente": "...", "tipo_caso": "...", "documentos_pendientes": "...", "abogado_asignado": "..."}

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
