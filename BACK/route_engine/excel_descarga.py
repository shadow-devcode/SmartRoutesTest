"""
Hoja «Rutas_Semana» y empaquetado del Excel que se descarga.

Una fila por punto y mercaderista, con los días de la semana como columnas y
los minutos de cada jornada en su celda. El pie usa SUBTOTAL para que los
totales sigan a los filtros, y las hojas internas se retiran de la copia que se
entrega: al sistema le hacen falta, a quien recibe el archivo no.
"""
from __future__ import annotations

from io import BytesIO

import openpyxl
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from route_engine.config import DAY_NAMES, ORDEN_SEMANA
from route_engine.excel_formato import (
    drop_spurious_total_rows_horarios_df,
    format_horarios_detalle_worksheet,
)

SHEET_RUTAS_SEMANA = "Rutas_Semana"

# Hojas que el sistema necesita en su copia pero que no se entregan al
# descargar: no aportan nada a quien lee el rutero.
HOJAS_SOLO_INTERNAS = ("Config_Procesamiento", "Categorias", "Mercadistas_Extra")


def _construir_hoja_rutas_semana(wb, abs_path: str) -> None:
    """
    Añade al libro descargado una hoja con UNA FILA POR PUNTO Y SEMANA y los
    días como columnas, en vez de una fila por visita con la columna «Día».

    Por qué: leer una ruta en el formato de una fila por visita obliga a
    reconstruir mentalmente la semana de cada tienda saltando entre filas. Con
    los días en columnas se ve de un vistazo qué días se visita cada punto y
    cuánto tiempo ocupa cada jornada.

    Se añade como PRIMERA hoja, y `Horarios_Detalle` se conserva detrás: es la
    que lee la aplicación y la que permite volver a subir el archivo como
    comparativa, además de guardar el horario y el orden de cada visita, que
    aquí no caben.
    """
    from route_engine.config import ORDEN_SEMANA

    df = pd.read_excel(abs_path, sheet_name="Horarios_Detalle")
    if df.empty or "Día" not in df.columns:
        return

    df = drop_spurious_total_rows_horarios_df(df)
    df["Día"] = df["Día"].astype(str).str.strip()

    col_serv = "Tiempo Servicio (min)"
    col_viaje = "Tiempo entre sucursal (min)"
    if col_serv not in df.columns:
        return
    # Mismo criterio que la hoja de detalle descargada: el tiempo que muestra
    # cada día es lo que ocupa la visita, servicio + desplazamiento hasta ella.
    ocupacion = pd.to_numeric(df[col_serv], errors="coerce").fillna(0.0)
    if col_viaje in df.columns:
        ocupacion = ocupacion + pd.to_numeric(df[col_viaje], errors="coerce").fillna(0.0)
    df["_ocupacion"] = ocupacion

    claves = [
        c
        for c in (
            "Mercadista", "Fecha", "Descripción", "CANAL", "CADENA",
            "PROVINCIA", "CIUDAD", "CALLE", "Latitud", "Longitud",
        )
        if c in df.columns
    ]

    dias_presentes = [d for d in ORDEN_SEMANA if d in set(df["Día"])]
    if not dias_presentes:
        return

    # `pivot_table` descarta las filas cuyo índice tenga un vacío, y columnas
    # como CALLE o CANAL vienen vacías a menudo: sin esto la hoja salía sin una
    # sola fila. Los textos se rellenan con cadena vacía y las coordenadas con 0.
    for columna in claves:
        if columna in ("Latitud", "Longitud"):
            df[columna] = pd.to_numeric(df[columna], errors="coerce").fillna(0.0)
        else:
            df[columna] = df[columna].fillna("").astype(str).str.strip()

    # groupby + unstack, no `pivot_table`: con diez columnas de índice, el pivot
    # con `dropna=False` intenta materializar TODAS las combinaciones posibles
    # de sus valores y se come la memoria de la máquina.
    tabla = (
        df.groupby(claves + ["Día"], sort=False)["_ocupacion"]
        .sum()
        .unstack("Día")
        .reindex(columns=dias_presentes)
        .reset_index()
    )

    # Horario de cada día, para no perder a qué hora se visita.
    if "Horario" in df.columns:
        horarios = (
            df.groupby(claves + ["Día"], sort=False)["Horario"]
            .agg(lambda s: next((str(x) for x in s if str(x).strip()), ""))
            .unstack("Día")
            .reindex(columns=dias_presentes)
            .reset_index()
        )
    else:
        horarios = None

    tabla["Total semana (min)"] = tabla[dias_presentes].sum(axis=1, min_count=1).fillna(0)



    # Los ceros estorban: un día sin visita se deja en blanco.
    for dia in dias_presentes:
        tabla[dia] = tabla[dia].map(
            lambda v: None if pd.isna(v) or not float(v) else round(float(v), 2)
        )

    if horarios is not None:
        for dia in dias_presentes:
            tabla[f"{dia} · horario"] = horarios[dia].where(tabla[dia].notna(), None)

    orden_columnas = (
        claves
        + [c for dia in dias_presentes for c in (dia, f"{dia} · horario") if c in tabla.columns]
        + ["Total semana (min)"]
    )
    tabla = tabla[orden_columnas].sort_values(
        [c for c in ("Mercadista", "Fecha", "Descripción") if c in tabla.columns]
    )

    ws = wb.create_sheet(SHEET_RUTAS_SEMANA, 0)
    ws.append(list(tabla.columns))
    for fila in tabla.itertuples(index=False):
        ws.append([None if pd.isna(v) else v for v in fila])

    _formatear_rutas_semana(ws, dias_presentes)


def _formatear_rutas_semana(ws, dias) -> None:
    """Cabecera azul, panel fijo, filtros y anchos: la hoja se abre lista para leer."""
    cabecera_fondo = PatternFill(fill_type="solid", fgColor="1D4ED8")
    dia_fondo = PatternFill(fill_type="solid", fgColor="2563EB")
    blanco = Font(bold=True, color="FFFFFF", size=10)
    borde_suave = PatternFill(fill_type="solid", fgColor="EFF6FF")

    dias_set = {*dias, *(f"{d} · horario" for d in dias)}
    for celda in ws[1]:
        nombre = str(celda.value or "")
        celda.fill = dia_fondo if nombre in dias_set else cabecera_fondo
        celda.font = blanco
        celda.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    ws.row_dimensions[1].height = 30
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(ws.max_column)}{ws.max_row}"

    anchos = {
        "Descripción": 34, "Mercadista": 14, "Fecha": 11, "CANAL": 12, "CADENA": 14,
        "PROVINCIA": 16, "CIUDAD": 16, "CALLE": 26, "Latitud": 11, "Longitud": 11,
        "Total semana (min)": 14,
    }
    for indice, celda in enumerate(ws[1], start=1):
        nombre = str(celda.value or "")
        ancho = anchos.get(nombre, 15 if nombre in dias_set else 14)
        ws.column_dimensions[get_column_letter(indice)].width = ancho

    # Franja tenue en las columnas de día: separan la parrilla del resto.
    columnas_dia = [
        i for i, c in enumerate(ws[1], start=1) if str(c.value or "") in dias_set
    ]
    ultima_fila = ws.max_row
    for fila in range(2, ultima_fila + 1):
        for col in columnas_dia:
            celda = ws.cell(row=fila, column=col)
            celda.fill = borde_suave
            celda.alignment = Alignment(horizontal="center")

    _agregar_fila_total(ws, ultima_fila)


def _agregar_fila_total(ws, ultima_fila: int) -> None:
    """
    Fila TOTAL al final: minutos de cada día y total de la semana.

    Usa SUBTOTAL(109) y no SUMA: así el total responde al autofiltro. Al filtrar
    por Mercadista 04 el pie muestra el trabajo de esa persona, que es
    exactamente lo que se quiere mirar al revisar una ruta.
    """
    if ultima_fila < 2:
        return

    fila_total = ultima_fila + 1
    negrita = Font(bold=True, size=10)
    fondo = PatternFill(fill_type="solid", fgColor="DBEAFE")

    ws.cell(row=fila_total, column=1, value="TOTAL")

    for indice, cabecera in enumerate(ws[1], start=1):
        nombre = str(cabecera.value or "")
        # Se suman los minutos de cada día y los dos totales; los horarios no.
        if not (_es_columna_de_dia(ws, indice) or nombre == "Total semana (min)"):
            continue
        letra = get_column_letter(indice)
        ws.cell(
            row=fila_total,
            column=indice,
            value=f"=SUBTOTAL(109,{letra}2:{letra}{ultima_fila})",
        )

    for celda in ws[fila_total]:
        celda.font = negrita
        celda.fill = fondo
        if celda.column > 1:
            celda.alignment = Alignment(horizontal="center")


def _es_columna_de_dia(ws, indice: int) -> bool:
    """True si esa columna es uno de los días (no su horario)."""
    from route_engine.config import ORDEN_SEMANA

    return str(ws.cell(row=1, column=indice).value or "") in set(ORDEN_SEMANA)


def build_horarios_excel_download_bytes(abs_path: str) -> BytesIO:
    """
    Carga el .xlsx de rutas, aplica filtros + fila TOTAL en Horarios_Detalle
    y devuelve un buffer listo para enviar al cliente.
    Preserva el 'Tiempo Servicio (min)' original de cada visita.
    """
    wb = openpyxl.load_workbook(abs_path, data_only=False)
    if "Horarios_Detalle" in wb.sheetnames:
        try:
            format_horarios_detalle_worksheet(wb["Horarios_Detalle"])
        except Exception:
            pass
    # Vista semanal por punto (los días como columnas), como primera hoja.
    try:
        _construir_hoja_rutas_semana(wb, abs_path)
    except Exception as exc:
        print(f"      [!] No se pudo construir '{SHEET_RUTAS_SEMANA}': {exc}")

    # Hojas internas fuera de la copia que se descarga. Las usa el sistema
    # —`Config_Procesamiento` guarda con qué parámetros se generó el rutero y
    # `Categorias` alimenta las consultas—, pero a quien recibe el archivo no le
    # dicen nada. Se quitan solo del libro en memoria: el del sistema sigue
    # intacto, así que la app las sigue leyendo.
    for hoja_interna in HOJAS_SOLO_INTERNAS:
        if hoja_interna in wb.sheetnames:
            del wb[hoja_interna]

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio
