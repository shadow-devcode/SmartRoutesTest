"""
Lectura del modelo de jornada con el que se generó un Excel de rutas.

Vive aquí, y no en `route_engine`, porque necesita el caché de lectura de
Excel y lo consumen varios servicios (dashboard y edición de rutas). El motor
solo sabe escribir la hoja; interpretarla para las consultas es cosa de la
capa transversal.
"""
from __future__ import annotations

from route_engine.config import (
    CUOTA_DIA_POR_DEFECTO,
    DIAS_SEMANA_LABORALES,
    JORNADA_INCLUYE_VIAJE_POR_DEFECTO,
    SEMANAS_MES,
)
from route_engine.run_config import (
    CLAVE_MAX_DIA,
    CLAVE_MAX_MES,
    SHEET_CONFIG_PROCESAMIENTO,
    leer_entero,
    leer_incluye_viaje,
)
from utils.excel_cache import read_excel_cached


def _config_df(horarios_path: str | None):
    """Hoja de configuración del Excel, o None si no la tiene (formato antiguo)."""
    if not horarios_path:
        return None
    try:
        return read_excel_cached(horarios_path, SHEET_CONFIG_PROCESAMIENTO)
    except Exception:
        return None


def incluye_viaje_del_dataset(horarios_path: str | None) -> bool:
    """
    True si ESE Excel se generó contando el desplazamiento en la jornada.

    Se lee del archivo y no del servidor porque la opción se elige en cada
    procesamiento. Los Excels anteriores a esta hoja caen al valor por defecto
    del servidor, que es con el que se generaron.
    """
    return leer_incluye_viaje(
        _config_df(horarios_path), por_defecto=JORNADA_INCLUYE_VIAJE_POR_DEFECTO
    )


def cuota_dia_del_dataset(horarios_path: str | None) -> int:
    """Minutos de jornada diaria con los que se calculó ESE Excel (480 o 400)."""
    return leer_entero(
        _config_df(horarios_path), CLAVE_MAX_DIA, por_defecto=CUOTA_DIA_POR_DEFECTO
    )


def cuota_mes_del_dataset(horarios_path: str | None) -> int:
    """
    Cuota mensual de ESE Excel: el 100% contra el que se calculan los
    porcentajes de ocupación del dashboard.
    """
    df = _config_df(horarios_path)
    por_defecto = CUOTA_DIA_POR_DEFECTO * DIAS_SEMANA_LABORALES * SEMANAS_MES
    return leer_entero(df, CLAVE_MAX_MES, por_defecto=por_defecto)


def cuota_semana_del_dataset(horarios_path: str | None) -> int:
    """Cuota semanal de ESE Excel."""
    return cuota_mes_del_dataset(horarios_path) // SEMANAS_MES
