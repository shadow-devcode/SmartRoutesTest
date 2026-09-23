"""
Serializadores de filas de `Horarios_Detalle` a dicts JSON.

Antes vivían duplicados en `mercadistas_query_service` y `comparativa_service`
con el mismo cuerpo: misma extracción de columnas, mismos defaults, misma
defensa contra coords corruptas.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from route_engine.mapbox import provincia_display
from utils.route_helpers import (
    coords_validas,
    km_entre_sucursales_row,
    minutos_entre_sucursales_row,
    recortar_lat,
    recortar_lon,
)


def fila_a_ubicacion(row, *, incluir_semana: bool) -> Optional[dict]:
    """
    Convierte una fila de Horarios_Detalle al dict JSON usado en las respuestas
    de mapa (todas-ubicaciones / dia/<>).

    Devuelve None si la fila no tiene coordenadas válidas (rango lat/lng
    incluido — defensa contra celdas Excel corruptas).

    Si `incluir_semana=True`, agrega el campo `semana` extraído de la columna
    `Fecha`. Se omite en el endpoint `/api/comparativa/dia/<dia>` por
    compatibilidad con el contrato HTTP original.
    """
    # recortar_lat/lon divide por 10 cuando el Excel perdió el punto decimal,
    # recuperando la ubicación real (ej. -399008796474345 → -3.99 para Ecuador).
    lat = recortar_lat(row["Latitud"])
    lon = recortar_lon(row["Longitud"])
    if not coords_validas(lat, lon):
        return None

    fecha_val = row.get("Fecha")
    item: dict = {
        "mercadista": row["Mercadista"] if pd.notna(row["Mercadista"]) else "",
        "dia": row["Día"] if pd.notna(row["Día"]) else "",
        "orden": int(row["Orden Ruta"]) if pd.notna(row["Orden Ruta"]) else 0,
        "descripcion": row["Descripción"] if pd.notna(row["Descripción"]) else "",
        "latitud": lat,
        "longitud": lon,
        "provincia": provincia_display(row.get("PROVINCIA", "")),
        "ciudad": row.get("CIUDAD", "") if pd.notna(row.get("CIUDAD", "")) else "",
        "calle": row.get("CALLE", "") if pd.notna(row.get("CALLE", "")) else "",
        "tiempo_servicio": int(row["Tiempo Servicio (min)"]) if pd.notna(row["Tiempo Servicio (min)"]) else 0,
        "horario": row["Horario"] if pd.notna(row["Horario"]) else "",
        "km_entre_sucursales": km_entre_sucursales_row(row),
        # El desplazamiento de cada tramo: lo suman las tarjetas de resumen
        # para dar el tiempo de la ruta que se está mirando, no la del mes.
        "tiempo_entre_sucursal": minutos_entre_sucursales_row(row),
    }
    if incluir_semana:
        item["semana"] = (
            str(fecha_val).strip() if fecha_val is not None and pd.notna(fecha_val) else ""
        )
    return item


def stats_vacios() -> dict:
    """
    Plantilla de stats con todos los totales en cero.

    Se devuelve cuando el Excel no tiene datos o le faltan columnas requeridas.
    Igual para `/api/stats` y `/api/comparativa/stats`.
    """
    from route_engine.config import CUOTA_DIA_POR_DEFECTO

    return {
        "total_mercadistas": 0,
        "total_ubicaciones": 0,
        "total_puntos_asignados": 0,
        "total_puntos_pendientes": 0,
        "total_por_dia": {},
        "tiempo_promedio_servicio": 0,
        "total_tiempo_trabajo_min": 0,
        "total_tiempo_entre_sucursales_min": 0.0,
        "total_km_entre_sucursales": 0.0,
        "jornada": jornada_dto(CUOTA_DIA_POR_DEFECTO, incluye_desplazamiento=False),
    }


def jornada_dto(minutos_dia: int, *, incluye_desplazamiento: bool) -> dict:
    """
    Cuota de jornada del dataset, para que el front no tenga que hardcodearla.

    Los avisos de "por debajo del mínimo" y los porcentajes de ocupación se
    calculan contra estos números: con la jornada reducida (400 min) el mínimo
    ya no es el de 480 y la alerta saltaba con jornadas perfectamente llenas.
    """
    dia = int(minutos_dia or 0)
    return {
        "minutos_dia": dia,
        "minutos_semana": dia * 5,
        "minutos_mes": dia * 20,
        "incluye_desplazamiento": bool(incluye_desplazamiento),
    }
