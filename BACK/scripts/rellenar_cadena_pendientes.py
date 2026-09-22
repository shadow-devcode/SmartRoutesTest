"""
Rellena la columna Cadena de Pendientes_Sin_Asignar en un Excel ya generado.

Los Excel procesados antes de guardar la cadena en esa hoja no permiten filtrar
los pendientes por grupo de cadenas. La cadena se busca, por orden: en las
visitas ya agendadas del mismo punto y en los Excel de entrada de uploads/,
cruzando primero por coordenadas y después por nombre.

Uso:  python scripts/rellenar_cadena_pendientes.py <excel> [--aplicar]
"""
from __future__ import annotations

import glob
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from services.pendientes_service import PENDIENTES_SHEET, leer_hoja_pendientes  # noqa: E402
from services.ruta_edit_service import _escribir_hojas  # noqa: E402
from utils.excel_cache import read_excel_cached  # noqa: E402
from utils.excel_lock import excel_file_lock  # noqa: E402


def _norm(v) -> str:
    return " ".join(str(v or "").split()).upper()


def _coord(lat, lon):
    try:
        return (round(float(lat), 6), round(float(lon), 6))
    except (TypeError, ValueError):
        return None


def _fuentes(hp: str) -> tuple[dict, dict]:
    """(cadena por coordenada, cadena por nombre) desde el propio Excel y uploads/."""
    por_coord: dict = {}
    por_nombre: dict = {}

    def anota(desc, lat, lon, cadena):
        texto = str(cadena or "").strip()
        if not texto or texto.lower() == "nan":
            return
        c = _coord(lat, lon)
        if c:
            por_coord.setdefault(c, texto)
        if _norm(desc):
            por_nombre.setdefault(_norm(desc), texto)

    hor = read_excel_cached(hp, "Horarios_Detalle")
    if "CADENA" in hor.columns:
        for d, la, lo, c in zip(hor["Descripción"], hor["Latitud"], hor["Longitud"], hor["CADENA"]):
            anota(d, la, lo, c)

    carpeta = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "uploads")
    for archivo in sorted(glob.glob(os.path.join(carpeta, "*.xlsx")), key=os.path.getmtime, reverse=True):
        try:
            df = pd.read_excel(archivo)
        except Exception:
            continue
        cols = {str(c).strip().upper(): c for c in df.columns}
        if "CADENA" not in cols:
            continue
        desc = cols.get("DESCRIPCION") or cols.get("DESCRIPCIÓN")
        lat, lon = cols.get("LATITUD"), cols.get("LONGITUD")
        if not desc:
            continue
        for i in df.index:
            anota(
                df.at[i, desc],
                df.at[i, lat] if lat else None,
                df.at[i, lon] if lon else None,
                df.at[i, cols["CADENA"]],
            )
    return por_coord, por_nombre


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    hp, aplicar = sys.argv[1], "--aplicar" in sys.argv

    with excel_file_lock(hp):
        pend = leer_hoja_pendientes(hp)
        if pend.empty:
            print("sin pendientes")
            return 0
        por_coord, por_nombre = _fuentes(hp)

        rellenadas = 0
        sin_cadena: list[str] = []
        for i in pend.index:
            actual = str(pend.at[i, "Cadena"] or "").strip() if "Cadena" in pend.columns else ""
            if actual and actual.lower() != "nan":
                continue
            c = _coord(pend.at[i, "Latitud"], pend.at[i, "Longitud"])
            cadena = por_coord.get(c) or por_nombre.get(_norm(pend.at[i, "Descripción"]))
            if cadena:
                pend.at[i, "Cadena"] = cadena
                rellenadas += 1
            else:
                sin_cadena.append(str(pend.at[i, "Descripción"]).strip())

        puntos = pend["Descripción"].astype(str).str.strip().nunique()
        con = pend[pend["Cadena"].astype(str).str.strip().str.lower().isin(["", "nan"]) == False]
        print(f"pendientes: {len(pend)} visitas / {puntos} puntos")
        print(f"  rellenadas ahora: {rellenadas} | con cadena tras el relleno: {len(con)}")
        if sin_cadena:
            print(f"  sin cadena: {len(sin_cadena)} -> {sorted(set(sin_cadena))[:6]}")
        if not rellenadas or not aplicar:
            print("(sin cambios; usa --aplicar para escribir)" if rellenadas else "(nada que rellenar)")
            return 0
        _escribir_hojas(hp, {PENDIENTES_SHEET: pend})
        print("escrito")
    return 0


if __name__ == "__main__":
    sys.exit(main())
