# agent/rag.py — Búsqueda semántica en normativa peruana (RAG)
# Minka — Asistente Legal AI

"""
Módulo RAG que consulta ChromaDB para encontrar artículos legales relevantes.
Se inyecta en el system prompt de generar_respuesta() para que Claude cite la ley exacta.

Uso:
    from agent.rag import buscar_normativa
    articulos = buscar_normativa("plazo prescripción delito estafa", codigos=["CP"], top_k=5)
"""

import os
import logging
from typing import Optional

logger = logging.getLogger("minka")

# Ruta a la base de datos ChromaDB (relativa al directorio de ejecución del servidor)
CHROMA_DIR      = os.path.join(os.path.dirname(__file__), "..", "knowledge", "chromadb")
COLLECTION_NAME = "normativa_peruana"
EMBED_MODEL     = "paraphrase-multilingual-MiniLM-L12-v2"

# Singleton — cargamos una sola vez para no re-cargar el modelo en cada request
_collection = None


def _get_collection():
    """Inicializa y cachea la colección ChromaDB (carga el modelo una sola vez)."""
    global _collection
    if _collection is not None:
        return _collection

    try:
        import chromadb
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    except ImportError:
        logger.error("RAG no disponible: instala 'chromadb' y 'sentence-transformers'")
        return None

    chroma_path = os.path.abspath(CHROMA_DIR)
    if not os.path.exists(chroma_path):
        logger.warning(f"ChromaDB no encontrado en {chroma_path}. Corre: python scripts/index_normativa.py")
        return None

    try:
        embed_fn = SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)
        client = chromadb.PersistentClient(path=chroma_path)
        _collection = client.get_collection(
            name=COLLECTION_NAME,
            embedding_function=embed_fn,
        )
        logger.info(f"RAG listo: {_collection.count()} artículos indexados")
        return _collection
    except Exception as e:
        logger.error(f"Error inicializando ChromaDB: {e}")
        return None


def buscar_normativa(
    query: str,
    codigos: Optional[list[str]] = None,
    top_k: int = 5,
) -> list[dict]:
    """
    Busca artículos legales relevantes para la consulta.

    Args:
        query:   Texto de búsqueda (pregunta del usuario o resumen del caso)
        codigos: Lista de códigos a filtrar, p.ej. ["CP", "CPP"]. None = todos.
                 Valores válidos: CP, CPP, CPC, CC, CEP, CNA, CONST, NLPT, L30077, L30364
        top_k:   Número máximo de artículos a retornar

    Returns:
        Lista de dicts con: citacion, texto, numero, titulo, codigo
        Lista vacía si RAG no está disponible o no hay resultados.
    """
    collection = _get_collection()
    if collection is None:
        return []

    if not query or not query.strip():
        return []

    try:
        # Construir filtro por código(s) si se especifica
        where = None
        if codigos and len(codigos) == 1:
            where = {"codigo": {"$eq": codigos[0]}}
        elif codigos and len(codigos) > 1:
            where = {"codigo": {"$in": codigos}}

        results = collection.query(
            query_texts=[query.strip()],
            n_results=min(top_k, 10),
            where=where,
        )

        articulos = []
        metas = results.get("metadatas", [[]])[0]
        for m in metas:
            articulos.append({
                "citacion": m.get("citacion", ""),
                "texto":    m.get("texto", ""),
                "numero":   m.get("numero", ""),
                "titulo":   m.get("titulo", ""),
                "codigo":   m.get("codigo", ""),
            })

        return articulos

    except Exception as e:
        logger.error(f"Error en búsqueda RAG: {e}")
        return []


def formatear_para_prompt(articulos: list[dict], max_chars_por_art: int = 400) -> str:
    """
    Convierte los artículos encontrados en texto listo para inyectar en el system prompt.

    Args:
        articulos:          Lista retornada por buscar_normativa()
        max_chars_por_art:  Límite de caracteres del texto de cada artículo

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
