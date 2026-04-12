#!/usr/bin/env python3
"""
index_normativa.py — Indexa la normativa peruana en ChromaDB para búsqueda semántica RAG.

Uso:
  python scripts/index_normativa.py           # Indexa todo
  python scripts/index_normativa.py --reset   # Borra y re-indexa (si cambió la normativa)
  python scripts/index_normativa.py --test    # Corre 4 queries de validación al terminar

Genera: knowledge/chromadb/   (añadir a .gitignore)
"""

import argparse
import json
import os
import sys

# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

KNOWLEDGE_BASE = os.path.join(os.path.dirname(__file__), "..", "knowledge")
NORMATIVA_DIR  = os.path.join(KNOWLEDGE_BASE, "normativa")
CHROMA_DIR     = os.path.join(KNOWLEDGE_BASE, "chromadb")
COLLECTION_NAME = "normativa_peruana"

# Modelo multilingual para español legal
EMBED_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"

# Mapeo carpeta → código corto para metadatos y filtrado
CODIGO_SHORTNAME = {
    "codigo_penal":               "CP",
    "codigo_procesal_penal":      "CPP",
    "codigo_procesal_civil":      "CPC",
    "codigo_civil":               "CC",
    "codigo_ejecucion_penal":     "CEP",
    "codigo_ninos_adolescentes":  "CNA",
    "constitucion":               "CONST",
    "ley_29497_nlpt":             "NLPT",
    "ley_30077_crimen_organizado":"L30077",
    "ley_30364_violencia_mujer":  "L30364",
}

# Nombre completo para citaciones
CODIGO_NOMBRE = {
    "CP":     "Código Penal",
    "CPP":    "Código Procesal Penal",
    "CPC":    "Código Procesal Civil",
    "CC":     "Código Civil",
    "CEP":    "Código de Ejecución Penal",
    "CNA":    "Código de los Niños y Adolescentes",
    "CONST":  "Constitución Política del Perú",
    "NLPT":   "Nueva Ley Procesal del Trabajo (Ley 29497)",
    "L30077": "Ley 30077 - Crimen Organizado",
    "L30364": "Ley 30364 - Violencia contra la Mujer",
}

# ---------------------------------------------------------------------------
# Carga de artículos
# ---------------------------------------------------------------------------

def cargar_todos_articulos() -> list[dict]:
    """Lee todos los articulos.json y añade campo 'codigo' (shortname)."""
    todos = []
    for carpeta, codigo in CODIGO_SHORTNAME.items():
        ruta = os.path.join(NORMATIVA_DIR, carpeta, "articulos.json")
        if not os.path.exists(ruta):
            print(f"  [OMITIDO] {carpeta} — no encontrado")
            continue
        with open(ruta, encoding="utf-8") as f:
            articulos = json.load(f)
        for a in articulos:
            a["_codigo"] = codigo
            a["_carpeta"] = carpeta
        todos.extend(articulos)
        print(f"  {codigo:<8} {len(articulos):>5} arts  ({carpeta})")
    return todos


def construir_texto_embedding(art: dict) -> str:
    """
    Texto que se embeds. Incluir número + título + texto para que
    búsquedas por concepto ('estafa') y por artículo ('Art. 196') funcionen.
    """
    partes = []
    codigo = art.get("_codigo", "")
    nombre = CODIGO_NOMBRE.get(codigo, codigo)

    numero = art.get("numero", "")
    titulo = art.get("titulo", "")
    texto  = art.get("texto", "")

    if numero:
        partes.append(f"Artículo {numero}.")
    if titulo:
        partes.append(titulo + ".")
    if texto:
        # Limitar a ~500 palabras para no sobrecargar el embedding
        palabras = texto.split()
        partes.append(" ".join(palabras[:500]))

    return f"[{nombre}] " + " ".join(partes)


def construir_citacion(art: dict) -> str:
    """'Art. 196 del Código Penal'"""
    codigo = art.get("_codigo", "")
    nombre = CODIGO_NOMBRE.get(codigo, codigo)
    numero = art.get("numero", "")
    return f"Art. {numero} del {nombre}" if numero else nombre


def construir_doc_id(art: dict) -> str:
    """ID único por código + número de artículo."""
    codigo  = art.get("_codigo", "UNKNOWN")
    numero  = art.get("numero", "0").replace("/", "_").replace(" ", "")
    return f"{codigo}_{numero}"

# ---------------------------------------------------------------------------
# Indexación
# ---------------------------------------------------------------------------

def indexar(reset: bool = False) -> "chromadb.Collection":
    try:
        import chromadb
        from chromadb.utils.embedding_functions import SentenceTransformerEmbeddingFunction
    except ImportError:
        print("ERROR: chromadb o sentence-transformers no instalado.", file=sys.stderr)
        print("  pip install chromadb sentence-transformers", file=sys.stderr)
        sys.exit(1)

    print(f"\nCargando artículos desde {NORMATIVA_DIR}...")
    articulos = cargar_todos_articulos()
    print(f"Total: {len(articulos)} artículos\n")

    print(f"Inicializando ChromaDB en {CHROMA_DIR}")
    os.makedirs(CHROMA_DIR, exist_ok=True)
    client = chromadb.PersistentClient(path=CHROMA_DIR)

    if reset:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"  Colección '{COLLECTION_NAME}' eliminada para re-indexar.")
        except Exception:
            pass

    print(f"Cargando modelo de embeddings: {EMBED_MODEL}")
    print("  (primera vez descarga ~500MB — siguiente vez es instantáneo)")
    embed_fn = SentenceTransformerEmbeddingFunction(model_name=EMBED_MODEL)

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=embed_fn,
        metadata={"hnsw:space": "cosine"},
    )

    # Verificar cuántos ya están indexados
    existing = collection.count()
    if existing > 0 and not reset:
        print(f"\n  Ya hay {existing} documentos indexados.")
        print("  Usa --reset para re-indexar desde cero.")
        return collection

    # Deduplicar por doc_id (algunos artículos aparecen dos veces en el JSON)
    seen_ids = set()
    articulos_unicos = []
    duplicados = 0
    for art in articulos:
        doc_id = construir_doc_id(art)
        if doc_id not in seen_ids:
            seen_ids.add(doc_id)
            articulos_unicos.append(art)
        else:
            duplicados += 1
    if duplicados:
        print(f"  [DEDUP] {duplicados} artículos duplicados omitidos")
    articulos = articulos_unicos

    # Indexar en lotes (ChromaDB recomienda <=500 por batch)
    BATCH = 200
    total_indexados = 0
    total_omitidos  = 0

    print(f"\nIndexando {len(articulos)} artículos en lotes de {BATCH}...")
    for start in range(0, len(articulos), BATCH):
        lote = articulos[start : start + BATCH]

        ids       = []
        documents = []
        metadatas = []

        for art in lote:
            texto_embed = construir_texto_embedding(art)
            if not texto_embed.strip():
                total_omitidos += 1
                continue

            doc_id = construir_doc_id(art)
            ids.append(doc_id)
            documents.append(texto_embed)
            metadatas.append({
                "codigo":      art.get("_codigo", ""),
                "numero":      art.get("numero", ""),
                "titulo":      art.get("titulo", "")[:200],
                "libro":       art.get("libro", "")[:100],
                "titulo_legal":art.get("titulo_legal", "")[:100],
                "capitulo":    art.get("capitulo", "")[:100],
                "citacion":    construir_citacion(art),
                # Texto completo para mostrar en resultados (no se embeds)
                "texto":       art.get("texto", "")[:2000],
            })

        if ids:
            collection.upsert(ids=ids, documents=documents, metadatas=metadatas)
            total_indexados += len(ids)
            pct = (start + len(lote)) / len(articulos) * 100
            print(f"  {pct:5.1f}%  {total_indexados} indexados...")

    print(f"\nIndexación completa:")
    print(f"  Indexados : {total_indexados}")
    print(f"  Omitidos  : {total_omitidos} (sin texto)")
    print(f"  Total BD  : {collection.count()}")
    return collection


# ---------------------------------------------------------------------------
# Test de validación
# ---------------------------------------------------------------------------

QUERIES_TEST = [
    ("estafa",                           ["CP"],            "Art. 196"),
    ("plazo prescripción acción penal",  ["CP"],            "Art. 80"),
    ("medidas de protección violencia",  ["L30364"],        "Art. "),
    ("proceso inmediato flagrancia",     ["CPP"],           "Art. 44"),
]

def test_rag(collection) -> None:
    print("\n" + "="*60)
    print("TEST DE VALIDACIÓN — 4 queries de referencia")
    print("="*60)
    ok = 0
    for query, codigos_esperados, art_esperado in QUERIES_TEST:
        results = collection.query(
            query_texts=[query],
            n_results=5,
            where={"codigo": {"$in": codigos_esperados}} if len(codigos_esperados) == 1 else None,
        )
        metas = results["metadatas"][0]
        encontrado = any(art_esperado.lower() in m["citacion"].lower() for m in metas)
        status = "OK" if encontrado else "FALLO"
        if encontrado:
            ok += 1
        top1 = metas[0]["citacion"] if metas else "—"
        print(f"  [{status}] '{query}'")
        print(f"         Top-1: {top1}")
    print(f"\nResultado: {ok}/{len(QUERIES_TEST)} queries pasaron")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Indexa normativa peruana en ChromaDB (RAG)")
    parser.add_argument("--reset", action="store_true", help="Borra y re-indexa toda la colección")
    parser.add_argument("--test",  action="store_true", help="Valida con 4 queries al terminar")
    args = parser.parse_args()

    collection = indexar(reset=args.reset)

    if args.test:
        test_rag(collection)


if __name__ == "__main__":
    main()
