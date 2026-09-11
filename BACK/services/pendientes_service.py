"""
Hoja `Pendientes_Sin_Asignar`: visitas que el motor no pudo colocar.

La hoja se genera en `route_engine/excel_writer.py` al final del Excel de
salida. Aquí solo se lee/actualiza para que el frontend pueda asignarlas a
mano (ver `ruta_edit_service`).
"""
from __future__ import annotations

import os
from typing import Optional

import pandas as pd

from route_engine.excel_writer import drop_spurious_total_rows_horarios_df
from route_engine.geo import parse_coordenada_a_float
from route_engine.mapbox import provincia_display
from utils.excel_cache import read_excel_cached
from utils.route_helpers import clave_ub, coords_validas, recortar_lat, recortar_lon
from utils.uploads import UPLOAD_FOLDER

PENDIENTES_SHEET = "Pendientes_Sin_Asignar"
PENDIENTES_COLS: list[str] = [
    "Descripción", "Latitud", "Longitud", "Semana",
    "Tiempo Servicio (min)", "Provincia", "Ciudad", "Calle",
    "Frecuencia mes",
    "Mercadista origen", "Día origen", "Motivo",
]


def resolver_excel_maestro_frecuencia() -> str | None:
    """Excel de entrada con FRECUENCIA MES: preview pendiente o último upload.

    Importa lazy `get_pending_input_file` para evitar import circular:
    `excel_processing_service` depende de utils, y este módulo depende
    de excel_processing_service.
    """
    from services.excel_processing_service import get_pending_input_file

    pendiente = get_pending_input_file()
    if pendiente and os.path.isfile(pendiente):
        return pendiente
    upload_dir = os.path.abspath(UPLOAD_FOLDER)
    if not os.path.isdir(upload_dir):
        return None
    candidatos: list[tuple[float, str]] = []
    for fn in os.listdir(upload_dir):
        if fn.lower().endswith((".xlsx", ".xls")):
            p = os.path.join(upload_dir, fn)
            if os.path.isfile(p):
                candidatos.append((os.path.getmtime(p), p))
    if not candidatos:
        return None
    candidatos.sort(reverse=True)
    return candidatos[0][1]


def index_frecuencia_mes_desde_excel(path: str | None) -> dict:
    """Construye índice {(desc, lat6, lon6): frecuencia_mes} desde el Excel de entrada."""
    if not path or not os.path.isfile(path):
        return {}
    try:
        from route_engine.excel_reader import index_frecuencia_mes_por_punto, leer_excel_entrada

        return index_frecuencia_mes_por_punto(leer_excel_entrada(path))
    except Exception:
        return {}


def leer_hoja_pendientes(path: str) -> pd.DataFrame:
    """Lee la hoja 'Pendientes_Sin_Asignar'; si no existe, devuelve DF vacío con cols esperadas."""
    try:
        df = read_excel_cached(path, PENDIENTES_SHEET)
    except (ValueError, KeyError, FileNotFoundError):
        return pd.DataFrame(columns=PENDIENTES_COLS)
    if df is None or df.empty:
        return pd.DataFrame(columns=PENDIENTES_COLS)
    for c in PENDIENTES_COLS:
        if c not in df.columns:
            df[c] = ""
    return df[PENDIENTES_COLS].copy()


def _index_provincia_ciudad_por_coord(path: str) -> dict:
    """{(lat6, lon6): (provincia, ciudad)} desde Horarios_Detalle, para enriquecer pendientes antiguas."""
    try:
        df = read_excel_cached(path, "Horarios_Detalle")
    except Exception:
        return {}
    if df is None or df.empty:
        return {}
    idx: dict = {}
    cols = set(df.columns)
    if not {"Latitud", "Longitud"}.issubset(cols):
        return idx
    for _, row in df.iterrows():
        la = parse_coordenada_a_float(row.get("Latitud"))
        lo = parse_coordenada_a_float(row.get("Longitud"))
        if la is None or lo is None:
            continue
        key = (round(la, 6), round(lo, 6))
        if key in idx:
            continue
        prov = ""
        if "PROVINCIA" in cols:
            v = row.get("PROVINCIA")
            prov = "" if (v is None or pd.isna(v)) else str(v).strip()
        ciudad = ""
        if "CIUDAD" in cols:
            v = row.get("CIUDAD")
            ciudad = "" if (v is None or pd.isna(v)) else str(v).strip()
        idx[key] = (prov, ciudad)
    return idx


def _mapa_mercadista_por_punto(df_horarios) -> dict:
    """Por cada punto (desc + lat/lon), el mercadista que ya tiene ≥1 visita en Horarios_Detalle."""
    m: dict = {}
    if df_horarios is None or df_horarios.empty:
        return m
    for _, row in df_horarios.iterrows():
        k = clave_ub(row.get("Descripción"), row.get("Latitud"), row.get("Longitud"))
        merc = str(row.get("Mercadista", "") if pd.notna(row.get("Mercadista")) else "").strip()
        if merc and k not in m:
            m[k] = merc
    return m


def pendientes_to_json_list(
    df_pend: pd.DataFrame,
    *,
    enriquecer_desde: Optional[str] = None,
    freq_index: Optional[dict] = None,
) -> list[dict]:
    """
    Convierte el DataFrame de pendientes a la lista JSON que consume el frontend.

    Si `enriquecer_desde` es una ruta a un Excel, completa Provincia y Ciudad
    para las filas que las tengan vacías cruzando por (lat, lon) contra
    Horarios_Detalle. Esto permite mostrar ciudad en Excels antiguos.
    """
    out: list[dict] = []
    if df_pend is None or df_pend.empty:
        return out
    coord_index: dict = {}
    lock_por_punto: dict = {}
    if enriquecer_desde:
        coord_index = _index_provincia_ciudad_por_coord(enriquecer_desde)
        try:
            df_h = read_excel_cached(enriquecer_desde, "Horarios_Detalle")
            df_h = drop_spurious_total_rows_horarios_df(df_h)
            lock_por_punto = _mapa_mercadista_por_punto(df_h)
        except Exception:
            lock_por_punto = {}
    if freq_index is None:
        maestro = resolver_excel_maestro_frecuencia()
        freq_index = index_frecuencia_mes_desde_excel(maestro) if maestro else {}
    from route_engine.excel_reader import lookup_frecuencia_mes

    for _pos, (_, row) in enumerate(df_pend.iterrows()):
        desc = row.get("Descripción") or ""
        desc = "" if pd.isna(desc) else str(desc).strip()
        # recortar_lat/lon divide por 10 cuando el Excel perdió el punto
        # decimal (ej. -399008796474345 → -3.99). Si tras recortar la coord
        # sigue siendo inutilizable (null, NaN, 0,0), descartamos.
        lat = recortar_lat(row.get("Latitud"))
        lon = recortar_lon(row.get("Longitud"))
        if not coords_validas(lat, lon):
            continue
        semana = row.get("Semana")
        semana = "" if (semana is None or pd.isna(semana)) else str(semana).strip()
        t_serv = row.get("Tiempo Servicio (min)")
        try:
            t_serv = float(t_serv) if t_serv is not None and not pd.isna(t_serv) else 0.0
        except (TypeError, ValueError):
            t_serv = 0.0
        provincia = provincia_display(row.get("Provincia"))
        ciudad = "" if pd.isna(row.get("Ciudad")) else str(row.get("Ciudad") or "").strip()
        calle = "" if pd.isna(row.get("Calle")) else str(row.get("Calle") or "").strip()
        if (not provincia or not ciudad) and coord_index and lat is not None and lon is not None:
            extra_prov, extra_ciu = coord_index.get((round(lat, 6), round(lon, 6)), ("", ""))
            if not provincia:
                provincia = extra_prov
            if not ciudad:
                ciudad = extra_ciu
        mercadista_obligatorio = ""
        if enriquecer_desde and lat is not None and lon is not None:
            mercadista_obligatorio = lock_por_punto.get(clave_ub(desc, lat, lon), "") or ""
        frec_mes = lookup_frecuencia_mes(
            freq_index or {},
            desc,
            lat,
            lon,
            explicit=row.get("Frecuencia mes"),
        )
        item = {
            # Posición incluida para que dos visitas del MISMO punto+semana
            # (frecuencia ≥ 2) sean filas distinguibles en el frontend. La
            # asignación NO usa el id (cruza por desc/lat/lon/semana), así que
            # incluir la posición es seguro.
            "id": f"{desc}|{lat}|{lon}|{semana}|{_pos}",
            "descripcion": desc,
            "latitud": lat,
            "longitud": lon,
            "semana": semana,
            "tiempo_servicio": t_serv,
            "provincia": provincia,
            "ciudad": ciudad,
            "calle": calle,
            "mercadista_origen": (
                "" if pd.isna(row.get("Mercadista origen"))
                else str(row.get("Mercadista origen") or "").strip()
            ),
            "dia_origen": (
                "" if pd.isna(row.get("Día origen"))
                else str(row.get("Día origen") or "").strip()
            ),
            "motivo": (
                "" if pd.isna(row.get("Motivo"))
                else str(row.get("Motivo") or "").strip()
            ),
            "mercadista_obligatorio": mercadista_obligatorio,
        }
        if frec_mes is not None:
            item["frecuencia_mes"] = frec_mes
        out.append(item)
    return out


def listar_pendientes(path: str, *, semana: str, provincia: str, ciudad: str) -> dict:
    """
    Devuelve las visitas pendientes del archivo `path`, filtradas opcionalmente
    por semana/provincia/ciudad.
    """
    df_pend = leer_hoja_pendientes(path)
    pendientes = pendientes_to_json_list(df_pend, enriquecer_desde=path)
    if semana:
        pendientes = [p for p in pendientes if (p.get("semana") or "").strip().lower() == semana.lower()]
    if provincia:
        pendientes = [p for p in pendientes if (p.get("provincia") or "").strip().lower() == provincia.lower()]
    if ciudad:
        pendientes = [p for p in pendientes if (p.get("ciudad") or "").strip().lower() == ciudad.lower()]
    return {
        "success": True,
        "pendientes": pendientes,
        "total": len(pendientes),
        "semana_filtro": semana or None,
        "provincia_filtro": provincia or None,
        "ciudad_filtro": ciudad or None,
    }


def resumen_pendientes(path: str, *, provincia: str = "", ciudad: str = "") -> dict:
    """
    Pendientes AGRUPADOS POR PUNTO DE VENTA, para la pantalla de gestión.

    La lista plana (`listar_pendientes`) devuelve una fila por visita suelta, que
    es lo que necesita el asignador manual del mapa. Para gestionar hace falta lo
    contrario: ver el punto entero —cuántas visitas al mes le tocan, cuántas
    quedaron fuera y en qué días se le está visitando ya—, porque esa es la
    decisión que se toma (a quién y a qué día se le mete lo que falta).

    `dias_visita` sale de Horarios_Detalle: son los días en los que ese punto SÍ
    tiene visitas agendadas. Un punto con frecuencia 8 que aparece con
    "Lunes, Miércoles" y 3 pendientes está diciendo que le faltan 3 de sus 8
    visitas y que su patrón real es lunes y miércoles.
    """
    df_pend = leer_hoja_pendientes(path)
    pendientes = pendientes_to_json_list(df_pend, enriquecer_desde=path)

    if provincia:
        pendientes = [p for p in pendientes if (p.get("provincia") or "").strip().lower() == provincia.lower()]
    if ciudad:
        pendientes = [p for p in pendientes if (p.get("ciudad") or "").strip().lower() == ciudad.lower()]

    # Días y semanas en los que cada punto YA está agendado.
    dias_por_punto: dict = {}
    semanas_por_punto: dict = {}
    merc_por_punto: dict = {}
    agendadas_por_punto: dict = {}
    try:
        df_h = read_excel_cached(path, "Horarios_Detalle")
        df_h = drop_spurious_total_rows_horarios_df(df_h)
        for _, row in df_h.iterrows():
            clave = clave_ub(row.get("Descripción"), row.get("Latitud"), row.get("Longitud"))
            dia = str(row.get("Día") or "").strip()
            if dia:
                dias_por_punto.setdefault(clave, set()).add(dia)
            semana = str(row.get("Fecha") or "").strip()
            if semana:
                semanas_por_punto.setdefault(clave, set()).add(semana)
            merc = str(row.get("Mercadista") or "").strip()
            if merc:
                merc_por_punto.setdefault(clave, merc)
            agendadas_por_punto[clave] = agendadas_por_punto.get(clave, 0) + 1
    except Exception:
        pass

    from route_engine.config import ORDEN_SEMANA

    def _ordenar_dias(dias) -> list:
        conocidos = [d for d in ORDEN_SEMANA if d in dias]
        otros = sorted(d for d in dias if d not in ORDEN_SEMANA)
        return conocidos + otros

    puntos: dict = {}
    for p in pendientes:
        clave = clave_ub(p.get("descripcion"), p.get("latitud"), p.get("longitud"))
        item = puntos.get(clave)
        if item is None:
            item = {
                "id": clave,
                "descripcion": p.get("descripcion") or "",
                "latitud": p.get("latitud"),
                "longitud": p.get("longitud"),
                "provincia": p.get("provincia") or "",
                "ciudad": p.get("ciudad") or "",
                "tiempo_servicio": p.get("tiempo_servicio") or 0,
                "frecuencia_mes": p.get("frecuencia_mes"),
                "mercadista": p.get("mercadista_obligatorio") or merc_por_punto.get(clave) or "",
                "dias_visita": _ordenar_dias(dias_por_punto.get(clave, set())),
                "semanas_agendadas": sorted(semanas_por_punto.get(clave, set())),
                "visitas_agendadas": agendadas_por_punto.get(clave, 0),
                "visitas_pendientes": 0,
                "semanas_pendientes": [],
                "motivos": [],
            }
            puntos[clave] = item
        item["visitas_pendientes"] += 1
        semana = (p.get("semana") or "").strip()
        if semana and semana not in item["semanas_pendientes"]:
            item["semanas_pendientes"].append(semana)
        motivo = (p.get("motivo") or "").strip()
        if motivo and motivo not in item["motivos"]:
            item["motivos"].append(motivo)

    lista = sorted(
        puntos.values(),
        key=lambda x: (-x["visitas_pendientes"], x["descripcion"]),
    )
    for item in lista:
        item["semanas_pendientes"].sort()

    return {
        "success": True,
        "puntos": lista,
        "total_puntos": len(lista),
        "total_visitas_pendientes": sum(x["visitas_pendientes"] for x in lista),
        "minutos_pendientes": round(
            sum(float(x["tiempo_servicio"] or 0) * x["visitas_pendientes"] for x in lista), 1
        ),
    }
