"""
Quita de un Excel ya generado las pendientes fantasma que dejó la
reconciliación de frecuencia.

Hasta ahora la reconciliación cruzaba (punto, tiempo de servicio) sin redondear
el tiempo. Un tiempo calculado con fórmula en Excel se guarda como
294.0000000000001, mientras que las visitas agendadas llevaban 294.0: la pareja
no coincidía, el punto parecía no tener ninguna visita puesta y su frecuencia
mensual entera se volcaba a `Pendientes_Sin_Asignar`. El motor ya redondea en
los dos lados (ver `_info_puntos_desde_df`), pero los Excels generados antes de
ese arreglo siguen arrastrando esas filas.

Regla que se aplica, punto a punto:

    visitas agendadas + filas pendientes == FRECUENCIA MES

Lo que sobra se descarta empezando por las filas de reconciliación, que son las
sintéticas; las de «red de seguridad» describen un intento real de colocación y
se conservan mientras quepan.

Uso:
    python scripts/limpiar_pendientes_duplicadas.py <excel> [--aplicar]

Sin `--aplicar` solo informa. Con él, guarda una copia `.bak` antes de tocar
nada.
"""
from __future__ import annotations

import shutil
import sys

import pandas as pd

HOJA_HORARIOS = "Horarios_Detalle"
HOJA_PENDIENTES = "Pendientes_Sin_Asignar"


def _clave(desc, lat, lon):
    try:
        return (str(desc).strip(), round(float(lat), 6), round(float(lon), 6))
    except (TypeError, ValueError):
        return (str(desc).strip(), None, None)


def _prioridad_descarte(motivo: str) -> int:
    """Primero se tiran las filas de reconciliación."""
    return 0 if str(motivo).startswith("reconciliación") else 1


def limpiar(path: str, aplicar: bool) -> int:
    hojas = pd.read_excel(path, sheet_name=None)
    if HOJA_PENDIENTES not in hojas or HOJA_HORARIOS not in hojas:
        print(f"[!] {path} no tiene las hojas necesarias.")
        return 0

    horarios = hojas[HOJA_HORARIOS]
    horarios = horarios[horarios["Mercadista"].astype(str).str.strip().str.upper() != "TOTAL"]
    pendientes = hojas[HOJA_PENDIENTES]
    if pendientes.empty:
        print("Sin pendientes que revisar.")
        return 0

    agendadas = (
        pd.Series([_clave(d, la, lo) for d, la, lo in zip(
            horarios["Descripción"], horarios["Latitud"], horarios["Longitud"])])
        .value_counts()
        .to_dict()
    )

    claves = [_clave(d, la, lo) for d, la, lo in zip(
        pendientes["Descripción"], pendientes["Latitud"], pendientes["Longitud"])]
    pendientes = pendientes.assign(_clave=claves)

    sobrantes: list[int] = []
    for clave, grupo in pendientes.groupby("_clave", sort=False):
        frecuencia = grupo["Frecuencia mes"].max()
        if pd.isna(frecuencia) or int(frecuencia) <= 0:
            continue
        sobra = (agendadas.get(clave, 0) + len(grupo)) - int(frecuencia)
        if sobra <= 0:
            continue
        orden = sorted(
            grupo.index,
            key=lambda i: (_prioridad_descarte(grupo.loc[i, "Motivo"]), i),
        )
        sobrantes.extend(orden[:sobra])

    print(f"{path}")
    print(f"  pendientes actuales : {len(pendientes)}")
    print(f"  filas sobrantes     : {len(sobrantes)} en "
          f"{pendientes.loc[sobrantes, '_clave'].nunique() if sobrantes else 0} puntos")
    if not sobrantes or not aplicar:
        if sobrantes:
            print("  (simulación: vuelve a lanzarlo con --aplicar para escribirlo)")
        return len(sobrantes)

    shutil.copy2(path, path + ".bak")

    # Se borran las filas con openpyxl en vez de reescribir el libro con pandas:
    # el Excel lleva anchos, colores, autofiltros y fórmulas SUBTOTAL en otras
    # hojas que un `to_excel` completo dejaría en texto plano.
    from openpyxl import load_workbook

    from utils.excel_atomic import escritura_atomica

    libro = load_workbook(path)
    hoja = libro[HOJA_PENDIENTES]
    # +2: la fila 1 es la cabecera y openpyxl cuenta desde 1.
    for fila_excel in sorted((i + 2 for i in sobrantes), reverse=True):
        hoja.delete_rows(fila_excel)
    # Atómico: si la app está leyendo el archivo mientras esto corre, verá el
    # anterior completo hasta que el nuevo esté entero en disco.
    with escritura_atomica(path) as destino:
        libro.save(destino)
    print(f"  escrito. Copia previa en {path}.bak")
    return len(sobrantes)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        raise SystemExit(1)
    limpiar(sys.argv[1], "--aplicar" in sys.argv[2:])
