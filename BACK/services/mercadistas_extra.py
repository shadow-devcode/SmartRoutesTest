"""
Mercaderistas dados de alta sin puntos.

Los mercaderistas salen de las filas de Horarios_Detalle, así que uno recién
creado no existe hasta su primera visita. Esta hoja interna los guarda para
que el calendario los liste y se les pueda arrastrar pendientes.
"""
from __future__ import annotations

import re

import pandas as pd

from utils.excel_cache import read_excel_cached

SHEET_MERCADISTAS_EXTRA = "Mercadistas_Extra"
JORNADA_SEMANA = "lunes-viernes"
JORNADA_FIN_SEMANA = "miercoles-domingo"
COLUMNAS = ["Mercadista", "Jornada"]
_DIAS = {
    JORNADA_SEMANA: ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"],
    JORNADA_FIN_SEMANA: ["Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"],
}


def leer_mercadistas_extra(hp: str) -> pd.DataFrame:
    """Hoja de mercaderistas vacíos; vacía si el Excel aún no la tiene."""
    try:
        df = read_excel_cached(hp, SHEET_MERCADISTAS_EXTRA)
    except Exception:
        return pd.DataFrame(columns=COLUMNAS)
    for col in COLUMNAS:
        if col not in df.columns:
            df[col] = ""
    df = df[COLUMNAS].copy()
    df["Mercadista"] = df["Mercadista"].astype(str).str.strip()
    return df[df["Mercadista"] != ""].reset_index(drop=True)


def jornadas_extra(hp: str, con_filas) -> dict:
    """{nombre: 'semana' | 'fin_semana'} de los vacíos que aún no tienen filas."""
    salida: dict = {}
    for _, fila in leer_mercadistas_extra(hp).iterrows():
        nombre = fila["Mercadista"]
        if nombre in con_filas:
            continue
        jornada = str(fila["Jornada"] or "").strip()
        salida[nombre] = "fin_semana" if jornada == JORNADA_FIN_SEMANA else "semana"
    return salida


def dias_de_jornada(jornada: str) -> list:
    return list(_DIAS.get(jornada, _DIAS[JORNADA_SEMANA]))


def siguiente_nombre_mercadista(nombres) -> str:
    """'Mercadista NN' con el número siguiente al mayor que exista."""
    mayor = 0
    for nombre in nombres:
        m = re.search(r"(\d+)\s*$", str(nombre))
        if m:
            mayor = max(mayor, int(m.group(1)))
    return f"Mercadista {mayor + 1:02d}"
