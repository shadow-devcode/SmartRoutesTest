"""
Grupos de cadenas de un dataset procesado «por multicadenas».

El reparto guarda en Config_Procesamiento los grupos como texto
(«Grupo 1: A, B · Grupo 2: C»). Aquí se leen y se asigna cada mercaderista a su
grupo según las cadenas de sus visitas, para poder filtrar las pantallas por
grupo. En un dataset «por zona» no hay grupos y la lista sale vacía.
"""
from __future__ import annotations

from collections import Counter

from utils.excel_cache import read_excel_cached


def _norm(texto) -> str:
    return " ".join(str(texto or "").split()).upper()


def _leer_grupos(hp: str) -> tuple[str, list[dict]]:
    try:
        cfg = read_excel_cached(hp, "Config_Procesamiento")
    except Exception:
        return "", []
    valores = dict(zip(cfg.iloc[:, 0].astype(str).str.strip(), cfg.iloc[:, 1]))
    tipo = str(valores.get("tipo_carga") or "").strip().lower()
    texto = str(valores.get("grupos_de_cadenas") or "").strip()
    grupos: list[dict] = []
    if tipo == "multicanal" and texto and texto not in ("—", "-", "nan"):
        for parte in texto.split("·"):
            nombre, _, cadenas = parte.partition(":")
            lista = [c.strip() for c in cadenas.split(",") if c.strip()]
            if nombre.strip() and lista:
                grupos.append({"nombre": nombre.strip(), "cadenas": lista})
    return tipo, grupos


def grupos_mercadistas(hp: str | None) -> dict:
    """{grupos: [{nombre, cadenas, mercadistas}], grupo_por_mercadista: {merc: grupo}}."""
    vacio = {"success": True, "tipo_carga": "", "grupos": [], "grupo_por_mercadista": {}}
    if not hp:
        return vacio
    tipo, grupos = _leer_grupos(hp)
    if not grupos:
        return {**vacio, "tipo_carga": tipo}

    grupo_de_cadena = {_norm(c): g["nombre"] for g in grupos for c in g["cadenas"]}
    df = read_excel_cached(hp, "Horarios_Detalle")
    df = df[df["Mercadista"].astype(str).str.strip().str.upper() != "TOTAL"]
    votos: dict = {}
    if "CADENA" in df.columns:
        for merc, cadena in zip(df["Mercadista"].astype(str).str.strip(), df["CADENA"]):
            grupo = grupo_de_cadena.get(_norm(cadena))
            if grupo:
                votos.setdefault(merc, Counter())[grupo] += 1
    # El motor no mezcla grupos en una persona; si por edición manual hubiera
    # mezcla, manda el grupo de la mayoría de sus visitas.
    por_merc = {m: c.most_common(1)[0][0] for m, c in votos.items()}
    for g in grupos:
        g["mercadistas"] = sorted(m for m, n in por_merc.items() if n == g["nombre"])
    return {"success": True, "tipo_carga": tipo, "grupos": grupos, "grupo_por_mercadista": por_merc}
