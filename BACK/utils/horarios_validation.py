"""
Validación de columnas mínimas de la hoja Horarios_Detalle.

Si falta alguna de estas columnas el archivo está corrupto/mal generado y los
endpoints fallarían con KeyError; estas funciones lo detectan a tiempo y
permiten devolver un mensaje en español al frontend.
"""
from __future__ import annotations

from typing import Iterable

HORARIOS_REQUIRED_COLS: tuple[str, ...] = ("Mercadista", "Día", "Fecha")


def horarios_missing_columns(df) -> list[str]:
    """Devuelve la lista de columnas requeridas que faltan en df, o lista vacía si todo OK."""
    if df is None:
        return list(HORARIOS_REQUIRED_COLS)
    return [c for c in HORARIOS_REQUIRED_COLS if c not in df.columns]


def horarios_corruption_message(missing: Iterable[str], *, comparativa: bool = False) -> str:
    """Mensaje único, en español, explicando qué falta y qué hacer."""
    cols = ", ".join(f"'{c}'" for c in missing)
    archivo = "comparativa" if comparativa else "de horarios"
    return (
        f"El archivo {archivo} está incompleto: falta(n) la(s) columna(s) {cols}. "
        "Vuelve a generar las rutas desde un Excel de entrada válido o "
        "activa un dataset anterior desde el panel de administración."
    )
