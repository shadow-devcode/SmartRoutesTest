"""
Retira las filas de Pendientes_Sin_Asignar que sobran: las de puntos donde
agendadas + pendientes supera su frecuencia mensual.

Uso:  python scripts/recortar_pendientes_sobrantes.py datasets/<slug>/minoristas_horarios.xlsx [--aplicar]
Sin --aplicar solo informa.
"""
from __future__ import annotations

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
from utils.route_helpers import clave_ub  # noqa: E402


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    hp = sys.argv[1]
    aplicar = "--aplicar" in sys.argv

    with excel_file_lock(hp):
        h = read_excel_cached(hp, "Horarios_Detalle")
        h = h[h["Mercadista"].astype(str).str.strip().str.upper() != "TOTAL"]
        p = leer_hoja_pendientes(hp)

        agendadas: dict = {}
        for _, r in h.iterrows():
            c = clave_ub(r.get("Descripción"), r.get("Latitud"), r.get("Longitud"))
            agendadas[c] = agendadas.get(c, 0) + 1

        quitar: list = []
        pendientes_por_punto: dict = {}
        for i in p.index:
            r = p.loc[i]
            c = clave_ub(r.get("Descripción"), r.get("Latitud"), r.get("Longitud"))
            pendientes_por_punto.setdefault(c, []).append(i)
        for c, idxs in pendientes_por_punto.items():
            try:
                frecuencia = int(float(p.at[idxs[0], "Frecuencia mes"]))
            except (TypeError, ValueError):
                continue
            sobran = agendadas.get(c, 0) + len(idxs) - frecuencia
            if sobran > 0:
                # Las últimas filas del punto son las más recientes.
                quitar.extend(idxs[-sobran:])
                print(f"  {str(p.at[idxs[0], 'Descripción'])[:45]:45} sobran {sobran}")

        print(f"filas pendientes de más: {len(quitar)} de {len(p)}")
        if not quitar or not aplicar:
            print("(sin cambios; usa --aplicar para escribir)" if quitar else "(nada que quitar)")
            return 0
        p = p.drop(index=quitar).reset_index(drop=True)
        _escribir_hojas(hp, {PENDIENTES_SHEET: p})
        print(f"escrito: quedan {len(p)} pendientes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
