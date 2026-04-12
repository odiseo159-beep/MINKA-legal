#!/usr/bin/env python3
"""
enriquecer_procesos.py — Puebla articulos_aplicables en procesos_legales.json
con texto real de los artículos scrapeados.

Lee el campo 'norma' de cada etapa, busca los artículos en los JSON de normativa,
e inyecta el texto real en 'articulos_aplicables'.

Uso:
  python scripts/enriquecer_procesos.py            # Actualiza procesos_legales.json
  python scripts/enriquecer_procesos.py --dry-run  # Muestra resultados sin escribir
"""

import argparse
import json
import os
import re
import sys

# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------
BASE_DIR        = os.path.join(os.path.dirname(__file__), "..")
PROCESOS_PATH   = os.path.join(BASE_DIR, "knowledge", "procesos_legales.json")
NORMATIVA_DIR   = os.path.join(BASE_DIR, "knowledge", "normativa")

# ---------------------------------------------------------------------------
# Mapeo de abreviaturas del campo 'norma' → carpeta en knowledge/normativa/
# ---------------------------------------------------------------------------
ABREV_A_CARPETA = {
    # Código Procesal Penal
    "CPP":   ("codigo_procesal_penal",      "CPP",    "Código Procesal Penal"),
    # Código Penal
    "CP":    ("codigo_penal",               "CP",     "Código Penal"),
    # Código Procesal Civil
    "CPC":   ("codigo_procesal_civil",      "CPC",    "Código Procesal Civil"),
    # Código Civil
    "CC":    ("codigo_civil",               "CC",     "Código Civil"),
    # Código de los Niños y Adolescentes
    "CNA":   ("codigo_ninos_adolescentes",  "CNA",    "Código de los Niños y Adolescentes"),
    # Código de Ejecución Penal
    "CEP":   ("codigo_ejecucion_penal",     "CEP",    "Código de Ejecución Penal"),
    # Constitución
    "CONST": ("constitucion",               "CONST",  "Constitución Política del Perú"),
    # Leyes especiales
    "29497": ("ley_29497_nlpt",             "NLPT",   "Ley 29497 - Nueva Ley Procesal del Trabajo"),
    "NLPT":  ("ley_29497_nlpt",             "NLPT",   "Ley 29497 - Nueva Ley Procesal del Trabajo"),
    "30077": ("ley_30077_crimen_organizado","L30077",  "Ley 30077 - Crimen Organizado"),
    "30364": ("ley_30364_violencia_mujer",  "L30364",  "Ley 30364 - Violencia contra la Mujer"),
}

# Alias adicionales para texto libre en el campo norma
ALIAS_NORMA = {
    "Ley N° 29497":               "29497",
    "Ley Nº 29497":               "29497",
    "Ley N\u00ba 29497":          "29497",
    "Ley 29497":                  "29497",
    "Ley N° 30364":               "30364",
    "Ley Nº 30364":               "30364",
    "Ley N\u00ba 30364":          "30364",
    "Ley 30364":                  "30364",
    "Ley N° 30077":               "30077",
    "Ley Nº 30077":               "30077",
    "Ley N\u00ba 30077":          "30077",
    "Ley 30077":                  "30077",
    "Código Penal":               "CP",
    "C\u00f3digo Penal":          "CP",
    "Código Procesal Penal":      "CPP",
    "C\u00f3digo Procesal Penal": "CPP",
    "Código Procesal Civil":      "CPC",
    "C\u00f3digo Procesal Civil": "CPC",
    "Código Civil":               "CC",
    "C\u00f3digo Civil":          "CC",
    "Código de los Niños y Adolescentes": "CNA",
    "C\u00f3digo de los Ni\u00f1os y Adolescentes": "CNA",
}

# ---------------------------------------------------------------------------
# Carga de artículos en memoria (índice por código+número)
# ---------------------------------------------------------------------------

def cargar_indice_normativa() -> dict:
    """
    Retorna: { "CPP": {"326": {numero, titulo, texto, citacion}, ...}, "CP": {...}, ... }
    """
    indice = {}
    for abrev, (carpeta, codigo, nombre) in ABREV_A_CARPETA.items():
        if codigo in indice:
            continue  # ya cargado por otro alias
        ruta = os.path.join(NORMATIVA_DIR, carpeta, "articulos.json")
        if not os.path.exists(ruta):
            print(f"  [OMITIDO] {carpeta} — no encontrado")
            continue
        with open(ruta, encoding="utf-8") as f:
            arts = json.load(f)
        indice[codigo] = {}
        for a in arts:
            num = a.get("numero", "").strip()
            if num:
                indice[codigo][num] = {
                    "numero":   num,
                    "titulo":   a.get("titulo", ""),
                    "texto":    a.get("texto", ""),
                    "citacion": f"Art. {num} del {nombre}",
                    "codigo":   codigo,
                }
        print(f"  {codigo:<8} {len(indice[codigo]):>5} arts cargados")
    return indice


# ---------------------------------------------------------------------------
# Parser del campo 'norma'
# ---------------------------------------------------------------------------

def normalizar_norma(norma_raw: str) -> str:
    """Reemplaza alias de texto libre por abreviaturas cortas."""
    s = norma_raw
    # Ordenar alias de mayor a menor longitud para evitar match parcial
    for alias, abrev in sorted(ALIAS_NORMA.items(), key=lambda x: -len(x[0])):
        s = s.replace(alias, abrev)
    return s


def parsear_numeros(nums_str: str) -> list[str]:
    """
    Convierte "16, 17 y 42", "253 al 320", "330-333" en lista de números.
    Para rangos grandes (>15 arts) solo incluye primero y último.
    Soporta sub-artículos como "566-A", "68-B".
    Ignora "28.2" (párrafos) y "y ss." (sin expansión).
    """
    nums_str = nums_str.strip()

    # Rango "X al Y"
    rango_al = re.match(r"^(\d+)\s+al\s+(\d+)$", nums_str)
    if rango_al:
        ini, fin = int(rango_al.group(1)), int(rango_al.group(2))
        if fin - ini <= 15:
            return [str(n) for n in range(ini, fin + 1)]
        return [str(ini), str(fin)]

    # Rango "X-Y" solo cuando ambos lados son números (330-333), NO sub-arts (68-A)
    rango_guion = re.match(r"^(\d+)-(\d+)$", nums_str)
    if rango_guion:
        ini, fin = int(rango_guion.group(1)), int(rango_guion.group(2))
        if fin - ini <= 15:
            return [str(n) for n in range(ini, fin + 1)]
        return [str(ini), str(fin)]

    # Lista separada por comas / " y " — puede contener rangos internos
    partes = re.split(r",|\s+y\s+", nums_str)
    resultado = []
    for p in partes:
        p = p.strip()
        if not p:
            continue
        # Rango interno "330-333"
        r = re.match(r"^(\d+)-(\d+)$", p)
        if r:
            ini, fin = int(r.group(1)), int(r.group(2))
            if fin - ini <= 15:
                resultado.extend(str(n) for n in range(ini, fin + 1))
            else:
                resultado += [str(ini), str(fin)]
            continue
        # Rango interno "330 al 333"
        r2 = re.match(r"^(\d+)\s+al\s+(\d+)$", p)
        if r2:
            ini, fin = int(r2.group(1)), int(r2.group(2))
            if fin - ini <= 15:
                resultado.extend(str(n) for n in range(ini, fin + 1))
            else:
                resultado += [str(ini), str(fin)]
            continue
        # Sub-artículo: "68-A", "566-A", "121-B"
        if re.match(r"^\d+[-–][A-Z]$", p, re.IGNORECASE):
            resultado.append(p.upper())
            continue
        # Número simple (puede tener °/º): "196", "59°"
        m = re.match(r"^(\d+)[°º]?$", p)
        if m:
            resultado.append(m.group(1))
            continue
        # "X y ss." → solo X
        m2 = re.match(r"^(\d+)\s+ss\.?$", p)
        if m2:
            resultado.append(m2.group(1))
            continue
        # Ignorar "28.2" (numeración de párrafo), "D.Leg.", etc.

    return resultado


def parsear_norma(norma_raw: str) -> list[tuple[str, str]]:
    """
    Parsea el campo norma y retorna lista de (codigo, numero_articulo).

    Soporta:
      "Arts. 326, 328, 329, CPP"           → coma antes del código
      "Art. 326 CPP; Art. 189 CP"          → espacio antes del código
      "Arts. 330-333 CPP"                  → rango con guión
      "Arts. 15, 16, 23, 24 Ley 30364"    → código al final sin coma
      "Art. 185 CP (hurto simple)"         → texto entre paréntesis (ignorado)
    """
    norma = normalizar_norma(norma_raw)
    resultados = []

    # Construir patrón para reconocer códigos (orden: más largos primero)
    codigos_sorted = sorted(ABREV_A_CARPETA.keys(), key=len, reverse=True)
    patron_codigo = "|".join(re.escape(k) for k in codigos_sorted)

    # Dividir por ";" para segmentos independientes
    segmentos = re.split(r"\s*;\s*", norma)

    for seg in segmentos:
        seg = seg.strip()
        if not seg:
            continue

        # Quitar texto entre paréntesis (descripciones como "(prisión preventiva)")
        seg_limpio = re.sub(r"\([^)]*\)", "", seg).strip()

        # Detectar el código: puede ir precedido de "," o " " (espacio)
        match = re.search(
            r"[,\s]\s*(" + patron_codigo + r")\b",
            seg_limpio
        )
        if not match:
            continue

        codigo_abrev = match.group(1)
        _, codigo, _ = ABREV_A_CARPETA[codigo_abrev]

        # Parte de números: todo lo que está antes del match del código
        nums_parte = seg_limpio[:match.start()].strip()

        # Quitar prefijo "Arts.", "Art."
        nums_parte = re.sub(r"^Arts?\.\s*", "", nums_parte, flags=re.IGNORECASE).strip()

        if not nums_parte:
            continue

        numeros = parsear_numeros(nums_parte)
        for n in numeros:
            if (codigo, n) not in resultados:  # evitar duplicados dentro del mismo campo
                resultados.append((codigo, n))

    return resultados


# ---------------------------------------------------------------------------
# Búsqueda en índice
# ---------------------------------------------------------------------------

TEXTO_MAX = 350   # caracteres de texto a guardar en articulos_aplicables

def buscar_articulo(indice: dict, codigo: str, numero: str) -> dict | None:
    """Busca el artículo en el índice. Retorna dict con citacion, texto, etc."""
    tabla = indice.get(codigo, {})
    art = tabla.get(numero)
    if art:
        return {
            "codigo":   art["codigo"],
            "numero":   art["numero"],
            "titulo":   art["titulo"],
            "citacion": art["citacion"],
            "texto":    art["texto"][:TEXTO_MAX] + ("..." if len(art["texto"]) > TEXTO_MAX else ""),
        }
    return None


# ---------------------------------------------------------------------------
# Procesamiento principal
# ---------------------------------------------------------------------------

def enriquecer(dry_run: bool = False) -> None:
    print("Cargando índice de normativa...")
    indice = cargar_indice_normativa()
    print()

    print(f"Leyendo {PROCESOS_PATH}...")
    with open(PROCESOS_PATH, encoding="utf-8") as f:
        data = json.load(f)

    procesos = data["procesos"]

    total_etapas    = 0
    total_arts      = 0
    total_no_enc    = 0
    no_encontrados  = []

    for proc_key, proc in procesos.items():
        etapas = proc.get("etapas", [])
        for etapa in etapas:
            total_etapas += 1
            norma = etapa.get("norma", "")
            if not norma:
                continue

            referencias = parsear_norma(norma)
            articulos_aplicables = []

            for codigo, numero in referencias:
                art = buscar_articulo(indice, codigo, numero)
                if art:
                    articulos_aplicables.append(art)
                    total_arts += 1
                else:
                    no_encontrados.append(f"{codigo} Art.{numero} ({proc_key}/{etapa['id']})")
                    total_no_enc += 1

            etapa["articulos_aplicables"] = articulos_aplicables

            if dry_run and articulos_aplicables:
                print(f"  [{proc_key}] {etapa['id']}")
                for a in articulos_aplicables:
                    print(f"    -> {a['citacion']}: {a['texto'][:80]}...")
                print()

    print(f"\nResultado:")
    print(f"  Etapas procesadas   : {total_etapas}")
    print(f"  Artículos enlazados : {total_arts}")
    print(f"  No encontrados      : {total_no_enc}")

    if no_encontrados:
        print(f"\n  Artículos no encontrados ({len(no_encontrados)}):")
        for nf in no_encontrados[:30]:
            print(f"    {nf}")
        if len(no_encontrados) > 30:
            print(f"    ... y {len(no_encontrados)-30} más")

    if not dry_run:
        with open(PROCESOS_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"\nGuardado: {PROCESOS_PATH}")
    else:
        print("\n[DRY-RUN] No se escribieron cambios.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Enriquece procesos_legales.json con artículos reales")
    parser.add_argument("--dry-run", action="store_true", help="Muestra resultados sin escribir el archivo")
    args = parser.parse_args()
    enriquecer(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
