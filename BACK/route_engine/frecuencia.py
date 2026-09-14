"""
Reconciliación de frecuencia: agendadas + pendientes == FRECUENCIA MES.

Es una igualdad, no un mínimo, y se comprueba en los dos sentidos. Si faltan
visitas se añaden a pendientes; si sobran —porque una fase tardía colocó lo que
otra dio por perdido— se retiran. El cruce se hace por (punto, tiempo de
servicio) con el tiempo redondeado igual que en el resto del motor.
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

import pandas as pd

from route_engine.excel_reader import (
    clave_punto_frecuencia,
    columna_frecuencia_mes,
    minutos_normalizados,
    parse_coordenada_a_float,
    _normalizar_columnas_clave,
)
from route_engine.mapbox import provincia_display


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
