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

PRIMERO evalúa el documento:
- legible: true si el texto es claramente legible, false si está borroso, torcido o ilegible
- es_legal: true si es un documento legal peruano (denuncia, demanda, resolución, oficio, contrato, etc.), false si no lo es

Si legible=false o es_legal=false, devuelve SOLO:
{"legible": false, "es_legal": false, "rejection_reason": "descripción breve del problema"}

Si pasa la validación, extrae los siguientes campos si los encuentras de forma EXPLÍCITA:
- nombre_cliente: Nombre completo del cliente, demandante o denunciante principal
- telefono: Número de teléfono del cliente (solo los 9 dígitos, sin +51 ni 51)
- expediente: Número de expediente judicial o carpeta fiscal
- tipo_caso: Materia o tipo del proceso (ej: Laboral, Penal - Estafa, Alimentos)
- abogado_asignado: Nombre del abogado que patrocina al cliente
- documentos_pendientes: Documentos que el juzgado ha requerido presentar
- proxima_fecha: Fecha de próxima audiencia. Formato YYYY-MM-DD.
- proxima_accion: Descripción breve de la próxima acción procesal

REGLAS:
1. Solo extrae datos EXPLÍCITAMENTE escritos. NO inventes ni inferras.
2. Si un campo no está en el documento, omítelo del JSON.
3. Devuelve ÚNICAMENTE un objeto JSON válido, sin texto adicional.

Formato cuando pasa validación (incluye solo campos encontrados):
{"legible": true, "es_legal": true, "nombre_cliente": "...", "telefono": "...", ...}
"""

PROMPT_RESUMEN_ESTRUCTURADO = """Eres un asistente legal especializado en derecho peruano.
Analiza el documento adjunto y extrae ÚNICAMENTE información que aparezca de forma EXPLÍCITA.
NO inventes, NO asumas, NO infieras lo que no está escrito.

Devuelve ÚNICAMENTE un objeto JSON válido con esta estructura exacta (omite campos null):
{
  "tipo_documento": "denuncia_penal|demanda_civil|resolucion|oficio|contrato|audiencia|otro",
  "partes": {
    "demandante_denunciante": "nombre completo o null",
    "demandado_denunciado": "nombre completo o null",
    "fiscal": "nombre o null",
    "juez": "nombre o null"
  },
  "hechos_clave": "resumen de los hechos principales en 2 a 3 párrafos concisos",
  "pretension": "qué se reclama o solicita (1 oración)",
  "fundamentos_juridicos": ["art. X del CP", "..."],
  "pruebas_evidencia": ["prueba 1", "prueba 2"],
  "fechas_importantes": [{"fecha": "YYYY-MM-DD o texto si no hay formato claro", "descripcion": "..."}],
  "montos": [{"monto": "S/ X", "concepto": "..."}],
  "resolucion_fallo": "resolución o fallo si existe, o null",
  "expediente": "número exacto si existe, o null"
}

Reglas:
1. Solo extrae lo explícitamente escrito. Si un campo no está, omítelo del JSON.
2. hechos_clave: incluye quién, qué, cuándo, dónde, cómo. Máximo 300 palabras.
3. Si el documento es muy corto o ilegible, devuelve {"tipo_documento": "otro", "hechos_clave": "Documento sin contenido procesable"}.
4. Sin texto adicional, sin markdown, sin bloques de código. Solo el JSON.
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
    # Imagen (JPG, PNG, WEBP) → Vision API
    elif extension in ("jpg", "jpeg", "png", "webp"):
        # Normalizar media type
        if extension in ("jpg", "jpeg"):
            media_type = "image/jpeg"
        elif extension == "png":
            media_type = "image/png"
        elif extension == "webp":
            media_type = "image/webp"
        else:
            media_type = "image/jpeg"
        b64 = base64.standard_b64encode(contenido_bytes).decode("utf-8")
        mensaje = cliente.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": b64},
                    },
                    {"type": "text", "text": PROMPT_EXTRACCION},
                ],
            }],
        )
    else:
        raise ValueError(
            f"Formato no soportado: '{extension}'. Solo se aceptan PDF, DOCX, JPG, PNG y WEBP."
        )

    # Parsear respuesta JSON
    texto_respuesta = mensaje.content[0].text.strip()

    if "```" in texto_respuesta:
        match = re.search(r"```(?:json)?\s*(.*?)\s*```", texto_respuesta, re.DOTALL)
        if match:
            texto_respuesta = match.group(1)

    try:
        campos_raw = json.loads(texto_respuesta)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", texto_respuesta, re.DOTALL)
        campos_raw = json.loads(match.group()) if match else {}

    # Extraer campos de validación (presentes en imágenes; para PDF/DOCX default True)
    legible = campos_raw.get("legible", True)
    es_legal = campos_raw.get("es_legal", True)
    rejection_reason = campos_raw.get("rejection_reason", None)

    # Documento rechazado por validación (ilegible o no legal)
    if legible is False or es_legal is False:
        return {
            "campos": {},
            "faltantes": CAMPOS_REQUERIDOS,
            "advertencias": [rejection_reason or "Documento rechazado por validación."],
            "legible": False,
            "es_legal": es_legal if es_legal is not None else True,
            "rejection_reason": rejection_reason,
            "archivo": nombre_archivo,
            "campos_encontrados": 0,
        }

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
        "legible": True,
        "es_legal": True,
        "rejection_reason": None,
        "archivo": nombre_archivo,
        "campos_encontrados": len(campos),
    }


def extraer_resumen_estructurado(
    contenido_bytes: bytes,
    nombre_archivo: str,
    content_type: str,
) -> tuple[dict, str]:
    """
    Extrae un resumen estructurado del documento usando Claude Haiku.
    Retorna (resumen_dict, texto_relevante_str).

    - resumen_dict: JSON con partes, hechos, pruebas, fechas, etc.
    - texto_relevante_str: concatenación de campos textuales para búsqueda BM25.
    """
    import json as _json

    ext = nombre_archivo.lower().rsplit(".", 1)[-1] if "." in nombre_archivo else ""

    # Preparar contenido según tipo de archivo
    if ext == "docx":
        texto = _leer_docx_como_texto(contenido_bytes)
        content_for_claude = [
            {
                "type": "text",
                "text": f"Documento legal (DOCX convertido a texto):\n\n{texto[:12000]}",
            }
        ]
    elif content_type.startswith("image/") or ext in ("jpg", "jpeg", "png"):
        b64 = base64.standard_b64encode(contenido_bytes).decode("utf-8")
        content_for_claude = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": content_type, "data": b64},
            },
            {"type": "text", "text": "Analiza este documento legal."},
        ]
    else:
        # PDF: enviar como base64 document
        b64 = base64.standard_b64encode(contenido_bytes).decode("utf-8")
        content_for_claude = [
            {
                "type": "document",
                "source": {"type": "base64", "media_type": "application/pdf", "data": b64},
            },
            {"type": "text", "text": "Analiza este documento legal."},
        ]

    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            system=PROMPT_RESUMEN_ESTRUCTURADO,
            messages=[{"role": "user", "content": content_for_claude}],
        )
        raw = response.content[0].text.strip()
        # Limpiar posible markdown
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        resumen = _json.loads(raw)
    except Exception as e:
        print(f"[Extracción estructurada] ⚠️ Error: {e}")
        resumen = {"tipo_documento": "otro", "hechos_clave": "No se pudo procesar el documento."}

    # Construir texto_relevante para BM25 (solo campos textuales importantes)
    partes = resumen.get("partes", {})
    partes_str = " ".join(v for v in partes.values() if v)
    pruebas = " ".join(resumen.get("pruebas_evidencia", []))
    fundamentos = " ".join(resumen.get("fundamentos_juridicos", []))
    fechas = " ".join(
        f"{f.get('fecha', '')} {f.get('descripcion', '')}"
        for f in resumen.get("fechas_importantes", [])
    )
    texto_relevante = "\n".join(filter(None, [
        resumen.get("hechos_clave", ""),
        resumen.get("pretension", ""),
        partes_str,
        pruebas,
        fundamentos,
        fechas,
        resumen.get("resolucion_fallo") or "",
    ]))

    return resumen, texto_relevante
