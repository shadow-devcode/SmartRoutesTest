"""
Postproceso de las jornadas ya asignadas.

Ordena cada día por cercanía, recalcula horarios y tiempos, deduplica visitas
repetidas y aplica el tope combinado diario reubicando lo que no cabe en otro
día de la misma semana. Lo que no encuentra sitio vuelve a pendientes, de modo
que ninguna visita desaparece del recuento.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

import pandas as pd

from route_engine.config import (
    DAY_NAMES,
    carga_jornada,
    columnas_de_mercadista,
    cuota_dia,
    dias_de_mercadista,
    max_dia_flex,
    max_servicio_dia,
    max_servicio_overflow,
    cuota_mes,
    viaje_en_agenda,
)
from route_engine.excel_reader import index_frecuencia_mes_por_punto, lookup_frecuencia_mes
from route_engine.geo import haversine_km, parse_coordenada_a_float
from route_engine.mapbox import calcular_tiempo_entre
from route_engine.scheduling import calcular_horario_almuerzo, format_duracion, formato_horario


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


def _recalcular_ruta(grupo, incluye_viaje=None, respetar_orden=False):
    """
    Recalcula Tiempo entre sucursal, km y Horario para cada ruta
    (Mercadista, Dia, Fecha) en orden.
    Asignación por columnas completas (evita fallos silenciosos con iloc en algunos DataFrames).

    `incluye_viaje` fija el modelo de jornada; `None` usa el activo. Lo pasan
    los editores de rutas, que trabajan sobre un Excel ya generado y deben
    respetar el modelo de ESE archivo, no el que tenga el servidor por defecto.
    """
    # En una edición manual manda el orden que puso el usuario: reordenar por
    # cercanía dejaba el número de orden como él lo quería, pero las horas y
    # los tramos calculados para otra secuencia.
    if respetar_orden and "Orden Ruta" in grupo.columns:
        grupo = grupo.sort_values("Orden Ruta", kind="stable").copy()
    else:
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
    aplicar_almuerzo = total_serv != cuota_dia()

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
