"""
Lógica de negocio para la hoja `Puntos_Sin_Coordenadas`.

Permite leer los puntos con coordenadas inválidas y moverlos a
`Pendientes_Sin_Asignar` tras asignarles lat/lon válidas y geocodificarlos.
"""
from __future__ import annotations

import pandas as pd

from route_engine.mapbox import obtener_direccion_desde_coordenadas
from services.pendientes_service import PENDIENTES_COLS, leer_hoja_pendientes
from services.ruta_edit_service import _agregar_pendiente_sin_duplicar, _sanitizar_df_para_excel
from utils.excel_atomic import escritura_atomica
from utils.excel_cache import invalidate_excel_cache, read_excel_cached
from utils.excel_lock import with_excel_file_lock

SIN_COORD_SHEET = "Puntos_Sin_Coordenadas"

SIN_COORD_COLS: list[str] = [
    "Descripción", "Latitud", "Longitud", "Semana",
    "Tiempo Servicio (min)", "Provincia", "Ciudad",
    "Frecuencia mes", "Mercadista origen", "Día origen", "Motivo",
]


def leer_hoja_sin_coordenadas(path: str) -> pd.DataFrame:
    """Lee la hoja Puntos_Sin_Coordenadas; si no existe, devuelve DF vacío."""
    try:
        df = read_excel_cached(path, SIN_COORD_SHEET)
    except (ValueError, KeyError, FileNotFoundError):
        return pd.DataFrame(columns=SIN_COORD_COLS)
    if df is None or df.empty:
        return pd.DataFrame(columns=SIN_COORD_COLS)
    df = df.where(pd.notna(df), None)
    for c in SIN_COORD_COLS:
        if c not in df.columns:
            df[c] = None
    return df[SIN_COORD_COLS].copy()


def sin_coord_to_json_list(df: pd.DataFrame) -> list[dict]:
    out: list[dict] = []
    for _, row in df.iterrows():
        def _s(col):
            v = row.get(col)
            return "" if (v is None or (isinstance(v, float) and pd.isna(v))) else str(v).strip()

        def _f(col):
            v = row.get(col)
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return None
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        out.append({
            "descripcion": _s("Descripción"),
            "latitud": _f("Latitud"),
            "longitud": _f("Longitud"),
            "semana": _s("Semana"),
            "tiempo_servicio": _f("Tiempo Servicio (min)"),
            "provincia": _s("Provincia"),
            "ciudad": _s("Ciudad"),
            "frecuencia_mes": _f("Frecuencia mes"),
            "mercadista_origen": _s("Mercadista origen"),
            "dia_origen": _s("Día origen"),
            "motivo": _s("Motivo"),
        })
    return out


@with_excel_file_lock("hp")
def actualizar_y_mover_a_pendientes(
    hp: str,
    descripcion: str,
    lat_nueva: float,
    lon_nueva: float,
) -> dict:
    """
    Geocodifica las nuevas coordenadas, mueve el punto de Puntos_Sin_Coordenadas
    a Pendientes_Sin_Asignar con Motivo='asignar mercaderista' y actualiza el Excel.
    """
    df_sin = leer_hoja_sin_coordenadas(hp)

    mascara = df_sin["Descripción"].astype(str).str.strip() == descripcion.strip()
    if not mascara.any():
        return {"success": False, "error": "Punto no encontrado"}

    idx_fila = df_sin.index[mascara][0]
    fila_orig = df_sin.loc[idx_fila]

    provincia, ciudad, calle = obtener_direccion_desde_coordenadas(lat_nueva, lon_nueva)

    def _orig(col):
        v = fila_orig.get(col)
        return None if (v is None or (isinstance(v, float) and pd.isna(v))) else v

    nueva_fila: dict = {col: None for col in PENDIENTES_COLS}
    nueva_fila["Descripción"] = descripcion.strip()
    nueva_fila["Latitud"] = lat_nueva
    nueva_fila["Longitud"] = lon_nueva
    nueva_fila["Semana"] = "ninguna"
    nueva_fila["Tiempo Servicio (min)"] = _orig("Tiempo Servicio (min)")
    nueva_fila["Provincia"] = provincia
    nueva_fila["Ciudad"] = ciudad
    nueva_fila["Calle"] = calle
    nueva_fila["Frecuencia mes"] = _orig("Frecuencia mes")
    nueva_fila["Mercadista origen"] = "ninguno"
    nueva_fila["Día origen"] = "ninguno"
    nueva_fila["Motivo"] = "asignar mercaderista"

    df_pend = leer_hoja_pendientes(hp)
    df_pend = _agregar_pendiente_sin_duplicar(df_pend, nueva_fila)

    df_sin = df_sin.drop(idx_fila).reset_index(drop=True)

    # Sobre una copia temporal que sustituye al original de golpe: quien
    # lea mientras tanto nunca verá el .xlsx a medio escribir.
    with escritura_atomica(hp) as _destino:
        with pd.ExcelWriter(_destino, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
            _sanitizar_df_para_excel(df_sin).to_excel(writer, sheet_name=SIN_COORD_SHEET, index=False)
            _sanitizar_df_para_excel(df_pend).to_excel(writer, sheet_name="Pendientes_Sin_Asignar", index=False)

    invalidate_excel_cache(hp)

    return {
        "success": True,
        "descripcion": descripcion.strip(),
        "latitud": lat_nueva,
        "longitud": lon_nueva,
        "provincia": provincia,
        "ciudad": ciudad,
        "calle": calle,
    }
