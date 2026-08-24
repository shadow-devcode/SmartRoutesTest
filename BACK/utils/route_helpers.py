"""
Helpers para construir claves y convertir valores de filas de Horarios_Detalle.

Las claves de visita (descripcion + lat/lon redondeados) se usan para identificar
puntos de venta de forma idempotente entre el frontend y el Excel almacenado.
"""
from __future__ import annotations

import math

import pandas as pd

from route_engine.geo import parse_coordenada_a_float

COL_KM_ENTRE_SUCURSALES = "kilometros entre sucurlas (km)"


def clave_ub(desc, lat, lon) -> tuple:
    """Clave única para identificar una visita por descripción y coordenadas."""
    try:
        if desc is None or pd.isna(desc):
            dstr = ""
        else:
            dstr = str(desc).strip()
    except (TypeError, ValueError):
        dstr = str(desc).strip() if desc is not None else ""
    la = parse_coordenada_a_float(lat)
    lo = parse_coordenada_a_float(lon)
    if la is not None:
        la = round(la, 6)
    if lo is not None:
        lo = round(lo, 6)
    return (dstr, la, lo)


def clave_pendiente(desc, lat, lon, semana) -> tuple:
    """Clave única para identificar una visita pendiente: punto + semana."""
    base = clave_ub(desc, lat, lon)
    sem = ""
    try:
        if semana is not None and not (isinstance(semana, float) and pd.isna(semana)):
            sem = str(semana).strip().lower()
    except (TypeError, ValueError):
        sem = str(semana).strip().lower() if semana is not None else ""
    return (*base, sem)


def a_numero(v):
    """Convierte a número (coma decimal, comillas en texto, etc.). Devuelve pd.NA si no se puede."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return pd.NA
    f = parse_coordenada_a_float(v)
    return f if f is not None else pd.NA


def km_entre_sucursales_row(row) -> float:
    """Km desde la parada anterior (columna Horarios_Detalle); valor seguro para JSON."""
    v = row.get(COL_KM_ENTRE_SUCURSALES, 0)
    if v is None or pd.isna(v):
        return 0.0
    try:
        x = float(v)
        return 0.0 if x != x else x
    except (TypeError, ValueError):
        return 0.0


def lat_lon_desde_celda_excel(v):
    """Convierte celda de latitud/longitud a float o None (acepta comillas y texto sucio)."""
    if v is None or pd.isna(v):
        return None
    return parse_coordenada_a_float(v)


def coords_validas(lat, lon) -> bool:
    """
    True si (lat, lon) es un par geográfico utilizable.

    Defiende contra celdas Excel corruptas donde pandas/openpyxl pierden el
    punto decimal o agregan exponentes spurios (ej. lat=-327020085467353,
    lng=-7.91e15). Esos valores pasan los chequeos de None/NaN/0 pero hacen
    que Mapbox bloquee el main thread intentando proyectarlos.
    """
    if lat is None or lon is None:
        return False
    try:
        la = float(lat)
        lo = float(lon)
    except (TypeError, ValueError):
        return False
    if la != la or lo != lo:  # NaN
        return False
    if la == 0 and lo == 0:
        return False
    return -90.0 <= la <= 90.0 and -180.0 <= lo <= 180.0


def _recortar_magnitud(valor, max_abs: float, prefer_abs: float):
    """
    Si abs(valor) > max_abs, divide por 10 hasta que entre en rango.

    Recupera coordenadas donde pandas perdió el punto decimal o agregó un
    exponente espurio (ej. -399008796474345 → -3.99008796474345).

    `prefer_abs` es la magnitud "típica" de una coord real. Si tras la
    primera división al rango global el resultado todavía excede esa
    magnitud preferida, dividimos una vez más — resuelve la ambigüedad de
    qué tantos 10× recortar cuando hay múltiples soluciones en rango.

    Ej. -399008796474345 con max_abs=90, prefer_abs=9:
        /10^13 = -39.9   (entra en [-90, 90] pero excede prefer)
        /10^14 = -3.99   (entra en prefer → respuesta final, Ecuador real)

    Devuelve None si la entrada no es parseable o no es finita.
    """
    f = parse_coordenada_a_float(valor)
    if f is None or not math.isfinite(f):
        return None
    if abs(f) <= max_abs:
        return f
    sign = -1.0 if f < 0 else 1.0
    av = abs(f)
    while av > max_abs:
        av /= 10.0
        if av == 0:  # Safety: nunca debería pasar
            return None
    # Resolver ambigüedad: si quedó en (prefer, max_abs], probablemente
    # quedó un dígito entero de más; recortamos una vez más.
    if av > prefer_abs:
        av /= 10.0
    return sign * av


def recortar_lat(valor):
    """Lat parseada y recortada a [-90, 90]. Para coords ambiguas prefiere
    magnitud ≤ 9 (lat típica tiene 1 dígito entero o ninguno)."""
    return _recortar_magnitud(valor, max_abs=90.0, prefer_abs=9.0)


def recortar_lon(valor):
    """Lon parseada y recortada a [-180, 180]. Para coords ambiguas prefiere
    magnitud ≤ 99 (lon típica tiene 1-2 dígitos enteros)."""
    return _recortar_magnitud(valor, max_abs=180.0, prefer_abs=99.0)
