"""
Lógica del dashboard: frecuencia de puntos por mercadista y porcentaje de
tiempo por provincia.

Tope diario base usado para los porcentajes de la vista provincias:
- 4 semanas → 9600 min = 100%
- 1 semana   → 2400 min = 100%
"""
from __future__ import annotations

import pandas as pd

from route_engine.config import (
    ORDEN_SEMANA,
    RADIO_RESCATE_AMPLIADO_KM,
    carga_jornada,
    cuota_mes,
    cuota_semana,
    max_dia_flex,
    max_servicio_dia,
)
from utils.dataset_config import (
    cuota_mes_del_dataset,
    cuota_semana_del_dataset,
    incluye_viaje_del_dataset,
)
from utils.excel_cache import invalidate_excel_cache, read_excel_cached
from utils.excel_lock import excel_file_lock

# La capacidad de un mercadista (el 100% de ocupación) NO es una constante del
# servidor: depende del preset con el que se generó cada Excel —480 min/día ->
# 9.600/mes, o 400 -> 8.000—, así que se lee del propio archivo con
# `cuota_mes_del_dataset`. Antes eran literales (10000) que además solo se
# comparaban contra el tiempo de servicio, y un mercadista con la jornada llena
# se mostraba al 50-60%.

# Dispersión máxima admitida al unir dos mercadistas.
#
# Se mide contra el radio AMPLIADO, no contra el normal. El motor usa dos
# alcances distintos: RADIO_ZONA_KM para armar una ruta corriente, y
# RADIO_RESCATE_AMPLIADO_KM cuando lo que está en juego es recuperar una plaza
# casi vacía en una comarca aislada —que es exactamente el caso de uso de este
# botón—. Con el límite normal el botón bloqueaba uniones que el propio motor
# hace por su cuenta al compactar: era más estricto que el criterio que aplica
# el reparto automático, y no hay razón para que la decisión manual tenga menos
# margen que la automática.
#
# El tope sigue existiendo para lo que se puso: impedir que una unión a mano
# deshaga la zonificación y produzca rutas cruzando media sierra. Solo que el
# umbral es ahora el mismo que usa el motor para la misma decisión.
MAX_DISPERSION_UNION_KM = 2 * RADIO_RESCATE_AMPLIADO_KM


def _leer_horarios(hp: str | None) -> pd.DataFrame | None:
    """Lee Horarios_Detalle para endpoints de dashboard. Retorna DataFrame o None."""
    if not hp:
        return None
    try:
        return read_excel_cached(hp, "Horarios_Detalle")
    except Exception:
        return None



def frecuencia_puntos(hp: str | None, mercadista_filter: str) -> dict:
    """Mercadistas y puntos únicos que visitan con frecuencia."""
    df = _leer_horarios(hp)
    if df is None or df.empty:
        return {"success": True, "frecuencia_puntos": [], "mercadistas": []}
    if "Mercadista" not in df.columns or "Descripción" not in df.columns:
        return {"success": True, "frecuencia_puntos": [], "mercadistas": []}

    # Tiempo de servicio ORIGINAL: en Horarios_Detalle la columna "Tiempo
    # Servicio (min)" conserva el valor base por visita; solo se suma al
    # tiempo entre sucursal al armar el Excel de descarga.
    tiene_tiempo_serv = "Tiempo Servicio (min)" in df.columns
    if tiene_tiempo_serv:
        df["Tiempo Servicio (min)"] = pd.to_numeric(
            df["Tiempo Servicio (min)"], errors="coerce"
        ).fillna(0)

    agregaciones: dict[str, tuple[str, str]] = {"frecuencia": ("Descripción", "size")}
    if tiene_tiempo_serv:
        # MAX del grupo: si hubiera variaciones puntuales entre visitas del
        # mismo punto, mostramos la configuración dominante/mayor.
        agregaciones["tiempo_servicio_original"] = ("Tiempo Servicio (min)", "max")

    freq = (
        df.groupby(["Mercadista", "Descripción"], dropna=False)
        .agg(**agregaciones)
        .reset_index()
    )
    freq["Mercadista"] = freq["Mercadista"].fillna("").astype(str)
    freq["Descripción"] = freq["Descripción"].fillna("").astype(str)

    # Filtramos "TOTAL" / vacías que a veces vienen heredadas del Excel.
    mask_invalido = (
        freq["Mercadista"].str.strip().str.upper().isin(["", "TOTAL"])
        | freq["Descripción"].str.strip().str.upper().isin(["", "TOTAL"])
    )
    freq = freq[~mask_invalido]

    freq = freq.sort_values(["Mercadista", "frecuencia"], ascending=[True, False])

    if mercadista_filter:
        freq = freq[freq["Mercadista"].str.contains(mercadista_filter, case=False, na=False)]

    mercadistas = (
        sorted(
            [
                m
                for m in df["Mercadista"].dropna().astype(str).unique().tolist()
                if m.strip() and m.strip().upper() != "TOTAL"
            ]
        )
        if "Mercadista" in df.columns
        else []
    )

    lista: list[dict] = []
    for _, r in freq.iterrows():
        item: dict = {
            "mercadista": r["Mercadista"],
            "punto": r["Descripción"],
            "frecuencia": int(r["frecuencia"]),
        }
        if tiene_tiempo_serv:
            ts = r.get("tiempo_servicio_original", 0)
            try:
                ts_num = float(ts) if pd.notna(ts) else 0.0
            except (TypeError, ValueError):
                ts_num = 0.0
            item["tiempo_servicio_original"] = (
                int(ts_num) if ts_num == int(ts_num) else round(ts_num, 2)
            )
        lista.append(item)

    return {"success": True, "frecuencia_puntos": lista, "mercadistas": mercadistas}


def provincias_porcentaje(
    hp: str | None,
    mercadista_filter: str,
    provincia_filter: str,
) -> dict:
    """% del tiempo de servicio por provincia y mercadista, con detalle por semana."""
    df = _leer_horarios(hp)
    if df is None or df.empty:
        return {
            "success": True,
            "provincias_porcentaje": [],
            "mercadistas": [],
            "provincias": [],
        }

    if "Mercadista" not in df.columns or "PROVINCIA" not in df.columns:
        return {
            "success": True,
            "provincias_porcentaje": [],
            "mercadistas": [],
            "provincias": [],
        }

    col_serv = "Tiempo Servicio (min)"
    col_viaje = "Tiempo entre sucursal (min)"
    df[col_serv] = pd.to_numeric(df.get(col_serv, 0), errors="coerce").fillna(0)
    df[col_viaje] = pd.to_numeric(df.get(col_viaje, 0), errors="coerce").fillna(0)
    # Qué consume la jornada lo decide el modelo con el que se generó este
    # Excel: con el de la empresa ("sin tiempo de desplazamiento") la cuota es
    # de servicio y el desplazamiento se muestra aparte, sin descontar.
    df["_combinado"] = carga_jornada(
        df[col_serv], df[col_viaje], incluye_viaje=incluye_viaje_del_dataset(hp)
    )
    df["PROVINCIA"] = df["PROVINCIA"].fillna("").astype(str).str.strip()
    df = df[df["PROVINCIA"] != ""]

    prov = (
        df.groupby(["Mercadista", "PROVINCIA"])[[col_serv, "_combinado"]].sum().reset_index()
    )
    prov["minutos"] = prov["_combinado"].round(0).astype(int)
    prov["minutos_servicio"] = prov[col_serv].round(0).astype(int)
    max_mes = cuota_mes_del_dataset(hp)
    max_semana = cuota_semana_del_dataset(hp)
    prov["porcentaje"] = ((prov["minutos"] / max_mes) * 100).round(1) if max_mes else 0.0
    prov = prov.sort_values(["Mercadista", "minutos"], ascending=[True, False])

    if mercadista_filter:
        prov = prov[prov["Mercadista"].str.contains(mercadista_filter, case=False, na=False)]
    if provincia_filter:
        prov = prov[prov["PROVINCIA"].str.contains(provincia_filter, case=False, na=False)]

    mercadistas = sorted(df["Mercadista"].dropna().astype(str).unique().tolist())
    provincias = (
        sorted(df["PROVINCIA"].dropna().astype(str).str.strip().unique().tolist())
        if "PROVINCIA" in df.columns
        else []
    )

    semanas_validas = ["semana 1", "semana 2", "semana 3", "semana 4"]
    df["Fecha"] = df.get("Fecha", "").astype(str).str.strip()
    df["Día"] = df.get("Día", "").astype(str).str.strip()

    lista: list[dict] = []
    for _, r in prov.iterrows():
        merc = r["Mercadista"]
        prov_name = r["PROVINCIA"]
        subset = df[(df["Mercadista"] == merc) & (df["PROVINCIA"] == prov_name)]

        semanas_data: dict[str, dict] = {}
        for semana in semanas_validas:
            sem_df = subset[subset["Fecha"] == semana]
            total_puntos = int(len(sem_df))
            minutos_sem = float(sem_df["_combinado"].sum()) if not sem_df.empty else 0.0
            porcentaje_sem = (
                round((minutos_sem / max_semana) * 100, 1)
                if max_semana
                else 0.0
            )
            dias_counts = (
                sem_df.groupby("Día").size().to_dict()
                if not sem_df.empty and "Día" in sem_df.columns
                else {}
            )
            semanas_data[semana] = {
                "total_puntos": total_puntos,
                "minutos": int(round(minutos_sem)),
                "porcentaje": porcentaje_sem,
                "puntos_por_dia": {str(k): int(v) for k, v in dias_counts.items()},
            }

        lista.append(
            {
                "mercadista": merc,
                "provincia": prov_name,
                "porcentaje": float(r["porcentaje"]),
                "minutos": int(r["minutos"]),
                # Desglose: cuánto de la jornada es trabajo en tienda y cuánto
                # es desplazamiento. `minutos` ya es la suma de ambos.
                "minutos_servicio": int(r["minutos_servicio"]),
                "minutos_viaje": int(r["minutos"]) - int(r["minutos_servicio"]),
                "semana1_puntos": semanas_data.get("semana 1", {}).get("total_puntos", 0),
                "semana2_puntos": semanas_data.get("semana 2", {}).get("total_puntos", 0),
                "semana3_puntos": semanas_data.get("semana 3", {}).get("total_puntos", 0),
                "semana4_puntos": semanas_data.get("semana 4", {}).get("total_puntos", 0),
                "semana1_minutos": semanas_data.get("semana 1", {}).get("minutos", 0),
                "semana2_minutos": semanas_data.get("semana 2", {}).get("minutos", 0),
                "semana3_minutos": semanas_data.get("semana 3", {}).get("minutos", 0),
                "semana4_minutos": semanas_data.get("semana 4", {}).get("minutos", 0),
                "semana1_porcentaje": semanas_data.get("semana 1", {}).get("porcentaje", 0.0),
                "semana2_porcentaje": semanas_data.get("semana 2", {}).get("porcentaje", 0.0),
                "semana3_porcentaje": semanas_data.get("semana 3", {}).get("porcentaje", 0.0),
                "semana4_porcentaje": semanas_data.get("semana 4", {}).get("porcentaje", 0.0),
                "dias_por_semana": semanas_data,
            }
        )

    return {
        "success": True,
        "provincias_porcentaje": lista,
        "mercadistas": mercadistas,
        "provincias": provincias,
    }


def _find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Busca una columna en df que coincida con alguno de los candidatos (sin importar tildes ni mayúsculas)."""
    import unicodedata

    def normalize_str(s: str) -> str:
        s_norm = unicodedata.normalize("NFD", str(s))
        s_clean = "".join(c for c in s_norm if unicodedata.category(c) != "Mn")
        return s_clean.lower().strip()

    normalized_cols = {normalize_str(col): col for col in df.columns}
    for cand in candidates:
        cand_norm = normalize_str(cand)
        if cand_norm in normalized_cols:
            return normalized_cols[cand_norm]
    return None


def _format_duration_string(serv_min: float) -> str:
    """Convierte minutos de servicio (ej. 240) a '4:00 horas'."""
    h = int(serv_min // 60)
    m = int(serv_min % 60)
    return f"{h}:{m:02d} horas"


def _optimize_day_route_order(indices, df: pd.DataFrame, col_lat: str | None, col_lon: str | None) -> list:
    """Ordena los índices de visitas de un día usando Nearest Neighbor para minimizar traslados por carretera."""
    from route_engine.mapbox import calcular_tiempo_entre

    idx_list = list(indices)
    if len(idx_list) <= 1:
        return idx_list

    unvisited = list(idx_list)
    ordered = [unvisited.pop(0)]

    while unvisited:
        last_idx = ordered[-1]
        last_lat = float(df.loc[last_idx, col_lat]) if col_lat and pd.notna(df.loc[last_idx, col_lat]) else None
        last_lon = float(df.loc[last_idx, col_lon]) if col_lon and pd.notna(df.loc[last_idx, col_lon]) else None

        best_cand = unvisited[0]
        best_dist = float('inf')

        for cand in unvisited:
            c_lat = float(df.loc[cand, col_lat]) if col_lat and pd.notna(df.loc[cand, col_lat]) else None
            c_lon = float(df.loc[cand, col_lon]) if col_lon and pd.notna(df.loc[cand, col_lon]) else None

            if last_lat is not None and last_lon is not None and c_lat is not None and c_lon is not None:
                t_min, _ = calcular_tiempo_entre(last_lat, last_lon, c_lat, c_lon)
            else:
                t_min = 0.0

            if t_min < best_dist:
                best_dist = t_min
                best_cand = cand

        ordered.append(best_cand)
        unvisited.remove(best_cand)

    return ordered


def _coords_unicas(df: pd.DataFrame, indices, col_lat: str | None, col_lon: str | None) -> list:
    """Coordenadas (lat, lon) distintas de las filas indicadas, redondeadas a 6 decimales.

    Se deduplica porque lo que interesa es la geografía de los PUNTOS de venta,
    no el número de visitas: un punto con 8 visitas al mes no debe pesar 8 veces
    al medir la dispersión, y deduplicar baja el coste del cálculo de diámetro
    de O(visitas²) a O(puntos²).
    """
    if not col_lat or not col_lon:
        return []
    vistos = set()
    for idx in indices:
        la = df.loc[idx, col_lat]
        lo = df.loc[idx, col_lon]
        if pd.isna(la) or pd.isna(lo):
            continue
        try:
            vistos.add((round(float(la), 6), round(float(lo), 6)))
        except (TypeError, ValueError):
            continue
    return list(vistos)


def _diametro_km(coords: list) -> float:
    """Distancia en línea recta entre los dos puntos más alejados del conjunto.

    Se usa el diámetro y no la distancia entre centroides porque el centroide
    esconde justo el caso que queremos evitar: dos racimos de puntos separados
    150 km tienen su centroide en medio y parecen "una sola zona compacta".
    """
    from route_engine.geo import haversine_km

    if len(coords) < 2:
        return 0.0
    peor = 0.0
    for i in range(len(coords) - 1):
        la1, lo1 = coords[i]
        for j in range(i + 1, len(coords)):
            la2, lo2 = coords[j]
            d = haversine_km(la1, lo1, la2, lo2)
            if d > peor:
                peor = d
    return peor


def _carga_combinada(df: pd.DataFrame, indices, col_serv: str | None, col_travel: str | None) -> tuple:
    """(servicio, desplazamiento) en minutos de las filas indicadas."""
    if not len(indices):
        return 0.0, 0.0
    sub = df.loc[indices]
    serv = (
        float(pd.to_numeric(sub[col_serv], errors="coerce").fillna(0).sum())
        if col_serv and col_serv in sub.columns
        else 0.0
    )
    viaje = (
        float(pd.to_numeric(sub[col_travel], errors="coerce").fillna(0).sum())
        if col_travel and col_travel in sub.columns
        else 0.0
    )
    return serv, viaje


def _validar_union(df: pd.DataFrame, merc_origen: str, merc_destino: str) -> dict | None:
    """
    Comprueba que unir dos mercadistas no rompa las reglas de rutas inteligentes.

    Devuelve None si la unión es válida, o un dict con el detalle del
    incumplimiento (para que el controller responda 409 y el frontend pueda
    ofrecer forzar) si no lo es. Se comprueban dos cosas:

    - Dispersión geográfica: el diámetro de la unión de los dos conjuntos de
      puntos no puede pasar de MAX_DISPERSION_UNION_KM.
    - Carga mensual: servicio + desplazamiento no puede pasar de
      cuota_mes(). El servicio es exacto (son las mismas visitas
      antes y después de unir); el desplazamiento es el actual de ambas rutas y
      solo aproxima el de la ruta unida, que se recalcula al re-enrutar.
    """
    col_merc = _find_column(df, ["Mercadista", "Mercaderista", "MERCADISTA"])
    if not col_merc:
        return None

    col_lat = _find_column(df, ["Latitud", "LATITUD"])
    col_lon = _find_column(df, ["Longitud", "LONGITUD"])
    col_serv = _find_column(df, ["Tiempo Servicio (min)", "Tiempo Servicio", "TiempoServicio"])
    col_travel = _find_column(
        df, ["Tiempo entre sucursal (min)", "Tiempo entre sucursales (min)", "Tiempo entre sucursal"]
    )

    mercs = df[col_merc].astype(str).str.strip()
    idx_origen = df.index[mercs == merc_origen]
    idx_destino = df.index[mercs == merc_destino]

    coords = _coords_unicas(df, list(idx_origen) + list(idx_destino), col_lat, col_lon)
    dispersion = _diametro_km(coords)

    serv_o, viaje_o = _carga_combinada(df, idx_origen, col_serv, col_travel)
    serv_d, viaje_d = _carga_combinada(df, idx_destino, col_serv, col_travel)
    servicio_total = serv_o + serv_d
    viaje_total = viaje_o + viaje_d
    combinado = servicio_total + viaje_total

    motivos: list[str] = []
    if dispersion > MAX_DISPERSION_UNION_KM:
        motivos.append(
            f"los puntos de ambos quedarían repartidos en {dispersion:.0f} km "
            f"(máximo {MAX_DISPERSION_UNION_KM:.0f} km)"
        )
    if combinado > cuota_mes():
        motivos.append(
            f"la carga mensual sería de {combinado:.0f} min combinados "
            f"(servicio {servicio_total:.0f} + viaje {viaje_total:.0f}), "
            f"por encima de los {cuota_mes()} min de un mercadista"
        )

    if not motivos:
        return None

    return {
        "success": False,
        "error": (
            f"No se puede unir {merc_origen} en {merc_destino}: "
            + "; ".join(motivos)
            + ". Reenvía con 'forzar': true para unirlos de todas formas."
        ),
        "union_invalida": True,
        "puede_forzar": True,
        "dispersion_km": round(dispersion, 1),
        "limite_dispersion_km": MAX_DISPERSION_UNION_KM,
        "servicio_total_min": round(servicio_total, 1),
        "travel_total_min": round(viaje_total, 1),
        "combinado_total_min": round(combinado, 1),
        "limite_mes_min": cuota_mes(),
        "exceso_min": round(max(0.0, combinado - cuota_mes()), 1),
    }


def _num_semana(valor) -> int | None:
    """Extrae el número de 'semana 3' → 3. None si no se reconoce."""
    import re

    m = re.search(r"(\d+)", str(valor or ""))
    return int(m.group(1)) if m else None


def _rebalance_mercadista_routes(df: pd.DataFrame, merc_destino: str) -> tuple:
    """
    Reorganiza y re-enruta las visitas asignadas a `merc_destino` cumpliendo las 3 reglas clave:
    1. Un mismo punto de venta NUNCA se visita más de una vez en el mismo día.
    2. La suma combinada (Tiempo Servicio + Tiempo entre sucursal) NUNCA supera
       max_dia_flex() por día.
    3. El horario de trabajo SIEMPRE inicia a las 08:00 AM y finaliza MÁXIMO a las 17:00 PM (jornada laboral fija de 08:00 a 17:00).

    Devuelve `(df, indices_a_pendientes)`. Las visitas que no caben en ningún
    día sin romper 2 o 3 NO se meten a la fuerza: se devuelven para que el
    llamador las pase a 'Pendientes_Sin_Asignar'. Siguen perteneciendo a
    `merc_destino` (regla de negocio: un punto lo visita siempre el mismo
    mercadista), solo quedan sin agendar.
    """
    from route_engine.mapbox import calcular_tiempo_entre

    # Los siete días: con cuadrilla de fin de semana hay rutas en sábado y
    # domingo, y dejarlas fuera del orden las excluía del rebalanceo.
    DAYS_ORDER = list(ORDEN_SEMANA)
    # Tope tomado del motor, no escrito a mano: si cambia la jornada, este
    # rebalanceo cambia con ella. Antes convivían dos literales (480 y 500) que
    # además contradecían al docstring, y el de 500 no se usaba en ningún sitio.
    MAX_DAY_COMBINED = float(max_dia_flex())

    col_merc = _find_column(df, ["Mercadista", "Mercaderista", "MERCADISTA"])
    if not col_merc:
        return df, []

    merc_destino_clean = str(merc_destino).strip()
    merc_mask = df[col_merc].astype(str).str.strip() == merc_destino_clean
    sub_indices = df[merc_mask].index

    if len(sub_indices) == 0:
        return df, []

    # Visitas que no caben en ninguna jornada del destino sin romper el tope
    # combinado o el fin de jornada. Se acumulan aquí y el llamador las manda a
    # pendientes en vez de forzarlas dentro de un día que ya está lleno.
    a_pendientes: list = []

    col_desc = _find_column(df, ["Descripción", "DESCRIPCION", "Descripcion", "Punto", "punto_id"])
    col_fecha = _find_column(df, ["Fecha", "Semana", "FECHA"])
    col_dia = _find_column(df, ["Día", "Dia", "DIA"])
    col_serv = _find_column(df, ["Tiempo Servicio (min)", "Tiempo Servicio", "TiempoServicio"])
    col_lat = _find_column(df, ["Latitud", "LATITUD"])
    col_lon = _find_column(df, ["Longitud", "LONGITUD"])
    col_horario = _find_column(df, ["Horario", "HORARIO"])
    col_duracion = _find_column(df, ["Duración (hh:mm)", "Duracion (hh:mm)", "Duración", "Duracion"])
    col_tiempo_suc = _find_column(df, ["Tiempo entre sucursal (min)", "Tiempo entre sucursales (min)", "Tiempo entre sucursal"])
    col_km_suc = _find_column(df, ["kilometros entre sucurlas (km)", "kilometros entre sucursales (km)", "kilometros entre sucursal (km)", "km entre sucursal"])
    col_orden = _find_column(df, ["Orden Ruta", "Orden", "ORDEN"])

    # Convertir columnas numéricas a float para evitar TypeError de pandas
    if col_km_suc:
        df[col_km_suc] = df[col_km_suc].astype(float)
    if col_tiempo_suc:
        df[col_tiempo_suc] = df[col_tiempo_suc].astype(float)
    if col_orden:
        df[col_orden] = df[col_orden].astype(float)

    # 1. Redistribuir días por semana ÚNICAMENTE para el mercaderista consolidado
    if col_fecha and col_dia and col_desc:
        for semana, sem_indices in df.loc[sub_indices].groupby(col_fecha).groups.items():
            sem_sub_indices = [idx for idx in sem_indices if idx in sub_indices]
            if not sem_sub_indices:
                continue

            day_combined_load = {d: 0.0 for d in DAYS_ORDER}
            day_last_coords = {d: None for d in DAYS_ORDER}
            store_assigned_days: dict[str, set[str]] = {}

            ideal_day_patterns = {
                1: ["Lunes"],
                2: ["Lunes", "Miércoles"],
                3: ["Lunes", "Miércoles", "Viernes"],
                4: ["Lunes", "Martes", "Jueves", "Viernes"],
                5: ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
            }

            # Agrupar por tienda en esta semana para este mercaderista
            stores = df.loc[sem_sub_indices].groupby(col_desc)
            for store_name, store_group in stores:
                k = len(store_group)
                pattern = ideal_day_patterns.get(k, DAYS_ORDER)
                store_assigned_days[store_name] = set()

                for i, idx in enumerate(store_group.index):
                    serv_min = float(df.loc[idx, col_serv]) if col_serv and pd.notna(df.loc[idx, col_serv]) else 60.0
                    cur_lat = float(df.loc[idx, col_lat]) if col_lat and pd.notna(df.loc[idx, col_lat]) else None
                    cur_lon = float(df.loc[idx, col_lon]) if col_lon and pd.notna(df.loc[idx, col_lon]) else None

                    chosen_day = None

                    # Opción A: Intentar patrón ideal si la tienda no se ha visitado en ese día y carga combinada <= 480 min
                    if i < len(pattern):
                        cand = pattern[i]
                        if cand not in store_assigned_days[store_name]:
                            last_c = day_last_coords[cand]
                            t_est = 0.0 if (last_c is None or cur_lat is None or cur_lon is None) else calcular_tiempo_entre(last_c[0], last_c[1], cur_lat, cur_lon)[0]
                            if day_combined_load[cand] + serv_min + t_est <= MAX_DAY_COMBINED:
                                chosen_day = cand

                    # Opción B: Buscar cualquier día NO VISITADO aún por la tienda con carga combinada <= 480 min
                    if not chosen_day:
                        valid_days = []
                        for d in DAYS_ORDER:
                            if d in store_assigned_days[store_name]:
                                continue  # REGLA 1: ESTRICTAMENTE PROHIBIDO VISITAR MISMA TIENDA EL MISMO DÍA
                            last_c = day_last_coords[d]
                            t_est = 0.0 if (last_c is None or cur_lat is None or cur_lon is None) else calcular_tiempo_entre(last_c[0], last_c[1], cur_lat, cur_lon)[0]
                            if day_combined_load[d] + serv_min + t_est <= MAX_DAY_COMBINED:
                                valid_days.append((d, day_combined_load[d] + serv_min + t_est))

                        if valid_days:
                            chosen_day = min(valid_days, key=lambda x: x[1])[0]

                    if not chosen_day:
                        # Aquí había una "Opción C" que metía la visita en el día
                        # menos cargado AUNQUE se pasara de los 480 min: si la
                        # semana ya estaba llena, cada visita extra se apilaba en
                        # jornadas de 600, 700 min. El botón deshacía así la regla
                        # que el motor acababa de aplicar, y el exceso no aparecía
                        # en ningún sitio porque la fila seguía "agendada".
                        # Ahora la visita queda pendiente y visible.
                        a_pendientes.append(idx)
                        continue

                    store_assigned_days[store_name].add(chosen_day)
                    df.loc[idx, col_dia] = chosen_day

                    last_c = day_last_coords[chosen_day]
                    t_est = 0.0 if (last_c is None or cur_lat is None or cur_lon is None) else calcular_tiempo_entre(last_c[0], last_c[1], cur_lat, cur_lon)[0]
                    day_combined_load[chosen_day] += serv_min + t_est
                    if cur_lat is not None and cur_lon is not None:
                        day_last_coords[chosen_day] = (cur_lat, cur_lon)

    # 2. Recalcular horarios, orden de ruta (Nearest Neighbor), tiempo entre
    #    sucursales y kilometraje por día, delegando en el MISMO VisitaConfirmador
    #    que usa el motor al generar el Excel.
    #
    #    Antes este bloque reimplementaba el cálculo de horarios a mano: sumaba
    #    servicio y viaje desde las 08:00 sin descontar la hora de almuerzo y sin
    #    comprobar el fin de jornada, así que una ruta unida podía terminar
    #    escribiendo "16:40 - 18:10" y no cuadraba con lo que el motor habría
    #    producido para esa misma ruta. Reutilizarlo garantiza que una ruta unida
    #    a mano y una generada por el motor se rijan por las mismas reglas.
    if col_fecha and col_dia:
        from route_engine.scheduling import VisitaConfirmador

        pend_set = set(a_pendientes)
        sub_activos = [i for i in sub_indices if i not in pend_set]

        for (semana, dia), grupo in df.loc[sub_activos].groupby([col_fecha, col_dia]):
            # Ordenar las visitas del día de forma geográfica óptima
            opt_indices = _optimize_day_route_order(grupo.index, df, col_lat, col_lon)

            servicios = {}
            for idx_f in opt_indices:
                servicios[idx_f] = (
                    float(df.loc[idx_f, col_serv])
                    if col_serv and pd.notna(df.loc[idx_f, col_serv])
                    else 60.0
                )
            # Misma condición que el motor: una jornada de exactamente 480 min de
            # servicio puro no cabe si además se para a almorzar.
            aplicar_almuerzo = sum(servicios.values()) != max_servicio_dia()

            confirmador = VisitaConfirmador(merc_destino_clean, dia, None, aplicar_almuerzo)
            semana_num = _num_semana(semana) or 1
            n_ord = 0

            for idx_f in opt_indices:
                cur_lat = float(df.loc[idx_f, col_lat]) if col_lat and pd.notna(df.loc[idx_f, col_lat]) else None
                cur_lon = float(df.loc[idx_f, col_lon]) if col_lon and pd.notna(df.loc[idx_f, col_lon]) else None
                # Sin coordenadas no se puede medir el traslado: se coloca en la
                # posición anterior para que el tramo cueste 0 en vez de romper.
                if cur_lat is None or cur_lon is None:
                    cur_lat = confirmador.prev_lat if confirmador.prev_lat is not None else 0.0
                    cur_lon = confirmador.prev_lon if confirmador.prev_lon is not None else 0.0

                visita = {
                    "lat": cur_lat,
                    "lon": cur_lon,
                    "tiempo": servicios[idx_f],
                    "descripcion": str(df.loc[idx_f, col_desc]) if col_desc else "",
                    "semana_num": semana_num,
                    "frecuencia_mes": 1,
                }

                antes = len(confirmador.day_summaries)
                # Los límites de distancia entre paradas los decide el motor al
                # armar la ruta; aquí las visitas ya son de este mercadista y no
                # se pueden rechazar por lejanía (eso lo cubre _validar_union).
                ok = confirmador.intentar_confirmar(visita, travel_limit=None, skip_distance_check=True)
                nuevos = confirmador.day_summaries[antes:]
                if not ok or not nuevos:
                    a_pendientes.append(idx_f)
                    continue

                resumen = nuevos[0]
                n_ord += 1
                if col_orden:
                    df.loc[idx_f, col_orden] = float(n_ord)
                if col_horario:
                    df.loc[idx_f, col_horario] = resumen["Horario"]
                if col_duracion:
                    df.loc[idx_f, col_duracion] = _format_duration_string(servicios[idx_f])
                if col_tiempo_suc:
                    df.loc[idx_f, col_tiempo_suc] = float(resumen["Tiempo entre sucursal (min)"])
                if col_km_suc:
                    df.loc[idx_f, col_km_suc] = float(resumen["kilometros entre sucurlas (km)"])

    return df, a_pendientes


def unir_mercadistas(
    hp: str | None,
    mercadista_origen: str,
    mercadista_destino: str,
    forzar: bool = False,
) -> dict:
    """
    Combina todas las visitas de `mercadista_origen` en `mercadista_destino`,
    redistribuye los días de visita para evitar duplicados en la misma jornada (medio tiempo/tiendas repetidas)
    y actualiza el archivo de Excel activo.

    Antes de tocar nada se comprueba que la unión respete las reglas de rutas
    inteligentes (dispersión geográfica y carga mensual). Si no las respeta se
    rechaza con el detalle del incumplimiento; `forzar=True` la aplica igual,
    para los casos en que el usuario sabe algo que el Excel no dice.

    El ciclo completo leer→modificar→escribir va dentro de `excel_file_lock`:
    bloquear solo la escritura no evita el lost update, porque el problema es la
    lectura obsoleta. Sin el lock, unir mercadistas mientras otro editor
    reordena una ruta hacía desaparecer uno de los dos cambios sin ningún error.
    """
    import os
    if not hp or not os.path.exists(hp):
        return {"success": False, "error": "No existe un archivo de rutas activo."}

    merc_origen_clean = mercadista_origen.strip()
    merc_destino_clean = mercadista_destino.strip()

    if merc_origen_clean.lower() == merc_destino_clean.lower():
        return {"success": False, "error": "El mercaderista de origen y destino no pueden ser el mismo."}

    # 'TOTAL' es la fila de totales que arrastra la hoja, no una persona. Sin
    # este filtro aparece en el desplegable como un mercadista más y unirla
    # "funciona" sin hacer nada.
    if "TOTAL" in (merc_origen_clean.upper(), merc_destino_clean.upper()):
        return {"success": False, "error": "'TOTAL' es una fila de totales del Excel, no un mercaderista."}

    from services.pendientes_service import (
        index_frecuencia_mes_desde_excel,
        leer_hoja_pendientes,
        resolver_excel_maestro_frecuencia,
    )
    # Helpers de ruta_edit_service: mismo esquema de pendientes y misma
    # sanitización anti-fórmulas al escribir. Duplicarlos aquí abriría la puerta
    # a que las dos rutas de escritura del Excel se desincronicen.
    from services.ruta_edit_service import (
        _agregar_pendiente,
        _fila_horarios_a_pendiente,
        _guardar_horarios_pendientes,
    )

    with excel_file_lock(hp):
        try:
            xls = pd.ExcelFile(hp)
            sheet_names = xls.sheet_names
            sheet_name = "Horarios_Detalle" if "Horarios_Detalle" in sheet_names else sheet_names[0]
            df = pd.read_excel(hp, sheet_name=sheet_name)
        except Exception as e:
            return {"success": False, "error": f"Error al leer el archivo de rutas: {str(e)}"}

        if df is None or df.empty or "Mercadista" not in df.columns:
            return {"success": False, "error": "El archivo de rutas no contiene la columna 'Mercadista'."}

        filas_origen = df[df["Mercadista"].astype(str).str.strip() == merc_origen_clean]
        if filas_origen.empty:
            return {"success": False, "error": f"No se encontraron visitas registradas para {merc_origen_clean}."}

        if not forzar:
            violacion = _validar_union(df, merc_origen_clean, merc_destino_clean)
            if violacion:
                return violacion

        # Reasignar Mercadista de origen a destino
        mask = df["Mercadista"].astype(str).str.strip() == merc_origen_clean
        df.loc[mask, "Mercadista"] = merc_destino_clean

        # Rebalancear días, horarios, secuencias y kilometraje para mercadista_destino
        df, idx_pendientes = _rebalance_mercadista_routes(df, merc_destino_clean)

        # Las visitas que no cupieron pasan a 'Pendientes_Sin_Asignar'. Siguen
        # ligadas a merc_destino ("Mercadista origen"), así que el frontend solo
        # permitirá reasignarlas a él: un punto de venta lo visita siempre el
        # mismo mercadista.
        n_pendientes = 0
        if idx_pendientes:
            df_pend = leer_hoja_pendientes(hp)
            freq_index = index_frecuencia_mes_desde_excel(resolver_excel_maestro_frecuencia())
            motivo = f"no cabe en la jornada tras unir {merc_origen_clean} en {merc_destino_clean}"
            for idx in idx_pendientes:
                df_pend = _agregar_pendiente(
                    df_pend, _fila_horarios_a_pendiente(df.loc[idx], motivo, freq_index)
                )
            df = df.drop(index=idx_pendientes).reset_index(drop=True)
            n_pendientes = len(idx_pendientes)
            try:
                _guardar_horarios_pendientes(hp, df, df_pend)
            except Exception as ex:
                return {"success": False, "error": f"Error al guardar los cambios en el Excel: {str(ex)}"}
        else:
            try:
                if "Horarios_Detalle" in sheet_names:
                    with pd.ExcelWriter(hp, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
                        df.to_excel(writer, sheet_name="Horarios_Detalle", index=False)
                else:
                    df.to_excel(hp, index=False, engine="openpyxl")
            except Exception:
                try:
                    df.to_excel(hp, index=False, engine="openpyxl")
                except Exception as ex:
                    return {"success": False, "error": f"Error al guardar los cambios en el Excel: {str(ex)}"}

        # Invalidar el caché de lectura para que la siguiente consulta traiga
        # datos frescos. Antes se importaba `clear_excel_cache`, que no existe:
        # el ImportError lo tragaba un `except Exception: pass`, así que el
        # bloque entero era código muerto y nadie se enteraba.
        invalidate_excel_cache(hp)

    mensaje = f"Se unieron exitosamente las rutas de {merc_origen_clean} en {merc_destino_clean}."
    resultado: dict = {"success": True, "message": mensaje, "pendientes_generadas": n_pendientes}
    if n_pendientes:
        resultado["advertencia"] = (
            f"{n_pendientes} visita(s) no cabían en la jornada de {merc_destino_clean} "
            "y quedaron en 'Pendientes_Sin_Asignar'."
        )
    return resultado
