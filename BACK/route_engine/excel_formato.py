"""
Formato de la hoja Horarios_Detalle y limpieza de filas de totales.

Anchos, cabecera fija, autofiltro y la fila TOTAL con SUBTOTAL para que los
totales respondan a los filtros. Incluye el descarte de las filas de «TOTAL»
que algunas hojas arrastran: no son visitas y falsean cualquier recuento.
"""
from __future__ import annotations

from typing import Optional, Set

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


def _cell_is_total_label(val) -> bool:
    """True si la celda es la etiqueta de fila resumen (no un mercadista)."""
    if val is None:
        return False
    return str(val).strip().upper() == "TOTAL"


def drop_spurious_total_rows_horarios_df(df: pd.DataFrame) -> pd.DataFrame:
    """
    Elimina filas donde Mercadista es «TOTAL» (restos de pie de tabla o Excel mal guardado).
    No afecta mercadistas con nombres distintos.
    """
    if df.empty or "Mercadista" not in df.columns:
        return df
    s = df["Mercadista"].astype(str).str.strip().str.upper()
    return df.loc[s != "TOTAL"].reset_index(drop=True)


def format_horarios_detalle_worksheet(ws, mercadistas_extra: Optional[Set[str]] = None) -> None:
    """
    Tras escribir Horarios_Detalle con pandas: mismos controles que el Excel de rutas generadas
    (filtros automáticos en cabecera, fila TOTAL con SUBTOTAL en columnas de tiempos/km).
    mercadistas_extra: nombres en columna Mercadista a resaltar en verde (solo generación inicial).
    """
    header_row = 1
    # Quitar cualquier fila con «TOTAL» en columna A (Mercadista): pie antiguo, duplicados o
    # filas basura que quedaron al re-leer el Excel; de abajo hacia arriba para no desplazar índices.
    r = ws.max_row
    while r > header_row:
        if _cell_is_total_label(ws.cell(row=r, column=1).value):
            ws.delete_rows(r)
        r -= 1

    last_data_row = ws.max_row
    if last_data_row < header_row + 1:
        return

    max_col = ws.max_column
    ref = f"A1:{get_column_letter(max_col)}{last_data_row}"
    ws.auto_filter.ref = ref

    first_data_row = header_row + 1
    headers = {ws.cell(row=header_row, column=c).value: c for c in range(1, max_col + 1)}

    total_row = last_data_row + 1
    ws.cell(row=total_row, column=1, value="TOTAL").font = Font(bold=True)

    def _set_subtotal(col_idx):
        if not col_idx:
            return
        col_letter = get_column_letter(col_idx)
        ws.cell(
            row=total_row,
            column=col_idx,
            value=f"=SUBTOTAL(109,{col_letter}{first_data_row}:{col_letter}{last_data_row})",
        ).font = Font(bold=True)

    _set_subtotal(headers.get("Tiempo Servicio (min)"))
    _set_subtotal(headers.get("Tiempo entre sucursal (min)"))
    _set_subtotal(headers.get("kilometros entre sucurlas (km)"))

    if mercadistas_extra:
        verde = PatternFill(fill_type="solid", fgColor="00FF00")
        col_merc = headers.get("Mercadista", 1)
        for row_idx in range(first_data_row, last_data_row + 1):
            val = ws.cell(row=row_idx, column=col_merc).value
            if val is not None and str(val).strip() in mercadistas_extra:
                ws.cell(row=row_idx, column=col_merc).fill = verde


def _sumar_tiempo_entre_sucursal_en_servicio(ws) -> None:
    """
    En la hoja Horarios_Detalle, reemplaza el valor mostrado de
    `Tiempo Servicio (min)` por `Tiempo Servicio (min) + Tiempo entre sucursal (min)`
    para cada fila de datos. Se aplica únicamente sobre los bytes que se envían
    al cliente (no modifica el archivo almacenado), por lo que el Excel
    descargado muestra el tiempo total de ocupación por visita, mientras que
    las estadísticas internas (dashboard, porcentajes por provincia, etc.)
    siguen operando sobre el tiempo de servicio puro.

    La fila TOTAL (si existe) se omite: la fila de totales se regenera
    posteriormente en `format_horarios_detalle_worksheet` usando SUBTOTAL,
    que vuelve a sumar los valores ya ajustados.
    """
    header_row = 1
    headers = {}
    for c in range(1, ws.max_column + 1):
        v = ws.cell(row=header_row, column=c).value
        if v is not None:
            headers[str(v).strip()] = c

    col_serv = headers.get("Tiempo Servicio (min)")
    col_entre = headers.get("Tiempo entre sucursal (min)")
    if not col_serv or not col_entre:
        return

    for r in range(header_row + 1, ws.max_row + 1):
        if _cell_is_total_label(ws.cell(row=r, column=1).value):
            continue
        v_serv = ws.cell(row=r, column=col_serv).value
        v_entre = ws.cell(row=r, column=col_entre).value
        try:
            s = float(v_serv) if v_serv is not None and v_serv != "" else 0.0
        except (TypeError, ValueError):
            s = 0.0
        try:
            e = float(v_entre) if v_entre is not None and v_entre != "" else 0.0
        except (TypeError, ValueError):
            e = 0.0
        nuevo = s + e
        ws.cell(row=r, column=col_serv).value = (
            int(nuevo) if nuevo == int(nuevo) else round(nuevo, 2)
        )


# Nombre de la hoja que se lee primero al abrir el Excel descargado.
SHEET_RUTAS_SEMANA = "Rutas_Semana"

# Hojas que el sistema necesita en su copia pero que no se entregan al
# descargar: no aportan nada a quien lee el rutero.
HOJAS_SOLO_INTERNAS = ("Config_Procesamiento", "Categorias")
