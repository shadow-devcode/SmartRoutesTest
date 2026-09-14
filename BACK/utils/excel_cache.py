"""
Cache de lectura de Excel invalidado por (mtime, size) del archivo.

pd.read_excel() es costoso (cientos de ms a varios segundos para hojas grandes).
Los endpoints de filtros lo invocan varias veces por click, lo que congela el UI.
Devolvemos siempre .copy() porque los call sites mutan/reasignan el df.
"""
from __future__ import annotations

import os
import threading

import pandas as pd

_EXCEL_CACHE: dict[tuple[str, str], tuple[float, int, pd.DataFrame]] = {}
_EXCEL_CACHE_LOCK = threading.Lock()


def invalidate_excel_cache(path: str) -> None:
    """Elimina todas las entradas del caché para el archivo indicado.

    Llamar después de cualquier escritura al Excel para garantizar que la
    siguiente lectura siempre traiga datos frescos del disco, evitando el
    caso borde en Windows donde mtime no cambia si escritura y lectura
    ocurren dentro del mismo instante de resolución del sistema de archivos.
    """
    abs_path = os.path.abspath(path)
    with _EXCEL_CACHE_LOCK:
        keys_to_del = [k for k in _EXCEL_CACHE if k[0] == abs_path]
        for k in keys_to_del:
            del _EXCEL_CACHE[k]


def actualizar_excel_cache(path: str, sheet_name: str, df: pd.DataFrame) -> None:
    """Deja en el cache la hoja que se acaba de escribir.

    Quien guarda ya tiene el DataFrame en memoria. Sin esto, la escritura
    invalida el cache y la siguiente lectura vuelve a parsear el .xlsx entero:
    1,29 s en el rutero nacional, pagados en cada arrastre del calendario.
    """
    try:
        st = os.stat(path)
    except OSError:
        return
    key = (os.path.abspath(path), sheet_name)
    with _EXCEL_CACHE_LOCK:
        _EXCEL_CACHE[key] = (st.st_mtime, st.st_size, df.copy())


def read_excel_cached(path: str, sheet_name: str) -> pd.DataFrame:
    """Lee una hoja de Excel con cache invalidado por mtime+size del archivo.

    Lanza la misma excepción que pd.read_excel si la lectura falla (hoja
    inexistente, archivo corrupto, etc.). Devuelve una copia para que las
    mutaciones aguas abajo no contaminen el cache.
    """
    st = os.stat(path)
    mtime, size = st.st_mtime, st.st_size
    key = (os.path.abspath(path), sheet_name)
    with _EXCEL_CACHE_LOCK:
        cached = _EXCEL_CACHE.get(key)
        if cached is not None and cached[0] == mtime and cached[1] == size:
            return cached[2].copy()
    df = pd.read_excel(path, sheet_name=sheet_name)
    with _EXCEL_CACHE_LOCK:
        _EXCEL_CACHE[key] = (mtime, size, df)
    return df.copy()
