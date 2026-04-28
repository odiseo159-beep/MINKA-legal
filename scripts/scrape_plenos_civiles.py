#!/usr/bin/env python3
"""
scrape_plenos_civiles.py — Scraper de Plenos Casatorios Civiles peruanos

Fuente: https://lpderecho.pe/descargue-todos-los-plenos-casatorios-civiles-pdf/
Cada Pleno es un precedente vinculante de la Corte Suprema (alta jerarquía).
Los abogados deben citarlos al argumentar en casos civiles relevantes.

Genera knowledge/normativa/plenos_casatorios_civiles/:
  metadata.json   — fuente, fecha de scraping, lista de plenos
  articulos.json  — un "artículo" por Pleno con el contenido completo

Uso:
  python scripts/scrape_plenos_civiles.py            # scrape todo
  python scripts/scrape_plenos_civiles.py --dry-run  # solo imprime, no guarda
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone

import httpx
from bs4 import BeautifulSoup, Tag

# ---------------------------------------------------------------------------
# Registro de los 10 Plenos Casatorios Civiles publicados a la fecha
# ---------------------------------------------------------------------------

PLENOS = [
    {
        "numero": "I",
        "casacion": "1465-2007-Cajamarca",
        "materia": "Indemnización por daños y perjuicios derivados de responsabilidad extracontractual",
        "url": "https://lpderecho.pe/i-pleno-casatorio-indemnizacion-danos-perjuicios-derivados-responsabilidad-extracontractual/",
    },
    {
        "numero": "II",
        "casacion": "2229-2008-Lambayeque",
        "materia": "Prescripción adquisitiva de dominio",
        "url": "https://lpderecho.pe/ii-pleno-casatorio-civil-prescripcion-adquisitiva-de-dominio/",
    },
    {
        "numero": "III",
        "casacion": "4664-2010-Puno",
        "materia": "Divorcio por causal de separación de hecho",
        "url": "https://lpderecho.pe/iii-pleno-casatorio-civil-indemnizacion-en-el-proceso-de-divorcio-por-causal-de-separacion-de-hecho/",
    },
    {
        "numero": "IV",
        "casacion": "2195-2011-Ucayali",
        "materia": "Desalojo por ocupación precaria",
        "url": "https://lpderecho.pe/iv-pleno-casatorio-civil-desalojo-ocupacion-precaria/",
    },
    {
        "numero": "V",
        "casacion": "3189-2012-Lima Norte",
        "materia": "Impugnación de acuerdos asociativos",
        "url": "https://lpderecho.pe/v-pleno-casatorio-civil-impugnacion-acuerdos-asociativos/",
    },
    {
        "numero": "VI",
        "casacion": "2402-2012-Lambayeque",
        "materia": "Ejecución de garantías reales (hipoteca)",
        "url": "https://lpderecho.pe/vi-pleno-casatorio-civil-ejecucion-garantias/",
    },
    {
        "numero": "VII",
        "casacion": "3671-2014-Lima",
        "materia": "Tercería de propiedad: propiedad no inscrita vs. embargo inscrito",
        "url": "https://lpderecho.pe/vii-pleno-casatorio-civil-propiedad-no-inscrita-vs-embargo-inscrito/",
    },
    {
        "numero": "VIII",
        "casacion": "3006-2015-Junín",
        "materia": "Actos de disposición de bienes sociales por un solo cónyuge",
        "url": "https://lpderecho.pe/gano-nulidad-publican-sentencia-viii-pleno-casatorio-civil-casacion-3006-2015-junin/",
    },
    {
        "numero": "IX",
        "casacion": "4442-2015-Moquegua",
        "materia": "Nulidad manifiesta del negocio jurídico declarable de oficio (otorgamiento de escritura pública)",
        "url": "https://lpderecho.pe/ix-pleno-casatorio-civil-juez-puede-declarar-de-oficio-la-nulidad-manifiesta-de-un-negocio-juridico/",
    },
    {
        "numero": "X",
        "casacion": "1242-2017-Lima Este",
        "materia": "Prueba de oficio y valoración probatoria (12 reglas vinculantes)",
        "url": "https://lpderecho.pe/x-pleno-casatorio-civil-estableceran-jurisprudencia-vinculante-prueba-oficio/",
    },
]

OUTPUT_DIR = os.path.join("knowledge", "normativa", "plenos_casatorios_civiles")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Minka Legal AI scraper)",
    "Accept": "text/html",
}


def fetch(url: str) -> str:
    with httpx.Client(timeout=30.0, follow_redirects=True, headers=HEADERS) as client:
        r = client.get(url)
        r.raise_for_status()
        return r.text


def _limpiar(texto: str) -> str:
    """Normaliza espacios y elimina caracteres invisibles raros."""
    texto = texto.replace("\xa0", " ").replace("​", "")
    texto = re.sub(r"[ \t]+", " ", texto)
    texto = re.sub(r"\n{3,}", "\n\n", texto)
    return texto.strip()


def extraer_contenido(html: str) -> dict:
    """Extrae el contenido relevante del artículo de lpderecho.pe.

    Estrategia: buscar el contenedor principal del artículo (article / .entry-content)
    y extraer todos los párrafos. Filtra menús, sidebars y comentarios.
    """
    soup = BeautifulSoup(html, "html.parser")

    # lpderecho.pe usa WordPress con clases típicas — probar varios selectores
    candidatos = [
        soup.find("div", class_="entry-content"),
        soup.find("article"),
        soup.find("main"),
    ]
    article = next((c for c in candidatos if c), None)
    if not article:
        return {"texto": "", "sumilla": "", "reglas": ""}

    # Extraer encabezados + párrafos en orden
    parrafos: list[str] = []
    for el in article.descendants:
        if not isinstance(el, Tag):
            continue
        if el.name in ("p", "h2", "h3", "h4", "li", "blockquote"):
            txt = el.get_text(" ", strip=True)
            if not txt or len(txt) < 5:
                continue
            # Filtrar boilerplate
            low = txt.lower()
            if any(s in low for s in [
                "deja un comentario", "compartir en", "facebook", "twitter",
                "whatsapp", "suscríbete", "click aquí", "lee también",
            ]):
                continue
            if el.name in ("h2", "h3", "h4"):
                parrafos.append(f"\n## {txt}\n")
            else:
                parrafos.append(txt)

    texto_completo = _limpiar("\n".join(parrafos))

    # Detectar sub-secciones populares en plenos casatorios
    sumilla = ""
    m = re.search(r"sumilla[:\s]+(.+?)(?=\n##|\Z)", texto_completo, re.IGNORECASE | re.DOTALL)
    if m:
        sumilla = _limpiar(m.group(1))[:1000]

    reglas = ""
    m = re.search(
        r"(?:reglas?\s+vinculante[s]?|precedente\s+vinculante|doctrina\s+jurisprudencial)[:\s]*(.+?)(?=\n##|\Z)",
        texto_completo,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        reglas = _limpiar(m.group(1))[:3000]

    return {"texto": texto_completo, "sumilla": sumilla, "reglas": reglas}


def construir_articulo(pleno: dict, contenido: dict) -> dict:
    """Construye un artículo en formato compatible con el corpus normativo."""
    texto_indexable = contenido["texto"] or f"Pleno Casatorio sobre {pleno['materia']}"
    return {
        "numero": pleno["numero"],
        "titulo": f"{pleno['numero']} Pleno Casatorio Civil — {pleno['materia']}",
        "casacion": pleno["casacion"],
        "materia": pleno["materia"],
        "sumilla": contenido["sumilla"],
        "reglas_vinculantes": contenido["reglas"],
        "texto": texto_indexable,
        "fuente_url": pleno["url"],
    }


def guardar(articulos: list[dict], out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    metadata = {
        "codigo": "Plenos Casatorios Civiles",
        "decreto": "Sentencias vinculantes de la Sala Civil de la Corte Suprema de Justicia",
        "fuente_url": "https://lpderecho.pe/descargue-todos-los-plenos-casatorios-civiles-pdf/",
        "titulo_pagina": "Plenos Casatorios Civiles peruanos",
        "fecha_scraping": datetime.now(timezone.utc).isoformat(),
        "fecha_actualizacion_fuente": str(datetime.now().year),
        "nota": "Precedentes vinculantes de la Corte Suprema (Art. 400 CPC). Citables como doctrina jurisprudencial vinculante.",
        "total_plenos": len(articulos),
    }
    with open(os.path.join(out_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "articulos.json"), "w", encoding="utf-8") as f:
        json.dump(articulos, f, ensure_ascii=False, indent=2)
    print(f"\n[DONE] Guardado en {out_dir}/ ({len(articulos)} plenos)")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Solo imprime, no guarda")
    args = ap.parse_args()

    articulos = []
    for pleno in PLENOS:
        print(f"  >> {pleno['numero']} Pleno - {pleno['materia']}")
        try:
            html = fetch(pleno["url"])
            contenido = extraer_contenido(html)
            if not contenido["texto"]:
                print(f"     [WARN] no se pudo extraer contenido de {pleno['url']}")
                continue
            articulo = construir_articulo(pleno, contenido)
            articulos.append(articulo)
            print(f"     [OK] {len(contenido['texto']):,} chars | reglas: {bool(contenido['reglas'])} | sumilla: {bool(contenido['sumilla'])}")
        except Exception as e:
            print(f"     [ERR] {e}")

    if not articulos:
        print("\n[FAIL] No se pudo extraer ningun Pleno. Aborting.")
        sys.exit(1)

    if args.dry_run:
        print(f"\n[DRY RUN] {len(articulos)} plenos extraidos. No se guardo nada.")
        return

    guardar(articulos, OUTPUT_DIR)


if __name__ == "__main__":
    main()
