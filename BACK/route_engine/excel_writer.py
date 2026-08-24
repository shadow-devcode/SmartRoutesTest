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
from openpyxl.styles import Font, PatternFill
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
    parse_coordenada_a_float,
)
from route_engine.geo import normalizar_coord_geografica, haversine_km
from route_engine.mapbox import _geocode_cache, calcular_tiempo_entre
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
        tiempo = pd.to_numeric(row.get("TIEMPO DE SERVICIO", 0), errors="coerce")
        freq = pd.to_numeric(row.get(col_freq, 0) if col_freq else 0, errors="coerce")
        info[key].append({
            "descripcion": desc,
            "lat": lat_f,
            "lon": lon_f,
            "tiempo": float(tiempo) if pd.notna(tiempo) else 0.0,
            "frecuencia": int(freq) if pd.notna(freq) and int(freq) > 0 else 1,
            "provincia": str(row.get("PROVINCIA_COORD", "") or "").strip(),
        })
    return info


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
            t = pd.to_numeric(r.get("Tiempo Servicio (min)", 0), errors="coerce")
            t_val = float(t) if pd.notna(t) else 0.0
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
    for p in state.puntos_pendientes:
        key = clave_punto_frecuencia(p.get("descripcion"), p.get("lat"), p.get("lon"))
        if key[1] is None:
            continue
        sem = _semana_a_int(p.get("semana"))
        dedup_key = (key, sem, p.get("_dedup_token", ""))
        if dedup_key in vistas_pend:
            continue
        vistas_pend.add(dedup_key)

        t_val = float(p.get("tiempo", 0))
        pend_por_config[(key, t_val)] += 1

        if sem is not None:
            semanas_usadas[key].add(sem)

    añadidas = 0
    for key, configs in info.items():
        usadas = set(semanas_usadas.get(key, set()))
        for idx_conf, conf in enumerate(configs):
            t_val = conf["tiempo"]
            freq_esperada = conf["frecuencia"]
            ya_cubiertas = agendadas_por_config.get((key, t_val), 0) + pend_por_config.get((key, t_val), 0)
            deficit = freq_esperada - ya_cubiertas
            if deficit <= 0:
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

    if añadidas:
        import sys as _sys
        print(
            f"      [!] Reconciliación de frecuencia: {añadidas} visita(s) "
            "faltante(s) añadidas a 'Pendientes_Sin_Asignar' para cumplir "
            "(agendadas + pendientes = frecuencia mensual).",
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
    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio


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

    horarios_df = _postprocesar_horarios(horarios_df, visit_instances, state)
    horarios_df = drop_spurious_total_rows_horarios_df(horarios_df)

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
            provincia_final = (
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
            max_dia_minutos=cuota_dia(),
            max_mes_minutos=cuota_mes(),
            cuadrilla_fin_semana=len(mercadistas_fin_semana()),
            hora_fin_minutos=hora_fin_jornada(),
        ).to_excel(writer, sheet_name=SHEET_CONFIG_PROCESAMIENTO, index=False)

    return horarios_df
