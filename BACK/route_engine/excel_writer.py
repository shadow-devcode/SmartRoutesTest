"""
Generacion del archivo Excel de salida.
"""

from __future__ import annotations

import re
from collections import defaultdict
from io import BytesIO
from typing import Optional, Set

import openpyxl
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from route_engine.config import (
    dias_de_mercadista,
    mercadistas_fin_semana,
    carga_jornada,
    cuota_dia,
    cuota_mes,
    hora_fin_jornada,
    jornada_incluye_viaje,
    max_dia_flex,
    max_servicio_dia,
    viaje_en_agenda,
)
from route_engine.run_config import (
    SHEET_CONFIG_PROCESAMIENTO,
    build_config_procesamiento_df,
)
from route_engine.excel_reader import (
    _normalizar_columnas_clave,
    clave_punto_frecuencia,
    columna_frecuencia_mes,
    index_frecuencia_mes_por_punto,
    lookup_frecuencia_mes,
    minutos_normalizados,
    parse_coordenada_a_float,
)
from route_engine.geo import (
    haversine_km,
    minutos_viaje_desde_km,
    normalizar_coord_geografica,
)
from route_engine.mapbox import (
    _geocode_cache,
    calcular_tiempo_entre,
    provincia_display,
    ruta_por_carretera,
)
from route_engine.scheduling import (
    calcular_horario_almuerzo,
    mostrar_progreso,
)


def _ordenar_jornada_por_cercania(grupo):
    """
    Reordena las visitas de una jornada por proximidad (vecino más cercano).

    Hasta aquí el orden de la jornada era el de INSERCIÓN: las visitas que
    añaden el pase de rescate y la red de seguridad caían al final estuvieran
    donde estuvieran, y `_recalcular_ruta` calculaba kilómetros y horas en ese
    orden. De ahí salían los saltos absurdos que se veían en el Excel —una
    visita en Rumiñahui seguida de otra en Puyo— y los horarios corridos hasta
    pasada la medianoche, porque cada trayecto inflado empujaba la hora del
    resto del día.

    Son exactamente las mismas visitas: solo cambia el orden en que se hacen, y
    con él el kilometraje y la hora de cierre.
    """
    if len(grupo) < 3:
        return grupo

    puntos = []
    for pos in range(len(grupo)):
        row = grupo.iloc[pos]
        try:
            puntos.append((pos, float(row["Latitud"]), float(row["Longitud"])))
        except (TypeError, ValueError):
            return grupo  # con una coordenada ilegible, mejor no tocar nada

    orden = [puntos[0]]
    libres = puntos[1:]
    while libres:
        _, lat, lon = orden[-1]
        siguiente = min(libres, key=lambda p: haversine_km(lat, lon, p[1], p[2]))
        libres.remove(siguiente)
        orden.append(siguiente)

    return grupo.iloc[[p[0] for p in orden]]


def _recalcular_ruta(grupo, incluye_viaje=None):
    """
    Recalcula Tiempo entre sucursal, km y Horario para cada ruta
    (Mercadista, Dia, Fecha) en orden.
    Asignación por columnas completas (evita fallos silenciosos con iloc en algunos DataFrames).

    `incluye_viaje` fija el modelo de jornada; `None` usa el activo. Lo pasan
    los editores de rutas, que trabajan sobre un Excel ya generado y deben
    respetar el modelo de ESE archivo, no el que tenga el servidor por defecto.
    """
    grupo = _ordenar_jornada_por_cercania(grupo).copy()
    col_tiempo_entre = "Tiempo entre sucursal (min)"
    col_km = "kilometros entre sucurlas (km)"

    n = len(grupo)
    tiempos_entre: list[float] = []
    kms: list[float] = []
    horarios: list[str] = []

    prev_lat, prev_lon = None, None
    hora_fin_min = 8 * 60
    total_serv = grupo["Tiempo Servicio (min)"].sum()
    aplicar_almuerzo = total_serv != 480

    for i in range(n):
        row = grupo.iloc[i]
        lat = row["Latitud"]
        lon = row["Longitud"]
        if pd.isna(lat) or pd.isna(lon) or lat == "" or lon == "":
            lat_f, lon_f = None, None
        else:
            try:
                lat_f = float(lat)
                lon_f = float(lon)
            except (TypeError, ValueError):
                lat_f, lon_f = None, None

        if lat_f is not None and lon_f is not None:
            tiempo_entre_min, km_entre = calcular_tiempo_entre(prev_lat, prev_lon, lat_f, lon_f)
            # Con "sin tiempo de desplazamiento" la columna va a cero y el
            # horario encadena las visitas; los km sí se conservan.
            tiempos_entre.append(round(viaje_en_agenda(tiempo_entre_min, incluye_viaje), 2))
            kms.append(round(km_entre, 2))
            prev_lat, prev_lon = lat_f, lon_f
        else:
            tiempos_entre.append(0.0)
            kms.append(0.0)

        tiempo_entre_min = tiempos_entre[-1]
        tiempo_serv = float(row["Tiempo Servicio (min)"]) if pd.notna(row["Tiempo Servicio (min)"]) else 0.0
        hora_inicio_prop = hora_fin_min + tiempo_entre_min
        temp_inicio, temp_fin = calcular_horario_almuerzo(hora_inicio_prop, tiempo_serv, aplicar_almuerzo)

        h_inicio = int(temp_inicio) // 60
        m_inicio = int(temp_inicio) % 60
        h_fin = int(temp_fin) // 60
        m_fin = int(temp_fin) % 60
        horarios.append(f"{h_inicio:02d}:{m_inicio:02d} - {h_fin:02d}:{m_fin:02d}")
        hora_fin_min = temp_fin

    grupo[col_tiempo_entre] = tiempos_entre
    grupo[col_km] = kms
    grupo["Horario"] = horarios
    return grupo


def _clave_punto_fila(fila):
    """Identidad de un punto de venta dentro de una fila de horarios."""
    try:
        lat = round(float(fila.get("Latitud") or 0), 6)
        lon = round(float(fila.get("Longitud") or 0), 6)
    except (TypeError, ValueError):
        lat, lon = 0.0, 0.0
    return (str(fila.get("Descripción", "")).strip().upper(), lat, lon)


def _combinado(grupo):
    """Minutos que este grupo consume de la cuota diaria del mercadista.

    Delega en `carga_jornada`: si el desplazamiento no descuenta de la cuota
    (modelo por defecto), aquí tampoco. Medirlo siempre en combinado mientras
    el asignador dimensionaba en servicio hacía que este postproceso recortara
    jornadas perfectamente válidas y las mandara a pendientes.
    """
    return carga_jornada(
        grupo["Tiempo Servicio (min)"].sum(), grupo["Tiempo entre sucursal (min)"].sum()
    )


def _reubicar_en_otro_dia(fila, dias_semana, dia_origen):
    """
    Intenta colocar una visita desplazada en otro día de la MISMA semana y del
    MISMO mercadista.

    Se respeta la regla de negocio de que un punto lo visita siempre el mismo
    mercadista: la visita nunca cambia de persona, solo de día. Antes, cuando
    un día se pasaba de los 480 min, la visita sobrante se descartaba y caía en
    'Pendientes_Sin_Asignar' aunque su mercadista tuviera el resto de la semana
    medio vacío.

    Se prueban los días con más hueco primero, se descartan los días donde ese
    punto ya está agendado (no se duplica una visita en la misma semana) y se
    acepta el primero en el que la jornada recalculada siga cabiendo en los
    480 min combinados.

    Devuelve el nombre del día donde se colocó, o None si no cupo en ninguno.
    """
    clave = _clave_punto_fila(fila)

    # Los días de ese mercaderista, no los de la semana estándar.
    candidatos = []
    for dia in dias_de_mercadista(fila.get("Mercadista")):
        if dia == dia_origen:
            continue
        grupo = dias_semana.get(dia)
        libre = (
            max_dia_flex()
            if grupo is None or grupo.empty
            else max_dia_flex() - _combinado(grupo)
        )
        if libre <= 0:
            continue
        if grupo is not None and not grupo.empty:
            ya_esta = any(
                _clave_punto_fila(r) == clave for _, r in grupo.iterrows()
            )
            if ya_esta:
                continue
        candidatos.append((libre, dia))

    for _, dia in sorted(candidatos, reverse=True):
        grupo = dias_semana.get(dia)
        fila_nueva = fila.copy()
        fila_nueva["Día"] = dia
        if grupo is None or grupo.empty:
            tentativo = pd.DataFrame([fila_nueva])
        else:
            tentativo = pd.concat([grupo, pd.DataFrame([fila_nueva])], ignore_index=True)

        tentativo = _recalcular_ruta(tentativo)
        if _combinado(tentativo) > max_dia_flex():
            continue

        tentativo = tentativo.reset_index(drop=True)
        if "Orden Ruta" in tentativo.columns:
            tentativo["Orden Ruta"] = range(1, len(tentativo) + 1)
        dias_semana[dia] = tentativo
        return dia

    return None


def _aplicar_tope_combinado_diario(df, state=None):
    """
    Para cada grupo (Mercadista, Día, Fecha), asegura que
    sum(Tiempo Servicio) + sum(Tiempo entre sucursal) ≤ max_dia_flex().

    Si un grupo se pasa, saca visitas de la cola (mayor 'Orden Ruta') hasta que
    encaje y las REUBICA en otro día de la misma semana del mismo mercadista
    (ver `_reubicar_en_otro_dia`). Solo las que no caben en ningún día acaban en
    `state.puntos_pendientes` y en la hoja "Pendientes_Sin_Asignar".
    """
    import sys as _sys

    if state is not None and getattr(state, "df", None) is not None:
        state._freq_idx_pendientes = index_frecuencia_mes_por_punto(state.df)

    col_serv = "Tiempo Servicio (min)"
    col_travel = "Tiempo entre sucursal (min)"

    # Numéricos: NaN/strings → 0 para sumar con seguridad
    df[col_serv] = pd.to_numeric(df[col_serv], errors="coerce").fillna(0)
    df[col_travel] = pd.to_numeric(df[col_travel], errors="coerce").fillna(0)

    descartadas_total = 0
    reubicadas_total = 0
    grupos_recortados = 0
    nuevos_grupos = []

    # Se trabaja semana a semana de cada mercadista para poder mover una visita
    # de un día a otro dentro de esa misma semana.
    for (merc, fecha), semana in df.groupby(["Mercadista", "Fecha"], sort=False):
        dias_semana = {
            dia: grupo.copy() for dia, grupo in semana.groupby("Día", sort=False)
        }
        desplazadas = []

        for dia in list(dias_semana.keys()):
            grupo = dias_semana[dia]
            combinado = _combinado(grupo)
            if combinado <= max_dia_flex():
                continue

            if "Orden Ruta" in grupo.columns:
                grupo = grupo.sort_values("Orden Ruta", kind="stable").copy()

            recortadas = []
            while combinado > max_dia_flex() and len(grupo) > 0:
                # Excepción: una visita que POR SÍ SOLA dura más que la jornada
                # (p. ej. 540 min con una cuota de 480). No cabe en ningún día
                # por definición, así que recortarla la condena a pendientes
                # para siempre y ese punto no se atendería nunca. Es la misma
                # excepción que aplica el confirmador al armar el día.
                if (
                    len(grupo) == 1
                    and float(grupo.iloc[0].get(col_serv, 0) or 0) > max_dia_flex()
                ):
                    break
                recortadas.append(grupo.iloc[-1])
                grupo = grupo.iloc[:-1]
                if grupo.empty:
                    break
                combinado = _combinado(grupo)

            if recortadas:
                grupos_recortados += 1
                if not grupo.empty:
                    grupo = _recalcular_ruta(grupo).reset_index(drop=True)
                    if "Orden Ruta" in grupo.columns:
                        grupo["Orden Ruta"] = range(1, len(grupo) + 1)
                dias_semana[dia] = grupo
                for fila in recortadas:
                    desplazadas.append((dia, fila))

        # Reubicar: primero las visitas más largas, que son las que menos
        # opciones tienen de encontrar hueco después.
        desplazadas.sort(key=lambda par: float(par[1].get(col_serv, 0) or 0), reverse=True)
        for dia_origen, fila in desplazadas:
            destino = _reubicar_en_otro_dia(fila, dias_semana, dia_origen)
            if destino is not None:
                reubicadas_total += 1
                continue

            descartadas_total += 1
            print(
                f"      [!] {merc} | {fecha}: {fila.get('Descripción', '')} no cabe en "
                f"ningún día de la semana (serv={float(fila.get(col_serv, 0) or 0):.0f} min); "
                "queda pendiente.",
                file=_sys.stderr,
                flush=True,
            )
            if state is not None:
                try:
                    lat_o = float(fila.get("Latitud"))
                    lon_o = float(fila.get("Longitud"))
                except (TypeError, ValueError):
                    lat_o, lon_o = None, None
                pend_dict = {
                    "motivo": (
                        f"tope diario combinado >{max_dia_flex()} min: "
                        f"sin hueco en ningún día de {merc}/{fecha}"
                    ),
                    "descripcion": fila.get("Descripción", ""),
                    "lat": lat_o,
                    "lon": lon_o,
                    "semana": fecha,
                    "tiempo": float(fila.get(col_serv, 0) or 0),
                    "mercadista_origen": merc,
                    "dia_origen": dia_origen,
                }
                if getattr(state, "_freq_idx_pendientes", None):
                    fm = lookup_frecuencia_mes(
                        state._freq_idx_pendientes, fila.get("Descripción", ""), lat_o, lon_o
                    )
                    if fm is not None:
                        pend_dict["frecuencia_mes"] = fm
                state.puntos_pendientes.append(pend_dict)

        for grupo in dias_semana.values():
            if grupo is not None and not grupo.empty:
                nuevos_grupos.append(grupo)

    if grupos_recortados:
        print(
            f"      [!] Tope diario combinado: {reubicadas_total} visita(s) movidas a "
            f"otro día de su semana y {descartadas_total} sin hueco, en "
            f"{grupos_recortados} jornada(s) que superaban "
            f"{max_dia_flex()} min (servicio+desplazamiento).",
            file=_sys.stderr,
            flush=True,
        )

    if not nuevos_grupos:
        return df.iloc[0:0]
    return pd.concat(nuevos_grupos, ignore_index=True)


def _postprocesar_horarios(horarios_df, visit_instances, state):
    """
    Post-procesamiento del DataFrame de horarios:
    - Eliminar duplicados
    - Aplicar tope de filas
    - Recorte defensivo por limites de servicio
    - Recalcular rutas
    """
    if horarios_df.empty:
        return horarios_df

    # 1) Deduplicar por ubicacion
    try:
        if all(c in horarios_df.columns for c in ["Mercadista", "Día", "Fecha", "Latitud", "Longitud", "Descripción"]):
            def _to_round(v):
                if pd.isna(v) or v == "":
                    return None
                try:
                    return round(float(str(v).replace(",", ".")), 6)
                except (ValueError, TypeError):
                    return None

            horarios_df["_lat6"] = horarios_df["Latitud"].apply(_to_round)
            horarios_df["_lon6"] = horarios_df["Longitud"].apply(_to_round)
            n_antes = len(horarios_df)
            subset_cols = ["Mercadista", "Día", "Fecha", "_lat6", "_lon6", "Descripción"]
            if "Tiempo Servicio (min)" in horarios_df.columns:
                subset_cols.append("Tiempo Servicio (min)")
            if "Horario" in horarios_df.columns:
                subset_cols.append("Horario")
            horarios_df = horarios_df.drop_duplicates(
                subset=subset_cols, keep="first"
            )
            horarios_df = horarios_df.drop(columns=["_lat6", "_lon6"], errors="ignore")
            if len(horarios_df) < n_antes:
                print(f"      -> Eliminados {n_antes - len(horarios_df)} duplicados (mismo punto mismo dia).")
    except Exception:
        pass

    # 2) Tope estricto de filas
    total_esperado = len(visit_instances)
    if len(horarios_df) > total_esperado:
        sort_cols = [c for c in ["Mercadista", "Día", "Fecha"] if c in horarios_df.columns]
        if sort_cols:
            horarios_df = horarios_df.sort_values(by=sort_cols).head(total_esperado).copy()
        else:
            horarios_df = horarios_df.head(total_esperado).copy()
        print(f"      -> Ajustado a {total_esperado} filas (tope FRECUENCIA MES).")

    # 3) Recorte defensivo: ningun grupo puede superar max_servicio_dia()
    if "Fecha" in horarios_df.columns and "Tiempo Servicio (min)" in horarios_df.columns:
        grupos = list(horarios_df.groupby(["Mercadista", "Día", "Fecha"], sort=False))
        filas_ok = []
        filas_exceso = []
        for (merc, dia, fecha), grupo in grupos:
            total = grupo["Tiempo Servicio (min)"].sum()
            if total <= max_dia_flex():
                filas_ok.append(grupo.copy())
                continue
            # Excepción: un día con UNA sola visita más larga que la jornada.
            # No hay recorte posible —quitarla deja el día vacío y el punto sin
            # atender—, así que se respeta tal cual.
            if len(grupo) == 1 and float(grupo.iloc[0]["Tiempo Servicio (min)"] or 0) > max_dia_flex():
                filas_ok.append(grupo.copy())
                continue
            acum = 0.0
            for idx, row in grupo.iterrows():
                t = float(row["Tiempo Servicio (min)"]) if pd.notna(row["Tiempo Servicio (min)"]) else 0.0
                r = row.to_dict()
                r["_Mercadista"] = merc
                r["_Día"] = dia
                r["_Fecha"] = fecha
                if acum + t <= max_dia_flex():
                    acum += t
                    filas_ok.append(pd.DataFrame([row]))
                else:
                    filas_exceso.append((r, t))

        horarios_df = pd.concat(filas_ok, ignore_index=True) if filas_ok else pd.DataFrame()

        # Reasignar exceso
        tot_mes_por_merc = (
            horarios_df.groupby("Mercadista")["Tiempo Servicio (min)"].sum().to_dict()
            if not horarios_df.empty
            else {}
        )
        for r, t in filas_exceso:
            merc = r.pop("_Mercadista")
            dia_orig = r.pop("_Día")
            fecha_orig = r.pop("_Fecha")
            if horarios_df.empty:
                horarios_df = pd.DataFrame([r])
                tot_mes_por_merc[r.get("Mercadista", merc)] = tot_mes_por_merc.get(
                    r.get("Mercadista", merc), 0
                ) + t
                continue
            tot = horarios_df.groupby(["Mercadista", "Día", "Fecha"], sort=False)[
                "Tiempo Servicio (min)"
            ].sum()
            candidatos = [
                (k, tot.get(k, 0))
                for k in tot.index
                if k[0] == merc
                and tot.get(k, 0) + t <= max_dia_flex()
                and tot_mes_por_merc.get(k[0], 0) + t <= cuota_mes()
            ]
            if candidatos:
                (merc_d, dia_d, fecha_d), _ = min(candidatos, key=lambda x: x[1])
                r["Mercadista"] = merc_d
                r["Día"] = dia_d
                r["Fecha"] = fecha_d
                tot_mes_por_merc[merc_d] = tot_mes_por_merc.get(merc_d, 0) + t
            else:
                # No se crean mercadistas adicionales; la fila en exceso no se reasigna
                continue
            horarios_df = pd.concat([horarios_df, pd.DataFrame([r])], ignore_index=True)

    # 4) Renumerar Orden Ruta por grupo
    if "Fecha" in horarios_df.columns:
        horarios_df["_idx_orig"] = horarios_df.index
        horarios_df.sort_values(by=["Mercadista", "Día", "Fecha", "_idx_orig"], inplace=True)
        horarios_df["Orden Ruta"] = (
            horarios_df.groupby(["Mercadista", "Día", "Fecha"], sort=False).cumcount() + 1
        )
        horarios_df.drop(columns=["_idx_orig"], inplace=True)
        horarios_df.sort_values(by=["Mercadista", "Día", "Fecha", "Orden Ruta"], inplace=True)

    # 5) Recalcular rutas
    # Pandas 3 cambió el default de groupby().apply() y ya no incluye las
    # columnas de agrupación en el DataFrame que llega a la función. Si usamos
    # .apply(_recalcular_ruta).reset_index(drop=True) las columnas Mercadista/
    # Día/Fecha se pierden por completo (el output queda inutilizable para el
    # frontend). Iteramos manualmente: el grupo siempre incluye todas las
    # columnas en cualquier versión de pandas.
    if "Fecha" in horarios_df.columns:
        grupos = horarios_df.groupby(["Mercadista", "Día", "Fecha"], sort=False)
    else:
        grupos = horarios_df.groupby(["Mercadista", "Día"], sort=False)
    partes = [_recalcular_ruta(grupo) for _, grupo in grupos]
    if partes:
        horarios_df = pd.concat(partes, ignore_index=True)

    # 5.5) Tope diario combinado servicio+desplazamiento ≤ max_dia_flex().
    # Después de recalcular tenemos los tiempos de desplazamiento REALES. Si un
    # grupo se pasa, descartamos las visitas de la cola (mayor "Orden Ruta")
    # hasta encajar, y luego volvemos a recalcular sus horarios. Las visitas
    # descartadas se acumulan en state.puntos_pendientes (reporte final + hoja
    # Pendientes_Sin_Asignar).
    if (
        "Fecha" in horarios_df.columns
        and "Tiempo Servicio (min)" in horarios_df.columns
        and "Tiempo entre sucursal (min)" in horarios_df.columns
    ):
        horarios_df = _aplicar_tope_combinado_diario(horarios_df, state=state)

    # 6) Orden de columnas
    desired_order = [
        "Mercadista", "Día", "Orden Ruta", "Descripción", "Latitud", "Longitud",
        "CANAL", "CADENA",
        "PROVINCIA", "CIUDAD", "CALLE",
        "Tiempo Servicio (min)", "Duración (hh:mm)", "Tiempo entre sucursal (min)",
        "kilometros entre sucurlas (km)", "Horario", "Fecha",
    ]
    cols = [c for c in desired_order if c in horarios_df.columns] + [
        c for c in horarios_df.columns if c not in desired_order
    ]
    horarios_df = horarios_df[cols]

    return horarios_df


def _semana_a_int(valor) -> Optional[int]:
    """Extrae el número de semana de un valor int o de un string 'semana N'."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):
        return valor if 1 <= valor <= 4 else None
    m = re.search(r"(\d+)", str(valor or ""))
    if not m:
        return None
    n = int(m.group(1))
    return n if 1 <= n <= 4 else None


def _info_puntos_desde_df(df) -> dict:
    """
    {clave_punto_frecuencia(desc,lat,lon): [ {descripcion, lat, lon, tiempo, frecuencia, provincia}, ... ]}
    desde el Excel maestro, agrupado por clave de punto para soportar múltiples tiempos/frecuencias por punto.
    """
    info: dict = defaultdict(list)
    if df is None or df.empty:
        return info
    col_freq = columna_frecuencia_mes(df)
    work = _normalizar_columnas_clave(df.copy())
    if "LATITUD" not in work.columns or "LONGITUD" not in work.columns:
        return info

    for _, row in work.iterrows():
        if row.get("_COORDENADAS_INVALIDAS", False):
            continue
        desc = str(row.get("DESCRIPCION", "") or "").strip()
        if not desc:
            continue
        try:
            lat_val = parse_coordenada_a_float(row.get("LATITUD"))
            lon_val = parse_coordenada_a_float(row.get("LONGITUD"))
            lat_f = lat_val if lat_val is not None else 0.0
            lon_f = lon_val if lon_val is not None else 0.0
        except (TypeError, ValueError):
            continue
        if lat_f == 0.0 and lon_f == 0.0:
            continue
        key = clave_punto_frecuencia(desc, lat_f, lon_f)
        if key[1] is None:
            continue
        # Mismo redondeo que usa el motor al crear las visitas. Sin él, un
        # tiempo calculado con fórmula en Excel (294.0000000000001) no coincide
        # con el de las visitas ya agendadas (294.0), la reconciliación cree que
        # el punto no tiene ninguna visita puesta y duplica su frecuencia entera
        # en pendientes.
        tiempo = minutos_normalizados(row.get("TIEMPO DE SERVICIO", 0))
        freq = pd.to_numeric(row.get(col_freq, 0) if col_freq else 0, errors="coerce")
        info[key].append({
            "descripcion": desc,
            "lat": lat_f,
            "lon": lon_f,
            "tiempo": tiempo,
            "frecuencia": int(freq) if pd.notna(freq) and int(freq) > 0 else 1,
            "provincia": str(row.get("PROVINCIA_COORD", "") or "").strip(),
        })
    return info


def _prioridad_sobrante(motivo: str) -> int:
    """Orden en que se descartan las pendientes que sobran.

    Primero las sintéticas de una reconciliación anterior, después las que
    describen un intento de colocación fallido (red de seguridad / rescate) y
    por último las genéricas: si hay que quitar una, mejor perder la que menos
    cuenta sobre lo que pasó.
    """
    m = str(motivo or "").lower()
    if m.startswith("reconciliación") or m.startswith("reconciliacion"):
        return 0
    if m.startswith("red de seguridad") or m.startswith("rescue"):
        return 1
    return 2


def recalcular_tramos_por_carretera(horarios_df):
    """
    Sustituye la estimación de desplazamiento por los kilómetros y minutos
    REALES de carretera de cada jornada.

    El motor planifica con una estimación —distancia en línea recta corregida
    por un factor— porque necesita comparar miles de combinaciones sin salir a
    la red. Pero lo que se entrega tiene que ser lo que el mercaderista va a
    recorrer de verdad, y en una ciudad la diferencia entre la recta y la calle
    no es un factor fijo: un río, una quebrada o una vía de un solo sentido la
    duplican.

    Se consulta la misma API que dibuja la «Vista Carretera» del mapa, con las
    paradas del día en su orden de visita, de modo que los kilómetros del Excel
    y los del mapa son el mismo número. Una consulta por jornada, y como las
    cuatro semanas repiten la misma ruta, el cache las reduce a una.

    Si no hay token de Mapbox, o la consulta falla, la jornada se queda con su
    estimación: mejor un número aproximado que ninguno.

    Devuelve (jornadas_actualizadas, jornadas_totales, jornadas_que_se_pasan).
    """
    columnas = {"Mercadista", "Día", "Fecha", "Latitud", "Longitud", "Orden Ruta"}
    if horarios_df.empty or not columnas.issubset(set(horarios_df.columns)):
        return 0, 0

    col_km = "kilometros entre sucurlas (km)"
    col_viaje = "Tiempo entre sucursal (min)"
    actualizadas = 0
    tarde = 0
    limite = hora_fin_jornada()
    grupos = list(horarios_df.groupby(["Mercadista", "Fecha", "Día"], sort=False))

    for _clave, grupo in grupos:
        orden = grupo.sort_values("Orden Ruta", kind="stable")
        coords = []
        indices = []
        for idx, fila in orden.iterrows():
            lat = parse_coordenada_a_float(fila.get("Latitud"))
            lon = parse_coordenada_a_float(fila.get("Longitud"))
            if lat is None or lon is None or (lat == 0 and lon == 0):
                coords = []
                break
            coords.append((lat, lon))
            indices.append(idx)
        if len(coords) < 2:
            continue

        tramos = ruta_por_carretera(coords)
        if not tramos:
            continue

        # El primer punto del día no tiene tramo anterior: su desplazamiento es
        # cero y el tramo i-1 corresponde a la parada i.
        horarios_df.at[indices[0], col_km] = 0.0
        horarios_df.at[indices[0], col_viaje] = 0.0
        for posicion, (_minutos_mapbox, km) in enumerate(tramos, start=1):
            horarios_df.at[indices[posicion], col_km] = km
            # Los kilómetros salen de la carretera real; los minutos, de la
            # regla de la empresa (4 min por cada 0,5 km). La duración que
            # devuelve Mapbox es la de un coche en tráfico libre y no recoge
            # aparcar, localizar el punto ni entrar, que es donde se va el
            # tiempo de una visita comercial.
            horarios_df.at[indices[posicion], col_viaje] = round(
                minutos_viaje_desde_km(km), 2
            )
        actualizadas += 1

        # Con los kilómetros reales una jornada puede estirarse más allá de lo
        # que se planificó. No se recorta: eso escondería el problema. Se
        # cuentan para avisar, que es lo que permite decidir qué hacer con
        # ellas.
        if _recomponer_horarios_del_dia(horarios_df, indices) > limite:
            tarde += 1

    return actualizadas, len(grupos), tarde


def _recomponer_horarios_del_dia(horarios_df, indices):
    """Reescribe las horas de la jornada encadenando servicio y viaje reales.

    Sin esto, las columnas de kilómetros dirían una cosa y las horas otra: la
    visita de las 11:30 seguiría marcada a las 11:30 aunque llegar hasta allí
    cueste ahora veinte minutos más.

    La hora de inicio de la jornada se respeta; lo que se recalcula es el
    encadenamiento a partir de ella. Devuelve el minuto en que termina.
    """
    if "Horario" not in horarios_df.columns:
        return
    primera = str(horarios_df.at[indices[0], "Horario"] or "").strip()
    inicio = _minutos_desde_horario(primera)
    if inicio is None:
        return 0

    reloj = inicio
    for posicion, idx in enumerate(indices):
        if posicion > 0:
            try:
                reloj += float(horarios_df.at[idx, "Tiempo entre sucursal (min)"] or 0)
            except (TypeError, ValueError):
                pass
        try:
            servicio = float(horarios_df.at[idx, "Tiempo Servicio (min)"] or 0)
        except (TypeError, ValueError):
            servicio = 0.0
        fin = reloj + servicio
        horarios_df.at[idx, "Horario"] = (
            f"{int(reloj) // 60:02d}:{int(reloj) % 60:02d} - "
            f"{int(fin) // 60:02d}:{int(fin) % 60:02d}"
        )
        reloj = fin

    return reloj


def _minutos_desde_horario(texto):
    """Minutos desde medianoche de la hora de inicio de un 'HH:MM - HH:MM'."""
    if not texto:
        return None
    inicio = texto.split("-")[0].strip().split(",")[0].strip()
    partes = inicio.split(":")
    if len(partes) != 2:
        return None
    try:
        return int(partes[0]) * 60 + int(partes[1])
    except ValueError:
        return None


def reconciliar_pendientes_por_frecuencia(horarios_df, df, state) -> int:
    """
    Garantiza la regla de negocio: para cada punto y cada configuración (tiempo, frecuencia),
        (visitas agendadas en Horarios_Detalle) + (filas en Pendientes_Sin_Asignar)
        == FRECUENCIA MES de esa configuración.

    Soporta rutas de Medio Tiempo donde un mismo punto físico tiene distintas
    configuraciones de tiempo de servicio y frecuencia.
    """
    info = _info_puntos_desde_df(df)
    if not info:
        return 0

    # 1) Visitas agendadas por (clave_punto, tiempo) y semanas usadas por clave
    agendadas_por_config = defaultdict(int)
    semanas_usadas = defaultdict(set)
    cols_ok = {"Descripción", "Latitud", "Longitud"}.issubset(set(horarios_df.columns))
    if not horarios_df.empty and cols_ok:
        for _, r in horarios_df.iterrows():
            key = clave_punto_frecuencia(
                r.get("Descripción"), r.get("Latitud"), r.get("Longitud")
            )
            if key[1] is None:
                continue
            # Redondeado igual que en `info`: la pareja (punto, tiempo) es la
            # clave con la que se cruzan agendadas y frecuencia esperada.
            t_val = minutos_normalizados(r.get("Tiempo Servicio (min)", 0))
            agendadas_por_config[(key, t_val)] += 1

            sem = _semana_a_int(r.get("Fecha"))
            if sem is not None:
                semanas_usadas[key].add(sem)

    # 2) Pendientes existentes por (clave_punto, tiempo).
    #
    # Se cuentan las que REALMENTE van a escribirse, aplicando la misma
    # deduplicación que la hoja: (descripción, lat, lon, semana, token). Contar
    # la lista en bruto inflaba el total y hacía creer que el punto ya estaba
    # cubierto. Con un punto de frecuencia 20 el rescate registra 5 pendientes
    # por semana, la hoja escribe una, y las otras cuatro no aparecían por
    # ningún lado: ni agendadas, ni pendientes, ni reconciliadas.
    pend_por_config = defaultdict(int)
    vistas_pend = set()
    # Entradas de `state.puntos_pendientes` que forman cada fila escrita, para
    # poder retirarlas enteras si sobran.
    entradas_por_grupo: dict = defaultdict(list)
    grupos_por_config: dict = defaultdict(list)
    for pos, p in enumerate(state.puntos_pendientes):
        key = clave_punto_frecuencia(p.get("descripcion"), p.get("lat"), p.get("lon"))
        if key[1] is None:
            continue
        sem = _semana_a_int(p.get("semana"))
        dedup_key = (key, sem, p.get("_dedup_token", ""))
        entradas_por_grupo[dedup_key].append(pos)
        if dedup_key in vistas_pend:
            continue
        vistas_pend.add(dedup_key)

        t_val = minutos_normalizados(p.get("tiempo", 0))
        pend_por_config[(key, t_val)] += 1
        grupos_por_config[(key, t_val)].append((dedup_key, str(p.get("motivo", ""))))

        if sem is not None:
            semanas_usadas[key].add(sem)

    añadidas = 0
    sobrantes = 0
    a_retirar: set = set()
    for key, configs in info.items():
        usadas = set(semanas_usadas.get(key, set()))
        for idx_conf, conf in enumerate(configs):
            t_val = conf["tiempo"]
            freq_esperada = conf["frecuencia"]
            ya_cubiertas = agendadas_por_config.get((key, t_val), 0) + pend_por_config.get((key, t_val), 0)
            deficit = freq_esperada - ya_cubiertas
            if deficit < 0:
                # Sobran pendientes: el punto tiene ya toda su frecuencia
                # agendada y además filas en la hoja. Pasa cuando una fase
                # tardía coloca una visita que una anterior había dado por
                # perdida —la cuadrilla de fin de semana recoge lo que la red
                # de seguridad no pudo—, porque quien la registró como
                # pendiente no se entera de que después encontró hueco.
                #
                # La regla del negocio es una igualdad, no un mínimo:
                # agendadas + pendientes == FRECUENCIA MES. Sin este recorte,
                # un punto con sus 8 visitas puestas seguía pidiendo 4 más en
                # el panel de pendientes.
                candidatos = sorted(
                    grupos_por_config.get((key, t_val), []),
                    key=lambda g: _prioridad_sobrante(g[1]),
                )
                for dedup_key, _motivo in candidatos[: -deficit]:
                    a_retirar.update(entradas_por_grupo.get(dedup_key, ()))
                    sobrantes += 1
                continue
            if deficit == 0:
                continue

            for i in range(deficit):
                libre = next((s for s in range(1, 5) if s not in usadas), None)
                semana_asignada = libre if libre is not None else ((i % 4) + 1)
                usadas.add(semana_asignada)
                state.puntos_pendientes.append({
                    "motivo": (
                        "reconciliación: visita faltante para completar "
                        f"frecuencia mensual de {t_val:.0f}min ({ya_cubiertas + i + 1} de {freq_esperada})"
                    ),
                    "descripcion": conf["descripcion"],
                    "lat": conf["lat"],
                    "lon": conf["lon"],
                    "semana": semana_asignada,
                    "tiempo": t_val,
                    "provincia_punto": conf["provincia"],
                    "frecuencia_mes": freq_esperada,
                    "_dedup_token": f"recon-{key}-{t_val}-{idx_conf}-{i}",
                })
                añadidas += 1

    if a_retirar:
        state.puntos_pendientes[:] = [
            p for i, p in enumerate(state.puntos_pendientes) if i not in a_retirar
        ]

    if añadidas or sobrantes:
        import sys as _sys
        if añadidas:
            print(
                f"      [!] Reconciliación de frecuencia: {añadidas} visita(s) "
                "faltante(s) añadidas a 'Pendientes_Sin_Asignar' para cumplir "
                "(agendadas + pendientes = frecuencia mensual).",
                file=_sys.stderr,
                flush=True,
            )
        if sobrantes:
            print(
                f"      [!] Reconciliación de frecuencia: {sobrantes} pendiente(s) "
                "retirada(s); esas visitas ya estaban agendadas.",
                file=_sys.stderr,
                flush=True,
            )
    return añadidas


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


def _indice_canal_cadena(df):
    """
    Canal y cadena de cada punto, tal como vienen en el Excel de entrada.

    Se indexa por coordenadas MÁS nombre, y se deja el nombre y las coordenadas
    sueltos como respaldo. Ninguna de las dos claves basta por su cuenta: en el
    archivo real hay tres tiendas distintas con las mismas coordenadas —una de
    otra cadena—, así que indexar solo por coordenadas le colgaba a SUPER AKI LA
    JOYA la cadena de su vecino; y el nombre solo falla si las coordenadas se
    corrigieron por el camino.

    Sin este índice habría que arrastrar los dos campos por todo el motor
    —asignación, rescate, red de seguridad— para acabar escribiéndolos igual al
    final.
    """
    from route_engine.excel_reader import columna_canal, columna_cadena

    col_canal = columna_canal(df)
    col_cadena = columna_cadena(df)
    if col_canal is None and col_cadena is None:
        return {}, {}, {}

    col_desc = None
    for candidata in ("DESCRIPCION", "Descripción", "DESCRIPCIÓN", "Descripcion"):
        if candidata in df.columns:
            col_desc = candidata
            break

    por_local, por_nombre, por_coord = {}, {}, {}
    for _, fila in df.iterrows():
        canal = str(fila.get(col_canal, "") or "").strip() if col_canal else ""
        cadena = str(fila.get(col_cadena, "") or "").strip().upper() if col_cadena else ""
        if not canal and not cadena:
            continue
        nombre = str(fila.get(col_desc, "") or "").strip().upper() if col_desc else ""
        try:
            coord = (
                round(float(fila.get("LATITUD")), 6),
                round(float(fila.get("LONGITUD")), 6),
            )
        except (TypeError, ValueError):
            coord = None
        if coord and nombre:
            por_local.setdefault((coord, nombre), (canal, cadena))
        if nombre:
            por_nombre.setdefault(nombre, (canal, cadena))
        if coord:
            por_coord.setdefault(coord, (canal, cadena))
    return por_local, por_nombre, por_coord


def _agregar_canal_cadena(horarios_df, df):
    """
    Añade CANAL y CADENA a las filas de horarios, buscándolos por coordenadas y,
    si no cuadran, por nombre del punto.

    Si el Excel de entrada no trae esas columnas, el DataFrame sale intacto: los
    archivos antiguos siguen generándose exactamente igual que antes.
    """
    if horarios_df.empty:
        return horarios_df

    por_local, por_nombre, por_coord = _indice_canal_cadena(df)
    if not por_local and not por_nombre and not por_coord:
        return horarios_df

    def _buscar(fila):
        try:
            coord = (
                round(float(fila.get("Latitud")), 6),
                round(float(fila.get("Longitud")), 6),
            )
        except (TypeError, ValueError):
            coord = None
        nombre = str(fila.get("Descripción", "") or "").strip().upper()
        dato = por_local.get((coord, nombre)) if coord and nombre else None
        if dato is None and nombre:
            dato = por_nombre.get(nombre)
        if dato is None and coord:
            dato = por_coord.get(coord)
        return dato or ("", "")

    valores = horarios_df.apply(_buscar, axis=1)
    horarios_df = horarios_df.copy()
    horarios_df["CANAL"] = [v[0] for v in valores]
    horarios_df["CADENA"] = [v[1] for v in valores]
    return horarios_df


def generar_excel_salida(output_file, horarios_df, df, visit_instances, state):
    """
    Genera el archivo Excel de salida con las hojas:
    - Horarios_Detalle
    - Categorias
    """
    import sys as _sys

    _required_cols = ("Mercadista", "Día", "Fecha")
    _missing_required = [c for c in _required_cols if c not in horarios_df.columns]
    if _missing_required:
        # Bandera visible: este archivo será inservible para el frontend (vista
        # de rutas/comparativa rompen sin estas columnas). El motivo suele
        # estar más arriba (asignación vacía, formato de entrada incompatible).
        print(
            "      [!!!] ATENCIÓN: horarios_df no contiene las columnas "
            f"requeridas {_missing_required}. El Excel generado quedará "
            "incompleto y las vistas de rutas/comparativa fallarán al leerlo. "
            "Revisa el Excel de entrada y el log de asignación.",
            file=_sys.stderr,
            flush=True,
        )
        print(
            f"      [!!!] Columnas presentes en horarios_df: {list(horarios_df.columns)}",
            file=_sys.stderr,
            flush=True,
        )
        print(
            f"      [!!!] all_day_summaries tiene {len(state.all_day_summaries)} entradas; "
            f"primera entrada: {state.all_day_summaries[0] if state.all_day_summaries else 'VACÍO'}",
            file=_sys.stderr,
            flush=True,
        )

    # Rellenar PROVINCIA/CIUDAD/CALLE desde cache
    for summary in state.all_day_summaries:
        lat = summary.get("Latitud", "")
        lon = summary.get("Longitud", "")
        if lat == "" or lon == "":
            continue
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except Exception:
            continue
        if lat_f == 0.0 and lon_f == 0.0:
            continue
        key_coord = (round(lat_f, 6), round(lon_f, 6))
        provincia, ciudad, calle = _geocode_cache.get(key_coord, ("", "", ""))
        summary["PROVINCIA"] = provincia
        summary["CIUDAD"] = ciudad
        summary["CALLE"] = calle

    # Canal y cadena de cada visita, tomados del Excel de entrada. Se añaden
    # aquí, sobre el DataFrame ya montado: llevarlos por todo el motor solo para
    # escribirlos al final no aportaría nada.
    horarios_df = _agregar_canal_cadena(horarios_df, df)

    horarios_df = _postprocesar_horarios(horarios_df, visit_instances, state)
    horarios_df = drop_spurious_total_rows_horarios_df(horarios_df)

    # Kilómetros y minutos REALES de carretera, ya con las rutas cerradas. El
    # motor planifica con una estimación para poder comparar miles de
    # combinaciones sin salir a la red; lo que se entrega debe ser lo que se
    # recorre de verdad.
    jornadas_ok, jornadas_total, jornadas_tarde = recalcular_tramos_por_carretera(horarios_df)
    if jornadas_total:
        if jornadas_ok:
            print(
                f"      -> Desplazamiento por carretera: {jornadas_ok} de "
                f"{jornadas_total} jornada(s) recalculadas con distancias reales"
            )
            if jornadas_tarde:
                limite_txt = f"{hora_fin_jornada() // 60:02d}:{hora_fin_jornada() % 60:02d}"
                print(
                    f"      [!] {jornadas_tarde} jornada(s) terminan después de las "
                    f"{limite_txt} con los tiempos reales de carretera. No se "
                    f"recortan: revisar si esas visitas deben pasar a otro día."
                )
        else:
            print(
                "      [!] No se pudo consultar la distancia por carretera "
                "(¿falta MAPBOX_ACCESS_TOKEN?); se mantiene la estimación en "
                "línea recta corregida."
            )

    # Reconciliación de frecuencia: tras fijar el set agendado definitivo,
    # asegurar que por cada punto (agendadas + pendientes) == FRECUENCIA MES.
    # Cubre visitas que se perdieron río arriba sin quedar en pendientes.
    reconciliar_pendientes_por_frecuencia(horarios_df, df, state)

    # Validacion de conteo
    total_esperado = len(visit_instances)
    total_generado = len(horarios_df) if not horarios_df.empty else 0
    if total_esperado != total_generado:
        print(
            f"      [!] Diferencia: se esperaban {total_esperado} visitas "
            f"y se generaron {total_generado} filas en Horarios_Detalle."
        )

    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        print("      -> Escribiendo hoja 'Horarios_Detalle'...")
        horarios_df.to_excel(writer, sheet_name="Horarios_Detalle", index=False)

        try:
            ws = writer.book["Horarios_Detalle"]
            plan_names = set(state.plan_names_base)
            nombres_en_excel = (
                set(horarios_df["Mercadista"].astype(str).unique())
                if "Mercadista" in horarios_df.columns
                else set()
            )
            mercadistas_extra = nombres_en_excel - plan_names
            format_horarios_detalle_worksheet(ws, mercadistas_extra if mercadistas_extra else None)
        except Exception:
            pass

        # Hoja Categorias
        print("      -> Escribiendo hoja 'Categorias'...")
        provincias = []
        ciudades = []
        calles = []
        for _, row in df.iterrows():
            lat = row.get("LATITUD", 0)
            lon = row.get("LONGITUD", 0)
            try:
                lat_f = float(lat)
                lon_f = float(lon)
                key_coord = (round(lat_f, 6), round(lon_f, 6))
                provincia, ciudad, calle = _geocode_cache.get(key_coord, ("", "", ""))
            except Exception:
                provincia, ciudad, calle = ("", "", "")
            provincias.append(provincia)
            ciudades.append(ciudad)
            calles.append(calle)

        categorias_df = pd.DataFrame({
            "ID": df.index + 1,
            "DESCRIPCION": df.get("DESCRIPCION", ""),
            "CATEGORIA": df.get("CATEGORIA", "DESCONOCIDO"),
            "PROVINCIA": provincias,
            "CIUDAD": ciudades,
            "CALLE": calles,
        })
        categorias_df.to_excel(writer, sheet_name="Categorias", index=False)

        # Hoja Frecuencia_Puntos: mercadista, punto único (Descripción), frecuencia de visitas
        print("      -> Escribiendo hoja 'Frecuencia_Puntos'...")
        if not horarios_df.empty and "Mercadista" in horarios_df.columns and "Descripción" in horarios_df.columns:
            freq_df = (
                horarios_df.groupby(["Mercadista", "Descripción"], dropna=False)
                .size()
                .reset_index(name="Frecuencia")
            )
            freq_df = freq_df.sort_values(["Mercadista", "Frecuencia"], ascending=[True, False])
            freq_df.to_excel(writer, sheet_name="Frecuencia_Puntos", index=False)

        # Hoja Provincias_Porcentaje: ocupación de la jornada por mercadista y
        # provincia. El 100% es la capacidad mensual COMBINADA de un mercadista
        # (cuota_mes() = 480 min/día × 5 × 4 = 9600), y los
        # minutos contados son servicio + desplazamiento.
        #
        # Antes se dividía solo el servicio entre 10000: un mercadista con 300
        # min en tienda y 180 en carretera —jornada de 8 h completa— aparecía
        # al 60%, y parecía que sobraba capacidad donde no la había.
        print("      -> Escribiendo hoja 'Provincias_Porcentaje'...")
        if not horarios_df.empty and "Mercadista" in horarios_df.columns and "PROVINCIA" in horarios_df.columns:
            col_serv = "Tiempo Servicio (min)"
            col_viaje = "Tiempo entre sucursal (min)"
            prov_calc = horarios_df.copy()
            prov_calc[col_serv] = pd.to_numeric(prov_calc.get(col_serv, 0), errors="coerce").fillna(0)
            prov_calc[col_viaje] = pd.to_numeric(prov_calc.get(col_viaje, 0), errors="coerce").fillna(0)
            prov_calc["_combinado"] = carga_jornada(prov_calc[col_serv], prov_calc[col_viaje])
            prov_df = (
                prov_calc.groupby(["Mercadista", "PROVINCIA"], dropna=False)[
                    [col_serv, "_combinado"]
                ]
                .sum()
                .reset_index()
            )
            prov_df["PROVINCIA"] = prov_df["PROVINCIA"].fillna("").astype(str).str.strip()
            prov_df = prov_df[prov_df["PROVINCIA"] != ""]
            prov_df["Minutos"] = prov_df["_combinado"].round(0).astype(int)
            prov_df["Minutos Servicio"] = prov_df[col_serv].round(0).astype(int)
            prov_df["Porcentaje"] = (
                (prov_df["Minutos"] / cuota_mes()) * 100
            ).round(1)
            prov_df["Provincia_Porcentaje"] = (
                prov_df["PROVINCIA"].astype(str) + " " + prov_df["Porcentaje"].astype(str) + "%"
            )
            prov_df = prov_df[
                ["Mercadista", "Provincia_Porcentaje", "Minutos", "Minutos Servicio"]
            ]
            prov_df = prov_df.sort_values(["Mercadista", "Minutos"], ascending=[True, False])
            prov_df.to_excel(writer, sheet_name="Provincias_Porcentaje", index=False)

        # Hoja Pendientes_Sin_Asignar: visitas que no se pudieron asignar a
        # ningún mercadista/día por superar el tope de jornada (480 min de
        # servicio+desplazamiento) o por falta de hueco mensual. Una fila por
        # (descripción, semana) deduplicada.
        print("      -> Escribiendo hoja 'Pendientes_Sin_Asignar'...")
        freq_idx = index_frecuencia_mes_por_punto(df) if df is not None else {}
        pendientes_data = []
        seen_key = set()
        for p in state.puntos_pendientes:
            desc = str(p.get("descripcion", "")).strip()
            semana_v = p.get("semana", "")
            semana_str = (
                f"semana {semana_v}"
                if isinstance(semana_v, int)
                else str(semana_v).strip()
            )
            try:
                lat_v = float(p.get("lat") or 0)
                lon_v = float(p.get("lon") or 0)
            except (TypeError, ValueError):
                lat_v, lon_v = 0.0, 0.0
            # _dedup_token único en filas de reconciliación: evita que varias
            # visitas faltantes del mismo punto/semana se colapsen en una sola.
            key = (desc, round(lat_v, 6), round(lon_v, 6), semana_str, p.get("_dedup_token", ""))
            if key in seen_key:
                continue
            seen_key.add(key)
            # Enriquecer provincia/ciudad desde el cache de geocoding (PROVINCIA,
            # CIUDAD, CALLE indexadas por (lat,lon) redondeado). Si el punto ya
            # trae provincia_punto desde el motor, la conservamos.
            prov_cache, ciudad_cache, calle_cache = "", "", ""
            if lat_v and lon_v:
                # Recortar antes del lookup: maneja coords fuera de rango (Excel sin decimal)
                lat_geo = normalizar_coord_geografica(lat_v, tipo="lat") or lat_v
                lon_geo = normalizar_coord_geografica(lon_v, tipo="lon") or lon_v
                key_coord = (round(lat_geo, 6), round(lon_geo, 6))
                try:
                    prov_cache, ciudad_cache, calle_cache = _geocode_cache.get(key_coord, ("", "", ""))
                except Exception:
                    prov_cache, ciudad_cache, calle_cache = "", "", ""
            # `provincia_punto` es la clave interna en mayúsculas. Se pasa por
            # `provincia_display` para que la hoja de pendientes guarde el mismo
            # nombre que Horarios_Detalle: al reinsertar una visita, ese texto
            # viaja a la columna PROVINCIA y, sin esto, la misma provincia salía
            # dos veces en los filtros.
            provincia_final = provincia_display(
                p.get("provincia_punto") or p.get("provincia") or prov_cache or ""
            )
            ciudad_final = p.get("ciudad_punto") or p.get("ciudad") or ciudad_cache or ""
            calle_final = p.get("calle_punto") or p.get("calle") or calle_cache or ""
            frec_mes = lookup_frecuencia_mes(
                freq_idx,
                desc,
                lat_v if lat_v else None,
                lon_v if lon_v else None,
                explicit=p.get("frecuencia_mes"),
            )
            fila_pend = {
                "Descripción": desc,
                "Latitud": lat_v if lat_v != 0 else None,
                "Longitud": lon_v if lon_v != 0 else None,
                "Semana": semana_str,
                "Tiempo Servicio (min)": p.get("tiempo", 0),
                "Provincia": provincia_final,
                "Ciudad": ciudad_final,
                "Calle": calle_final,
                "Mercadista origen": p.get("mercadista_origen", ""),
                "Día origen": p.get("dia_origen", ""),
                "Motivo": p.get("motivo", ""),
            }
            if frec_mes is not None:
                fila_pend["Frecuencia mes"] = frec_mes
            pendientes_data.append(fila_pend)
        pendientes_df = pd.DataFrame(pendientes_data) if pendientes_data else pd.DataFrame(
            columns=[
                "Descripción", "Latitud", "Longitud", "Semana",
                "Tiempo Servicio (min)", "Provincia", "Ciudad", "Calle",
                "Frecuencia mes",
                "Mercadista origen", "Día origen", "Motivo",
            ]
        )
        # Garantizar orden de columnas para Pendientes_Sin_Asignar
        pend_col_order = [
            "Descripción", "Latitud", "Longitud", "Semana",
            "Tiempo Servicio (min)", "Provincia", "Ciudad", "Calle",
            "Frecuencia mes", "Mercadista origen", "Día origen", "Motivo",
        ]
        pend_cols_present = [c for c in pend_col_order if c in pendientes_df.columns] + [
            c for c in pendientes_df.columns if c not in pend_col_order
        ]
        pendientes_df = pendientes_df[pend_cols_present]
        pendientes_df.to_excel(writer, sheet_name="Pendientes_Sin_Asignar", index=False)
        if pendientes_data:
            print(
                f"      [!] {len(pendientes_data)} visita(s) quedaron sin asignar "
                "(ver hoja 'Pendientes_Sin_Asignar').",
                file=__import__("sys").stderr,
                flush=True,
            )

        # Hoja Puntos_Sin_Coordenadas: puntos con lat/lon = 0 o coordenadas invalidas
        print("      -> Escribiendo hoja 'Puntos_Sin_Coordenadas'...")
        sin_coord_lista = getattr(state, "puntos_sin_coordenadas", [])
        sin_coord_data = []
        seen_sin_coord: set = set()
        freq_idx_sc = index_frecuencia_mes_por_punto(df) if df is not None else {}
        for p in sin_coord_lista:
            desc_sc = str(p.get("descripcion", "")).strip()
            lat_sc = p.get("lat", 0)
            lon_sc = p.get("lon", 0)
            key_sc = (desc_sc, lat_sc, lon_sc)
            if key_sc in seen_sin_coord:
                continue
            seen_sin_coord.add(key_sc)
            frec_sc = lookup_frecuencia_mes(
                freq_idx_sc, desc_sc, lat_sc, lon_sc,
                explicit=p.get("frecuencia_mes"),
            )
            sin_coord_data.append({
                "Descripción": desc_sc,
                "Latitud": lat_sc,
                "Longitud": lon_sc,
                "Semana": "ninguna",
                "Tiempo Servicio (min)": p.get("tiempo", 0),
                "Provincia": "ninguna",
                "Ciudad": "ninguna",
                "Frecuencia mes": frec_sc if frec_sc is not None else "",
                "Mercadista origen": "ninguno",
                "Día origen": "ninguno",
                "Motivo": p.get("motivo", "coordenadas_cero"),
            })
        sin_coord_df = pd.DataFrame(sin_coord_data) if sin_coord_data else pd.DataFrame(
            columns=[
                "Descripción", "Latitud", "Longitud", "Semana",
                "Tiempo Servicio (min)", "Provincia", "Ciudad",
                "Frecuencia mes", "Mercadista origen", "Día origen", "Motivo",
            ]
        )
        sin_coord_df.to_excel(writer, sheet_name="Puntos_Sin_Coordenadas", index=False)
        if sin_coord_data:
            print(
                f"      [!] {len(sin_coord_data)} punto(s) sin coordenadas válidas "
                "(ver hoja 'Puntos_Sin_Coordenadas').",
                file=__import__("sys").stderr,
                flush=True,
            )

        # Hoja Config_Procesamiento: con qué modelo de jornada se generó este
        # archivo. Sin ella, el dashboard mediría la ocupación con el modelo
        # activo en el servidor, que puede no ser el de este Excel.
        print(f"      -> Escribiendo hoja '{SHEET_CONFIG_PROCESAMIENTO}'...")
        build_config_procesamiento_df(
            incluye_viaje=jornada_incluye_viaje(),
            tipo_ruta=getattr(state, "tipo_ruta", ""),
            tipo_carga=getattr(state, "tipo_carga", "zona"),
            canal=getattr(state, "canal", ""),
            cadenas=getattr(state, "cadenas", None),
            grupos_cadenas=getattr(state, "grupos_cadenas", None),
            max_dia_minutos=cuota_dia(),
            max_mes_minutos=cuota_mes(),
            cuadrilla_fin_semana=len(mercadistas_fin_semana()),
            hora_fin_minutos=hora_fin_jornada(),
        ).to_excel(writer, sheet_name=SHEET_CONFIG_PROCESAMIENTO, index=False)

    return horarios_df
