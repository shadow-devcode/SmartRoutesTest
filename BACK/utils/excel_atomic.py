"""
Escritura atómica de los Excel del sistema.

`pd.ExcelWriter(path, mode="a")` reescribe el archivo ENCIMA de sí mismo: lo
trunca y lo vuelve a construir entero. Durante esa ventana —cerca de un segundo
con las hojas grandes— el .xlsx del disco no es un zip válido, y cualquier
lectura simultánea revienta con

    zipfile.BadZipFile: Bad CRC-32 for file 'docProps/core.xml'

Las escrituras se serializan entre sí con `with_excel_file_lock`, pero las
LECTURAS no toman ese lock —ni deben: son constantes y bloquearlas dejaría la
pantalla clavada cada vez que alguien arrastra una visita—. Así que el arreglo
no es más bloqueo, es no dejar nunca el archivo a medias.

Aquí se escribe sobre una copia temporal en el mismo directorio y, al terminar,
se mueve encima con `os.replace`, que es atómico: quien lea verá el archivo
anterior completo o el nuevo completo, nunca uno a medio construir.
"""
from __future__ import annotations

import os
import shutil
import uuid
from contextlib import contextmanager


@contextmanager
def escritura_atomica(path: str):
    """Da un destino temporal y lo mueve sobre `path` si todo fue bien.

    La copia previa es necesaria porque los llamadores abren el libro en modo
    "a" (añadir/reemplazar hojas): necesitan el contenido actual para conservar
    las hojas que no tocan.

    El temporal vive en el mismo directorio a propósito: `os.replace` solo es
    atómico dentro del mismo sistema de archivos.
    """
    # El nombre conserva la extensión .xlsx: `pd.ExcelWriter` valida la
    # extensión del destino y rechaza cualquier otra cosa.
    #
    # El sufijo lleva un id único por escritura, no solo el PID: dos hilos del
    # mismo proceso compartían temporal, se pisaban a media escritura y dejaban
    # un .xlsx corrupto («Error -3 while decompressing data») más un
    # FileNotFoundError en el segundo `os.replace`.
    raiz, extension = os.path.splitext(path)
    destino = f"{raiz}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}{extension or '.xlsx'}"
    if os.path.exists(path):
        shutil.copy2(path, destino)
    try:
        yield destino
        os.replace(destino, path)
    except BaseException:
        try:
            os.remove(destino)
        except OSError:
            pass
        raise
