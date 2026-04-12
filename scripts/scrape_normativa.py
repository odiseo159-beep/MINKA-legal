#!/usr/bin/env python3
"""
scrape_normativa.py — Scraper de Códigos Legales Peruanos
Fuente principal: https://lpderecho.pe/

Genera en knowledge/normativa/<codigo>/:
  metadata.json   — título, decreto, fuente, fecha de scraping
  articulos.json  — lista de artículos con jerarquía y texto completo

Uso:
  python scripts/scrape_normativa.py --code cp          # Código Penal
  python scripts/scrape_normativa.py --code cpc         # Código Procesal Civil
  python scripts/scrape_normativa.py --code cpp         # CPP Arts 1-445 (Parte General + Proceso Común)
  python scripts/scrape_normativa.py --code cpp2        # CPP Arts 446-566 (Procesos Especiales)
  python scripts/scrape_normativa.py --code cep         # Código de Ejecución Penal
  python scripts/scrape_normativa.py --code const       # Constitución Política del Perú
  python scripts/scrape_normativa.py --all              # Todos los códigos accesibles
  python scripts/scrape_normativa.py --merge-cpp        # Fusiona cpp + cpp2 en un solo JSON
  python scripts/scrape_normativa.py --code cp --dry-run
  python scripts/scrape_normativa.py --code cp --from-file /tmp/cp.html
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

# ---------------------------------------------------------------------------
# Registro de códigos disponibles
# ---------------------------------------------------------------------------

CODIGOS = {
    "cp": {
        "nombre": "Código Penal Peruano",
        "decreto": "Decreto Legislativo 635",
        "url": "https://lpderecho.pe/codigo-penal-peruano-actualizado/",
        "output_dir": "codigo_penal",
        "nota": "",
    },
    "cpc": {
        "nombre": "Código Procesal Civil Peruano",
        "decreto": "Decreto Legislativo 768 / Resolución Ministerial 010-93-JUS",
        "url": "https://lpderecho.pe/codigo-procesal-civil-actualizado/",
        "output_dir": "codigo_procesal_civil",
        "nota": "",
    },
    "cpp": {
        "nombre": "Nuevo Código Procesal Penal (Arts. 1-445)",
        "decreto": "Decreto Legislativo 957",
        "url": "https://lpderecho.pe/nuevo-codigo-procesal-penal-peruano-actualizado/",
        "output_dir": "codigo_procesal_penal",
        "nota": "Parte 1 de 2: Arts. 1-445 (Titulo Preliminar, Libros I-IV). "
                "Ejecutar --merge-cpp al finalizar para unir con cpp2.",
    },
    "cpp2": {
        "nombre": "Nuevo Código Procesal Penal (Arts. 446-566)",
        "decreto": "Decreto Legislativo 957",
        "url": "https://lpderecho.pe/nuevo-codigo-procesal-penal/",
        "output_dir": "codigo_procesal_penal",
        "output_file": "articulos_parte2.json",
        "nota": "Parte 2 de 2: Arts. 446-566 (Libros V-VI: Procesos Especiales, Ejecucion). "
                "Ejecutar --merge-cpp al finalizar para unir con cpp.",
    },
    "cep": {
        "nombre": "Código de Ejecución Penal",
        "decreto": "Decreto Legislativo 654",
        "url": "https://lpderecho.pe/codigo-de-ejecucion-penal-decreto-legislativo-654-actualizado-2019/",
        "output_dir": "codigo_ejecucion_penal",
        "nota": "",
    },
    "const": {
        "nombre": "Constitución Política del Perú",
        "decreto": "Promulgada el 29 de diciembre de 1993",
        "url": "https://lpderecho.pe/constitucion-politica-peru-actualizada/",
        "output_dir": "constitucion",
        "nota": "",
    },
    "cc": {
        "nombre": "Código Civil Peruano",
        "decreto": "Decreto Legislativo 295",
        "url": "https://lpderecho.pe/codigo-civil-peruano-realmente-actualizado/",
        "output_dir": "codigo_civil",
        "nota": "",
    },
    "cna": {
        "nombre": "Código de los Niños y Adolescentes",
        "decreto": "Ley 27337",
        "url": "https://lpderecho.pe/codigo-ninos-adolescentes-ley-27337-actualizado/",
        "output_dir": "codigo_ninos_adolescentes",
        "nota": "",
    },
    "nlpt": {
        "nombre": "Nueva Ley Procesal del Trabajo",
        "decreto": "Ley 29497",
        "url": "https://lpderecho.pe/nueva-ley-procesal-trabajo-ley-29497-actualizada/",
        "output_dir": "ley_29497_nlpt",
        "nota": "",
    },
}

KNOWLEDGE_BASE = os.path.join(os.path.dirname(__file__), "..", "knowledge", "normativa")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xhtml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-PE,es;q=0.9,en;q=0.8",
}

# ---------------------------------------------------------------------------
# Utilidades de texto
# ---------------------------------------------------------------------------

def _extract_modification_notes(paragraphs: list) -> list[str]:
    notas = []
    for p in paragraphs:
        for span in p.find_all("span", style=re.compile(r"background-color:\s*#ffff99")):
            text = span.get_text(separator=" ", strip=True)
            if "jurisprudencia" not in text.lower() and text:
                notas.append(text)
    return notas


def _extract_jurisprudencia_url(paragraphs: list) -> str | None:
    for p in paragraphs:
        for span in p.find_all("span", style=re.compile(r"background-color:\s*#ffff99")):
            if "jurisprudencia" in span.get_text(strip=True).lower():
                a = span.find("a")
                if a and a.get("href"):
                    return a["href"]
    return None


def _parse_articulo_numero_titulo(h4_text: str) -> tuple[str, str]:
    """
    'Artículo 196.- Estafa*'         → ('196', 'Estafa')
    'Artículo VIII.- Proporcionalidad*' → ('VIII', 'Proporcionalidad')
    'Artículo 68-A. Operativo...'    → ('68-A', 'Operativo...')
    'Artículo 341 B.-...'            → ('341-B', '...')
    """
    text = h4_text.strip().rstrip("*").strip()
    # Número: dígitos o romanos, con sufijo opcional como -A, -B, A, B (ej: 68-A, 341 B)
    m = re.match(
        r"Art[ií]culo\s+([IVXLCDM\d]+(?:[°º])?(?:\s*[-–]\s*[A-Z]|(?<=\d)\s+[A-Z](?=[\s.\-°]))?)\s*[.\-°]+\s*(.+)",
        text, re.IGNORECASE
    )
    if m:
        numero = re.sub(r"\s+", "", m.group(1).strip())  # "341 B" → "341B"
        titulo = m.group(2).strip().rstrip("*").strip()
        return numero, titulo
    return "", text


def _heading_level(tag: Tag) -> int | None:
    if tag.name and tag.name[0] == "h" and len(tag.name) == 2 and tag.name[1].isdigit():
        return int(tag.name[1])
    return None


def _is_articulo_heading(tag: Tag) -> bool:
    """
    Detecta encabezados de artículo. lpderecho usa dos formatos:
    1. <h4><span style="color:#ff0000"><strong>Artículo N.-...</strong></span></h4>  (mayoritario)
    2. <p><span style="color:#ff0000"><strong>Artículo N.-...</strong></span></p>    (NLPT, algunas leyes)
    """
    if tag.name not in ("h4", "h5", "p"):
        return False
    text = tag.get_text(strip=True)
    if not re.match(r"Art[ií]culo\s+", text, re.IGNORECASE):
        return False
    # Para <p>: solo si contiene un <strong> o <b> (evitar párrafos normales que mencionen "Artículo")
    if tag.name == "p":
        return bool(tag.find(["strong", "b"]))
    return True


def _clean_heading_text(tag: Tag) -> str:
    return tag.get_text(separator=" ", strip=True)


# ---------------------------------------------------------------------------
# Parser principal
# ---------------------------------------------------------------------------

def parse_codigo(html: str, codigo_cfg: dict) -> tuple[dict, list[dict]]:
    """
    Parsea el HTML de un código legal y retorna (metadata, articulos).
    Funciona con la estructura de lpderecho.pe (WordPress + headings h2/h3/h4).
    """
    soup = BeautifulSoup(html, "lxml")

    content = (
        soup.find(class_="td-post-content")
        or soup.find(class_="entry-content")
        or soup.find("article")
        or soup.find("main")
    )
    if not content:
        raise ValueError("No se encontró el contenedor de contenido en el HTML")

    titulo_tag = soup.find("h1") or soup.find("title")
    metadata = {
        "codigo": codigo_cfg["nombre"],
        "decreto": codigo_cfg["decreto"],
        "fuente_url": codigo_cfg["url"],
        "titulo_pagina": titulo_tag.get_text(strip=True) if titulo_tag else codigo_cfg["nombre"],
        "fecha_scraping": datetime.now(timezone.utc).isoformat(),
        "fecha_actualizacion_fuente": str(datetime.now().year),
        "nota": codigo_cfg.get("nota", ""),
    }

    articulos = []
    ctx = {"libro": "", "titulo_legal": "", "capitulo": "", "seccion": ""}

    elements = list(content.children)
    i = 0
    while i < len(elements):
        el = elements[i]

        if isinstance(el, NavigableString):
            i += 1
            continue
        if not isinstance(el, Tag):
            i += 1
            continue

        level = _heading_level(el)

        # --- Actualizar contexto jerárquico ---
        if level == 2:
            text = _clean_heading_text(el)
            if re.search(r"LIBRO\s+(PRIMERO|SEGUNDO|TERCERO|CUARTO|QUINTO|[IVX]+)", text, re.IGNORECASE):
                ctx["libro"] = text
                ctx["titulo_legal"] = ""
                ctx["capitulo"] = ""
                ctx["seccion"] = ""
            elif re.search(r"TÍTULO\s+[IVXLCDM]+|SECCIÓN\s+[IVXLCDM]+", text, re.IGNORECASE):
                ctx["titulo_legal"] = text
                ctx["capitulo"] = ""
                ctx["seccion"] = ""
            else:
                if not ctx["libro"]:
                    ctx["libro"] = text
                else:
                    ctx["titulo_legal"] = text
            i += 1
            continue

        if level == 3:
            text = _clean_heading_text(el)
            if re.search(r"CAPÍTULO|CAPITULO|SUBCAPÍTULO|SUBCAPITULO", text, re.IGNORECASE):
                ctx["capitulo"] = text
                ctx["seccion"] = ""
            elif re.search(r"SECCIÓN|SECCION", text, re.IGNORECASE):
                ctx["seccion"] = text
            else:
                ctx["seccion"] = text
            i += 1
            continue

        # Artículo en <h4>/<h5> O en <p><strong> (ej. NLPT)
        is_art = (level in (4, 5) and _is_articulo_heading(el)) or \
                 (el.name == "p" and _is_articulo_heading(el))
        if is_art:
            h4_text = _clean_heading_text(el)
            numero, titulo = _parse_articulo_numero_titulo(h4_text)

            # Recoger párrafos hasta el próximo heading o artículo
            parrafos = []
            j = i + 1
            while j < len(elements):
                sib = elements[j]
                if isinstance(sib, NavigableString):
                    j += 1
                    continue
                if isinstance(sib, Tag):
                    sib_level = _heading_level(sib)
                    if sib_level is not None and sib_level <= 4:
                        break
                    # Detener también si el siguiente <p> es otro artículo
                    if sib.name == "p" and _is_articulo_heading(sib):
                        break
                    if sib.name in ("p", "ul", "ol", "blockquote", "div"):
                        parrafos.append(sib)
                j += 1

            # Texto limpio (sin notas de modificación)
            texto_parts = []
            for p in parrafos:
                p_copy = BeautifulSoup(str(p), "lxml").find(p.name)
                if p_copy:
                    for span in p_copy.find_all("span", style=re.compile(r"background-color:\s*#ffff99")):
                        span.decompose()
                    t = p_copy.get_text(separator="\n", strip=True)
                    if t:
                        texto_parts.append(t)
            texto = re.sub(r"\n{3,}", "\n\n", "\n\n".join(texto_parts)).strip()

            # Texto completo (con notas)
            texto_completo_parts = []
            for p in parrafos:
                t = p.get_text(separator="\n", strip=True)
                if t:
                    texto_completo_parts.append(t)
            texto_completo = re.sub(r"\n{3,}", "\n\n", "\n\n".join(texto_completo_parts)).strip()

            articulos.append({
                "numero": numero,
                "titulo": titulo,
                "texto": texto,
                "libro": ctx["libro"],
                "titulo_legal": ctx["titulo_legal"],
                "capitulo": ctx["capitulo"],
                "seccion": ctx["seccion"],
                "modificaciones": _extract_modification_notes(parrafos),
                "jurisprudencia_url": _extract_jurisprudencia_url(parrafos),
                "texto_completo": texto_completo,
            })
            i = j
            continue

        if level == 4:
            ctx["seccion"] = _clean_heading_text(el)
            i += 1
            continue

        i += 1

    return metadata, articulos


# ---------------------------------------------------------------------------
# Descarga
# ---------------------------------------------------------------------------

def descargar_html(url: str) -> str:
    print(f"Descargando {url}...")
    resp = httpx.get(url, headers=HEADERS, timeout=90, follow_redirects=True)
    resp.raise_for_status()
    print(f"Descargado: {len(resp.text):,} bytes")
    return resp.text


# ---------------------------------------------------------------------------
# Ejecutar un código
# ---------------------------------------------------------------------------

def scrape_codigo(codigo_key: str, from_file: str | None = None, dry_run: bool = False) -> bool:
    if codigo_key not in CODIGOS:
        print(f"Código '{codigo_key}' no reconocido. Disponibles: {', '.join(CODIGOS.keys())}", file=sys.stderr)
        return False

    cfg = CODIGOS[codigo_key]
    print(f"\n{'='*60}")
    print(f"  {cfg['nombre']}")
    print(f"  {cfg['decreto']}")
    if cfg.get("nota"):
        print(f"  [AVISO] {cfg['nota']}")
    print(f"{'='*60}")

    # Obtener HTML
    if from_file:
        print(f"Leyendo HTML desde {from_file}...")
        with open(from_file, "r", encoding="utf-8", errors="replace") as f:
            html = f.read()
    else:
        try:
            html = descargar_html(cfg["url"])
        except Exception as e:
            print(f"ERROR al descargar: {e}", file=sys.stderr)
            return False

    # Parsear
    print("Parseando artículos...")
    try:
        metadata, articulos = parse_codigo(html, cfg)
    except Exception as e:
        print(f"ERROR al parsear: {e}", file=sys.stderr)
        return False

    print(f"Artículos encontrados: {len(articulos)}")

    if dry_run:
        print("\n--- DRY RUN: primeros 5 artículos ---")
        for a in articulos[:5]:
            print(f"\n  [{a['libro']}] [{a['titulo_legal']}] [{a['capitulo']}]")
            print(f"  Art. {a['numero']} — {a['titulo']}")
            print(f"  Texto: {a['texto'][:150]}...")
        print(f"\n--- Últimos 3 artículos ---")
        for a in articulos[-3:]:
            print(f"  Art. {a['numero']} — {a['titulo']} [{a['libro']}]")
        return True

    # Guardar
    output_dir = os.path.normpath(os.path.join(KNOWLEDGE_BASE, cfg["output_dir"]))
    os.makedirs(output_dir, exist_ok=True)

    with open(os.path.join(output_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    output_filename = cfg.get("output_file", "articulos.json")
    articulos_path = os.path.join(output_dir, output_filename)
    with open(articulos_path, "w", encoding="utf-8") as f:
        json.dump(articulos, f, ensure_ascii=False, indent=2)

    size_kb = os.path.getsize(articulos_path) // 1024
    print(f"Guardado: {articulos_path} ({len(articulos)} articulos, {size_kb} KB)")

    # Resumen por libro
    libros: dict[str, int] = {}
    for a in articulos:
        libro = a["libro"] or "(sin libro)"
        libros[libro] = libros.get(libro, 0) + 1
    print("Resumen por libro:")
    for libro, count in sorted(libros.items()):
        print(f"  {libro}: {count} artículos")

    return True


# ---------------------------------------------------------------------------
# Merge CPP (Arts 1-445 + Arts 446-566 → articulos.json completo)
# ---------------------------------------------------------------------------

def merge_cpp() -> bool:
    cpp_dir = os.path.normpath(os.path.join(KNOWLEDGE_BASE, "codigo_procesal_penal"))
    parte1 = os.path.join(cpp_dir, "articulos.json")
    parte2 = os.path.join(cpp_dir, "articulos_parte2.json")
    salida  = os.path.join(cpp_dir, "articulos.json")

    if not os.path.exists(parte1):
        print(f"ERROR: {parte1} no existe. Ejecuta --code cpp primero.", file=sys.stderr)
        return False
    if not os.path.exists(parte2):
        print(f"ERROR: {parte2} no existe. Ejecuta --code cpp2 primero.", file=sys.stderr)
        return False

    with open(parte1, encoding="utf-8") as f:
        arts1 = json.load(f)
    with open(parte2, encoding="utf-8") as f:
        arts2 = json.load(f)

    # Unir y ordenar por número de artículo (numérico cuando sea posible)
    def sort_key(a):
        n = a.get("numero", "")
        try:
            return (0, int(n))
        except ValueError:
            return (1, n)

    merged = sorted(arts1 + arts2, key=sort_key)
    numeros = [a["numero"] for a in merged]
    duplicados = [n for n in set(numeros) if numeros.count(n) > 1]
    if duplicados:
        print(f"[AVISO] Articulos duplicados detectados: {duplicados[:10]}")

    with open(salida, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    size_kb = os.path.getsize(salida) // 1024
    print(f"\n{'='*60}")
    print(f"CPP COMPLETO: {len(merged)} articulos ({size_kb} KB)")
    print(f"  Parte 1 (Arts 1-445):   {len(arts1)} arts")
    print(f"  Parte 2 (Arts 446-566): {len(arts2)} arts")
    print(f"  Guardado: {salida}")
    if duplicados:
        print(f"  Duplicados: {duplicados}")
    return True


# ---------------------------------------------------------------------------
# Parser SPIJ — obtiene normativa via API REST autenticada
# ---------------------------------------------------------------------------

SPIJ_BACK = "https://spijwsii.minjus.gob.pe/spij-ext-back/"
SPIJ_AUTH = {"usuario": "spijext", "clave": "password", "tipo": 0}


def _spij_get_token() -> str:
    """Autentica como invitado en SPIJ y retorna el JWT."""
    resp = httpx.post(
        SPIJ_BACK + "authenticate",
        json=SPIJ_AUTH,
        headers={"Content-Type": "application/json", "Origin": "https://spij.minjus.gob.pe"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success") or not data.get("value"):
        raise ValueError(f"SPIJ auth fallida: {data}")
    return data["value"]


def _spij_parse_html(html_text: str, nombre: str, decreto: str) -> list[dict]:
    """
    Parsea el HTML del campo textoCompleto de SPIJ.
    Formato: artículos en <b>Artículo N. Título</b> seguidos de párrafos <p>.
    """
    soup = BeautifulSoup(html_text, "lxml")

    # Detectar títulos/capítulos
    patron_titulo = re.compile(r"T[ÍI]TULO\s+[IVXLCDM]+", re.IGNORECASE)
    patron_cap = re.compile(r"CAP[ÍI]TULO\s+[IVXLCDM]+", re.IGNORECASE)

    articulos = []
    ctx = {"titulo_legal": "", "capitulo": ""}

    # Iterar todos los elementos de nivel superior
    elements = list(soup.body.children) if soup.body else list(soup.children)

    i = 0
    while i < len(elements):
        el = elements[i]
        if isinstance(el, NavigableString):
            i += 1
            continue
        if not isinstance(el, Tag):
            i += 1
            continue

        text = el.get_text(strip=True)

        # Actualizar contexto jerárquico
        if patron_titulo.match(text):
            ctx["titulo_legal"] = text
            ctx["capitulo"] = ""
            i += 1
            continue
        if patron_cap.match(text):
            ctx["capitulo"] = text
            i += 1
            continue

        # Artículo: en <b> o en <p><b>
        is_art_b = el.name == "b" and re.match(r"Art[ií]culo\s+\d", text, re.IGNORECASE)
        is_art_p = el.name == "p" and el.find("b") and re.match(r"Art[ií]culo\s+\d", text, re.IGNORECASE)

        if is_art_b or is_art_p:
            numero, titulo = _parse_articulo_numero_titulo(text)

            # Recoger párrafos siguientes hasta el próximo artículo o <b>
            parrafos = []
            j = i + 1
            while j < len(elements):
                sib = elements[j]
                if isinstance(sib, NavigableString):
                    j += 1
                    continue
                if isinstance(sib, Tag):
                    sib_text = sib.get_text(strip=True)
                    # Parar si es otro artículo
                    if sib.name == "b" and re.match(r"Art[ií]culo\s+\d", sib_text, re.IGNORECASE):
                        break
                    if sib.name == "p" and sib.find("b") and re.match(r"Art[ií]culo\s+\d", sib_text, re.IGNORECASE):
                        break
                    # Saltar el <p> duplicado del mismo artículo
                    if sib.name == "p" and re.match(r"Art[ií]culo\s+\d", sib_text, re.IGNORECASE):
                        j += 1
                        continue
                    if sib.name in ("p", "ul", "ol", "blockquote"):
                        parrafos.append(sib)
                j += 1

            # Limpiar texto de modificaciones (notas entre paréntesis con asterisco)
            texto_parts = [p.get_text(separator="\n", strip=True) for p in parrafos if p.get_text(strip=True)]
            texto = re.sub(r"\n{3,}", "\n\n", "\n\n".join(texto_parts)).strip()

            articulos.append({
                "numero": numero,
                "titulo": titulo,
                "texto": texto,
                "libro": "",
                "titulo_legal": ctx["titulo_legal"],
                "capitulo": ctx["capitulo"],
                "seccion": "",
                "modificaciones": [],
                "jurisprudencia_url": None,
                "texto_completo": texto,
            })
            i = j
            continue

        i += 1

    # Deduplicar por número (quedar con el de texto más largo)
    seen: dict[str, dict] = {}
    for a in articulos:
        k = a["numero"]
        if k not in seen or len(a["texto"]) > len(seen[k]["texto"]):
            seen[k] = a
    return list(seen.values())


def scrape_spij(norma_id: str, nombre: str, decreto: str, output_dir: str,
                dry_run: bool = False) -> bool:
    """Descarga y parsea una norma del SPIJ via API REST."""
    print(f"\n{'='*60}")
    print(f"  {nombre}")
    print(f"  {decreto}")
    print(f"  SPIJ ID: {norma_id}")
    print(f"{'='*60}")

    print("Autenticando en SPIJ...")
    try:
        token = _spij_get_token()
        print("  Token obtenido.")
    except Exception as e:
        print(f"ERROR auth SPIJ: {e}", file=sys.stderr)
        return False

    print(f"Descargando norma {norma_id}...")
    try:
        resp = httpx.get(
            SPIJ_BACK + f"api/detallenorma/{norma_id}",
            headers={
                "Authorization": f"Bearer {token}",
                "Origin": "https://spij.minjus.gob.pe",
                "Accept": "application/json",
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"ERROR descarga SPIJ: {e}", file=sys.stderr)
        return False

    html_content = data.get("textoCompleto", "")
    if not html_content:
        print("ERROR: textoCompleto vacío en respuesta SPIJ.", file=sys.stderr)
        return False

    print(f"HTML recibido: {len(html_content):,} chars. Parseando...")
    articulos = _spij_parse_html(html_content, nombre, decreto)
    print(f"Articulos encontrados: {len(articulos)}")

    if dry_run:
        for a in articulos[:5]:
            print(f"\n  [{a['titulo_legal']}] Art.{a['numero']} - {a['titulo']}")
            print(f"  Texto: {a['texto'][:120]}...")
        return True

    out_dir = os.path.normpath(os.path.join(KNOWLEDGE_BASE, output_dir))
    os.makedirs(out_dir, exist_ok=True)

    metadata = {
        "codigo": nombre,
        "decreto": decreto,
        "fuente_url": f"https://spij.minjus.gob.pe/spij-ext-web/#/detallenorma/{norma_id}",
        "spij_id": norma_id,
        "titulo_pagina": data.get("titulo", nombre),
        "sumilla": BeautifulSoup(data.get("sumilla", ""), "lxml").get_text(strip=True),
        "fecha_publicacion": data.get("fechaPublicacion", ""),
        "fecha_scraping": datetime.now(timezone.utc).isoformat(),
        "nota": "Extraido via API REST publica de SPIJ (spijwsii.minjus.gob.pe)",
    }
    with open(os.path.join(out_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    with open(os.path.join(out_dir, "articulos.json"), "w", encoding="utf-8") as f:
        json.dump(articulos, f, ensure_ascii=False, indent=2)

    size_kb = os.path.getsize(os.path.join(out_dir, "articulos.json")) // 1024
    print(f"Guardado: {out_dir}/articulos.json ({len(articulos)} articulos, {size_kb} KB)")
    return True


# ---------------------------------------------------------------------------
# Parser de PDF para leyes en texto plano (Ley 30077, etc.)
# ---------------------------------------------------------------------------

def _fix_pdf_text(text: str) -> str:
    """Limpia artefactos comunes del PDF: ligaduras, guiones de línea, espacios extra."""
    # Ligaduras tipográficas
    ligaduras = {"\ufb01": "fi", "\ufb02": "fl", "\ufb00": "ff", "\ufb03": "ffi", "\ufb04": "ffl"}
    for lig, rep in ligaduras.items():
        text = text.replace(lig, rep)
    # Palabras cortadas con guión al final de línea: "inves-\ntigación" → "investigación"
    text = re.sub(r"-\s*\n\s*([a-záéíóúüñA-ZÁÉÍÓÚÜÑ])", r"\1", text)
    # Múltiples espacios
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


def scrape_pdf(pdf_path: str, nombre: str, decreto: str, output_dir: str,
               ley_inicio_patron: str, dry_run: bool = False) -> bool:
    """
    Parsea una ley desde un PDF de El Peruano.
    Extrae artículos con formato: 'Artículo N. Título\n\nTexto...'
    """
    try:
        from pdfminer.high_level import extract_text as pdf_extract
    except ImportError:
        print("ERROR: pdfminer.six no instalado. Ejecuta: pip install pdfminer.six", file=sys.stderr)
        return False

    print(f"\n{'='*60}")
    print(f"  {nombre}")
    print(f"  {decreto}")
    print(f"  Fuente: {pdf_path}")
    print(f"{'='*60}")

    print("Extrayendo texto del PDF...")
    try:
        raw = pdf_extract(pdf_path)
    except Exception as e:
        print(f"ERROR al leer PDF: {e}", file=sys.stderr)
        return False

    text = _fix_pdf_text(raw)

    # Encontrar inicio de la ley dentro del PDF (puede estar en una edición de El Peruano)
    start = -1
    for pat in [ley_inicio_patron, ley_inicio_patron.replace("Nº", "N°"), ley_inicio_patron.replace("N°", "Nº")]:
        start = text.find(pat)
        if start != -1:
            break
    if start == -1:
        print(f"ERROR: No se encontró '{ley_inicio_patron}' en el PDF.", file=sys.stderr)
        return False

    ley_text = text[start:]
    print(f"Ley encontrada (chars: {len(ley_text)})")

    # Parsear artículos
    # Patron: "Artículo N. Título\n" o "Artículo N.- Título\n"
    patron_art = re.compile(
        r"Art[íi]culo\s+(\d+[A-Z]?(?:\s*[°º])?)\s*[.\-°]+\s*(.+?)(?=\nArt[íi]culo\s+\d|\Z)",
        re.DOTALL
    )

    # Detectar estructura de títulos (TÍTULO I, CAPÍTULO I, etc.)
    patron_titulo = re.compile(r"T[ÍI]TULO\s+[IVXLCDM]+[^\n]*", re.IGNORECASE)
    patron_cap    = re.compile(r"CAP[ÍI]TULO\s+[IVXLCDM]+[^\n]*", re.IGNORECASE)

    # Dividir el texto en bloques por artículo
    articulos = []
    current_titulo = ""
    current_cap = ""

    for m in patron_art.finditer(ley_text):
        numero = m.group(1).strip().rstrip("°º").strip()
        bloque = m.group(0)

        # Extraer título del artículo (primera línea tras "Artículo N. ")
        primera_linea_m = re.match(
            r"Art[íi]culo\s+\d+[A-Z]?\s*[.\-°]+\s*(.+?)[\n\r]", bloque
        )
        titulo_art = primera_linea_m.group(1).strip() if primera_linea_m else ""

        # Texto del artículo (resto)
        texto_raw = bloque[len(primera_linea_m.group(0)):].strip() if primera_linea_m else bloque
        # Limpiar texto
        texto = re.sub(r"\n{3,}", "\n\n", texto_raw).strip()

        # Actualizar contexto de título/capítulo mirando el texto anterior al artículo
        prev_text = ley_text[:m.start()][-300:]  # 300 chars antes del artículo
        t_match = list(patron_titulo.finditer(prev_text))
        c_match = list(patron_cap.finditer(prev_text))
        if t_match:
            current_titulo = t_match[-1].group(0).strip()
        if c_match:
            current_cap = c_match[-1].group(0).strip()

        articulos.append({
            "numero": numero,
            "titulo": titulo_art,
            "texto": texto,
            "libro": "",
            "titulo_legal": current_titulo,
            "capitulo": current_cap,
            "seccion": "",
            "modificaciones": [],
            "jurisprudencia_url": None,
            "texto_completo": texto,
        })

    print(f"Articulos encontrados: {len(articulos)}")

    if dry_run:
        print("\n--- DRY RUN: primeros 5 articulos ---")
        for a in articulos[:5]:
            print(f"\n  [{a['titulo_legal']}] [{a['capitulo']}]")
            print(f"  Art. {a['numero']} - {a['titulo']}")
            print(f"  Texto: {a['texto'][:120]}...")
        return True

    # Guardar
    out_dir = os.path.normpath(os.path.join(KNOWLEDGE_BASE, output_dir))
    os.makedirs(out_dir, exist_ok=True)

    metadata = {
        "codigo": nombre,
        "decreto": decreto,
        "fuente_url": f"file://{os.path.abspath(pdf_path)}",
        "titulo_pagina": nombre,
        "fecha_scraping": datetime.now(timezone.utc).isoformat(),
        "fecha_actualizacion_fuente": str(datetime.now().year),
        "nota": "Extraído de PDF oficial publicado en El Peruano",
    }
    with open(os.path.join(out_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    with open(os.path.join(out_dir, "articulos.json"), "w", encoding="utf-8") as f:
        json.dump(articulos, f, ensure_ascii=False, indent=2)

    size_kb = os.path.getsize(os.path.join(out_dir, "articulos.json")) // 1024
    print(f"Guardado: {out_dir}/articulos.json ({len(articulos)} articulos, {size_kb} KB)")
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Scraper de Codigos Legales Peruanos (lpderecho.pe + PDFs)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="\n".join(
            f"  --code {k:<8}  {v['nombre']}"
            for k, v in CODIGOS.items()
        ) + "\n  --pdf-ley30077 <ruta.pdf>   Parsear Ley 30077 desde PDF",
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--code", choices=list(CODIGOS.keys()), help="Codigo a scrapear desde web")
    group.add_argument("--all", action="store_true", help="Scrapear todos los codigos web accesibles")
    group.add_argument("--merge-cpp", action="store_true", help="Fusionar cpp + cpp2 en articulos.json completo")
    group.add_argument("--pdf-ley30077", metavar="PDF", help="Ruta al PDF de Ley 30077 (El Peruano)")
    group.add_argument("--spij-ley30364", action="store_true", help="Descargar Ley 30364 desde SPIJ API")
    parser.add_argument("--dry-run", action="store_true", help="Solo muestra los primeros articulos sin guardar")
    parser.add_argument("--from-file", help="Usar HTML local (solo con --code, para debug)")
    args = parser.parse_args()

    if args.merge_cpp:
        ok = merge_cpp()
        sys.exit(0 if ok else 1)

    if args.spij_ley30364:
        ok = scrape_spij(
            norma_id="H1141065",
            nombre="Ley 30364 - Ley para prevenir, sancionar y erradicar la violencia contra las mujeres",
            decreto="Ley N° 30364, publicada el 23 de noviembre de 2015 (TUO aprobado por DS 004-2020-MIMP)",
            output_dir="ley_30364_violencia_mujer",
            dry_run=args.dry_run,
        )
        sys.exit(0 if ok else 1)

    if args.pdf_ley30077:
        ok = scrape_pdf(
            pdf_path=args.pdf_ley30077,
            nombre="Ley 30077 - Ley contra el Crimen Organizado",
            decreto="Ley N° 30077, publicada el 20 de agosto de 2013",
            output_dir="ley_30077_crimen_organizado",
            ley_inicio_patron="LEY N\u00ba 30077",
            dry_run=args.dry_run,
        )
        sys.exit(0 if ok else 1)

    if args.all and args.from_file:
        print("--from-file no es compatible con --all", file=sys.stderr)
        sys.exit(1)

    # Excluir cpp2 de --all (se maneja con --merge-cpp)
    codigos_web = [k for k in CODIGOS if k != "cpp2"]
    codigos_a_scrapear = codigos_web if args.all else [args.code]
    errores = []

    for codigo_key in codigos_a_scrapear:
        from_file = args.from_file if not args.all else None
        ok = scrape_codigo(codigo_key, from_file=from_file, dry_run=args.dry_run)
        if not ok:
            errores.append(codigo_key)

    if args.all:
        print(f"\n{'='*60}")
        print(f"COMPLETADO: {len(codigos_a_scrapear) - len(errores)}/{len(codigos_a_scrapear)} codigos")
        if errores:
            print(f"FALLARON: {', '.join(errores)}")
        print("  Recuerda: ejecuta --merge-cpp para completar el CPP")
        print("  Recuerda: ejecuta --pdf-ley30077 <ruta.pdf> para Ley 30077")

    sys.exit(1 if errores else 0)


if __name__ == "__main__":
    main()
