"""
Cache del libro openpyxl abierto, para no releerlo en cada edición.

Guardar una hoja con pandas en modo "a" carga el .xlsx entero, reemplaza la
hoja y lo vuelve a escribir: 3,07 s en el rutero nacional, de los cuales 1,67 s
son solo abrir el archivo. Como todas las escrituras pasan por el lock del
Excel, el libro puede quedarse en memoria entre una edición y la siguiente.

El cache se invalida por (mtime, size), igual que el de DataFrames: si alguien
toca el archivo por fuera, la siguiente lectura lo relee.
"""
from __future__ import annotations

import os
import threading

import openpyxl

# path absoluto -> (mtime, size, Workbook). Se guarda solo el último libro: en
# la práctica se edita un dataset a la vez y cada libro ocupa decenas de MB.
_LIBRO: dict = {}
_LOCK = threading.Lock()


def cargar_libro(path: str):
    """Libro openpyxl de `path`, reutilizando el de la última escritura.

    El objeto se devuelve TAL CUAL, no una copia: quien lo pide está dentro del
    lock del archivo, así que nadie más lo está tocando.
    """
    abs_path = os.path.abspath(path)
    try:
        st = os.stat(path)
    except OSError:
        return openpyxl.load_workbook(path)

    with _LOCK:
        guardado = _LIBRO.get(abs_path)
        if guardado and guardado[0] == st.st_mtime and guardado[1] == st.st_size:
            return guardado[2]

    libro = openpyxl.load_workbook(path)
    with _LOCK:
        _LIBRO.clear()
        _LIBRO[abs_path] = (st.st_mtime, st.st_size, libro)
    return libro


def registrar_libro(path: str, libro) -> None:
    """Anota el libro recién guardado como el vigente de `path`."""
    try:
        st = os.stat(path)
    except OSError:
        return
    with _LOCK:
        _LIBRO.clear()
        _LIBRO[os.path.abspath(path)] = (st.st_mtime, st.st_size, libro)


def olvidar_libro(path: str) -> None:
    """Descarta el libro cacheado de `path`."""
    with _LOCK:
        _LIBRO.pop(os.path.abspath(path), None)
