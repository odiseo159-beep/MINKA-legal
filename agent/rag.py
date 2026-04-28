# agent/rag.py — Búsqueda de normativa peruana con BM25
# Minka — Asistente Legal AI
#
# Usa BM25 (rank-bm25) sobre los JSON ya scrapeados.
# Sin PyTorch, sin modelos de 500MB — funciona en Railway gratis.
#
# Uso:
#   from agent.rag import buscar_normativa, formatear_para_prompt
#   arts = buscar_normativa("plazo prescripción estafa", codigos=["CP"], top_k=5)

import os
import re
import json
import logging
from typing import Optional

logger = logging.getLogger("minka")

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

NORMATIVA_DIR = os.path.join(os.path.dirname(__file__), "..", "knowledge", "normativa")

CODIGOS = {
    "codigo_penal":                  "CP",
    "codigo_procesal_penal":         "CPP",
    "codigo_procesal_civil":         "CPC",
    "codigo_civil":                  "CC",
    "codigo_ejecucion_penal":        "CEP",
    "codigo_ninos_adolescentes":     "CNA",
    "constitucion":                  "CONST",
    "ley_29497_nlpt":                "NLPT",
    "ley_30077_crimen_organizado":   "L30077",
    "ley_30364_violencia_mujer":     "L30364",
    "codigo_tributario":             "CT",
    "ley_26887_sociedades":          "LGS",
    "ley_27444_lpag":                "LPAG",
    "ley_27584_contencioso":         "LPCA",
    "codigo_procesal_constitucional":"CPCo",
    "plenos_casatorios_civiles":     "PCC",
}

CODIGO_NOMBRE = {
    "CP":    "Código Penal",
    "CPP":   "Código Procesal Penal",
    "CPC":   "Código Procesal Civil",
    "CC":    "Código Civil",
    "CEP":   "Código de Ejecución Penal",
    "CNA":   "Código de los Niños y Adolescentes",
    "CONST": "Constitución Política del Perú",
    "NLPT":  "Ley 29497 - Nueva Ley Procesal del Trabajo",
    "L30077":"Ley 30077 - Crimen Organizado",
    "L30364":"Ley 30364 - Violencia contra la Mujer",
    "CT":    "Código Tributario (TUO DS 133-2013-EF)",
    "LGS":   "Ley 26887 - Ley General de Sociedades",
    "LPAG":  "Ley 27444 - Procedimiento Administrativo General",
    "LPCA":  "Ley 27584 - Proceso Contencioso Administrativo",
    "CPCo":  "Código Procesal Constitucional (Ley 31307)",
    "PCC":   "Plenos Casatorios Civiles (precedente vinculante de la Corte Suprema)",
}

# ---------------------------------------------------------------------------
# Singleton: índice BM25 (se construye una sola vez al primer request)
# ---------------------------------------------------------------------------

_bm25       = None   # instancia BM25Okapi
_articulos  = []     # lista paralela de dicts con metadata


def _tokenizar(texto: str) -> list[str]:
    """Tokenización simple para español legal."""
    texto = texto.lower()
    # Conservar números de artículos como tokens (196, 80, 446)
    return re.findall(r"[a-záéíóúüñ]+|\d+", texto)


def _construir_indice():
    """Carga todos los artículos y construye el índice BM25. Se llama una sola vez."""
    global _bm25, _articulos
    try:
        from rank_bm25 import BM25Okapi
    except ImportError:
        logger.error("rank-bm25 no instalado. Ejecuta: pip install rank-bm25")
        return

    corpus = []
    _articulos = []

    for carpeta, codigo in CODIGOS.items():
        ruta = os.path.join(NORMATIVA_DIR, carpeta, "articulos.json")
        if not os.path.exists(ruta):
            continue
        with open(ruta, encoding="utf-8") as f:
            arts = json.load(f)

        nombre = CODIGO_NOMBRE.get(codigo, codigo)
        cargados = 0
        for art in arts:
            numero = art.get("numero", "")
            titulo = art.get("titulo", "")
            texto  = art.get("texto", "")
            if not texto and not titulo:
                continue

            # Caso especial: Plenos Casatorios — formato de citación distinto
            if codigo == "PCC":
                casacion = art.get("casacion", "")
                materia  = art.get("materia", "")
                reglas   = art.get("reglas_vinculantes", "")
                # Indexamos: título + materia + reglas vinculantes (lo más citable) + texto
                texto_idx = f"pleno casatorio civil {numero} {titulo} {materia} {casacion} {reglas} {texto}"
                citacion = f"{numero} Pleno Casatorio Civil (Cas. {casacion})"
            else:
                texto_idx = f"{nombre} artículo {numero} {titulo} {texto}"
                citacion = f"Art. {numero} del {nombre}"

            corpus.append(_tokenizar(texto_idx))

            _articulos.append({
                "codigo":   codigo,
                "numero":   numero,
                "titulo":   titulo,
                "texto":    texto,
                "citacion": citacion,
            })
            cargados += 1

    if not corpus:
        logger.warning("RAG: no se encontraron artículos en knowledge/normativa/")
        return

    _bm25 = BM25Okapi(corpus)
    logger.info(f"RAG BM25 listo: {len(_articulos)} artículos indexados")


def _get_indice():
    """Retorna el índice BM25, construyéndolo si es la primera llamada."""
    global _bm25
    if _bm25 is None:
        _construir_indice()
    return _bm25


# ---------------------------------------------------------------------------
# API pública
# ---------------------------------------------------------------------------

def buscar_normativa(
    query: str,
    codigos: Optional[list[str]] = None,
    top_k: int = 5,
) -> list[dict]:
    """
    Busca artículos legales relevantes usando BM25.

    Args:
        query:   Texto de búsqueda (pregunta del usuario o resumen del caso)
        codigos: Filtro por código, p.ej. ["CP", "CPP"]. None = todos los códigos.
        top_k:   Número máximo de resultados.

    Returns:
        Lista de dicts con: citacion, texto, numero, titulo, codigo
    """
    bm25 = _get_indice()
    if bm25 is None or not _articulos:
        return []
    if not query or not query.strip():
        return []

    tokens = _tokenizar(query.strip())
    scores = bm25.get_scores(tokens)

    # Aplicar filtro por código (poner score=0 a los que no coinciden)
    if codigos:
        codigos_set = set(codigos)
        for i, art in enumerate(_articulos):
            if art["codigo"] not in codigos_set:
                scores[i] = 0.0

    # Ordenar por score descendente y tomar top_k con score > 0
    indices_ordenados = sorted(
        range(len(scores)),
        key=lambda i: scores[i],
        reverse=True,
    )

    resultados = []
    for i in indices_ordenados[:top_k * 2]:  # traer más y filtrar
        if scores[i] <= 0:
            break
        resultados.append(_articulos[i])
        if len(resultados) >= top_k:
            break

    return resultados


def formatear_para_prompt(articulos: list[dict], max_chars_por_art: int = 400) -> str:
    """
    Convierte los artículos encontrados en texto listo para inyectar en el system prompt.

    Returns:
        Bloque de texto formateado, o cadena vacía si no hay artículos.
    """
    if not articulos:
        return ""

    lineas = ["Artículos legales relevantes (cítalos cuando apliquen):"]
    for art in articulos:
        texto = art["texto"][:max_chars_por_art].rstrip()
        if len(art["texto"]) > max_chars_por_art:
            texto += "..."
        titulo = f" — {art['titulo']}" if art.get("titulo") else ""
        lineas.append(f"- {art['citacion']}{titulo}: {texto}")

    return "\n".join(lineas)
