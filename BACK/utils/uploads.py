"""Constantes y validaciones para subidas de archivos Excel."""
from __future__ import annotations

import os
import zipfile

UPLOAD_FOLDER = "uploads"
ALLOWED_EXTENSIONS: frozenset[str] = frozenset({"xlsx", "xls"})

# Nombres legacy en la raíz del proyecto. Se mantienen para compatibilidad con
# despliegues anteriores; los nuevos datasets viven dentro de datasets/<uuid>/.
OUTPUT_FILE = "minoristas_horarios.xlsx"
COMPARATIVA_FILE = "minoristas_horarios_comparativa.xlsx"

# --- Guardas anti zip-bomb para .xlsx (un .xlsx es un ZIP con XML adentro) -
# Un archivo pequeño puede descomprimirse a un tamaño descomunal y agotar
# memoria/disco al procesarlo (pandas/openpyxl descomprimen todo en RAM).
# Límites generosos para hojas legítimas grandes, pero que cortan los
# patrones clásicos de "bomba" (ratios de miles a uno).
MAX_XLSX_UNCOMPRESSED_MB = 500
MAX_XLSX_COMPRESSION_RATIO = 200


class UnsafeExcelError(Exception):
    """Se eleva cuando un .xlsx subido parece ser un zip-bomb o un ZIP inválido."""


def ensure_upload_folder() -> None:
    """Crea la carpeta de uploads si no existe; idempotente."""
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)


def allowed_file(filename: str | None) -> bool:
    """True si filename termina en una extensión permitida."""
    if not filename:
        return False
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def validar_xlsx_no_es_zip_bomb(origen) -> None:
    """
    Valida que un .xlsx subido (ruta en disco o file-like con bytes) no sea un
    "zip bomb": revisa el tamaño comprimido/descomprimido de cada entrada del
    ZIP usando solo los metadatos del directorio central, SIN descomprimir
    nada, y rechaza el archivo si el total descomprimido o el ratio de alguna
    entrada excede los límites configurados.

    Eleva `UnsafeExcelError` si el archivo no es un ZIP válido o parece una bomba.
    """
    try:
        with zipfile.ZipFile(origen) as zf:
            total_uncompressed = 0
            limite_total = MAX_XLSX_UNCOMPRESSED_MB * 1024 * 1024
            for info in zf.infolist():
                total_uncompressed += info.file_size
                if total_uncompressed > limite_total:
                    raise UnsafeExcelError(
                        "El archivo Excel se descomprime a un tamaño excesivo."
                    )
                if info.compress_size > 0:
                    ratio = info.file_size / info.compress_size
                    if ratio > MAX_XLSX_COMPRESSION_RATIO:
                        raise UnsafeExcelError(
                            "El archivo Excel tiene un ratio de compresión sospechoso "
                            "(posible archivo manipulado o corrupto)."
                        )
    except zipfile.BadZipFile as exc:
        raise UnsafeExcelError("El archivo no es un .xlsx válido (ZIP corrupto).") from exc
