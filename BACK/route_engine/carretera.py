"""
Desplazamiento real por carretera de cada jornada.

El motor planifica con distancia estimada porque comparar decenas de miles de
combinaciones consultando la red sería inviable. Aquí, con las rutas ya
cerradas, cada jornada se recalcula con la API de Directions —la misma que
dibuja la «Vista Carretera» del mapa— y se retiran las visitas que, medidas de
verdad, ya no caben en el reloj del día.
"""
from __future__ import annotations

import pandas as pd

from route_engine.config import (
    DISTANCE_FACTOR_CARRETERA,
    HORA_INICIO_JORNADA,
    hora_fin_jornada,
    jornada_incluye_viaje,
    tope_jornada_real,
)
from route_engine.geo import haversine_km, minutos_viaje_desde_km, parse_coordenada_a_float
from route_engine.mapbox import ruta_por_carretera


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

    Devuelve (jornadas_actualizadas, jornadas_totales, jornadas_que_se_pasan,
    filas_retiradas).
    """
    columnas = {"Mercadista", "Día", "Fecha", "Latitud", "Longitud", "Orden Ruta"}
    if horarios_df.empty or not columnas.issubset(set(horarios_df.columns)):
        return 0, 0

    col_km = "kilometros entre sucurlas (km)"
    col_viaje = "Tiempo entre sucursal (min)"
    # Cuántas visitas tiene cada punto en todo el mes: es su frecuencia real y
    # sirve para decidir a quién se retira cuando un día no cabe.
    frecuencia = _frecuencia_por_punto(horarios_df)
    actualizadas = 0
    tarde = 0
    retiradas: list = []
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
        # planificado. Las visitas que ya no caben en el reloj del día se
        # retiran —las últimas de la ruta, que son las que sobran— y vuelven a
        # pendientes. Dejarlas escritas sería entregar un plan que no se puede
        # cumplir; recortar solo la hora sería mentir sobre él.
        # Retirar visitas por el reloj real solo tiene sentido si el modelo cuenta
        # el desplazamiento; si no, la carretera se informa pero no quita nada.
        sobrantes = (
            _visitas_que_no_caben_en_el_dia(horarios_df, indices, frecuencia)
            if jornada_incluye_viaje()
            else []
        )
        if sobrantes:
            retiradas.extend(sobrantes)
            indices = [i for i in indices if i not in set(sobrantes)]
            if len(indices) < 2:
                continue
        if _recomponer_horarios_del_dia(horarios_df, indices) > limite:
            tarde += 1

    # Antes de mandar a pendientes lo que no cupo, se intenta otro día de la
    # misma semana con el mismo mercaderista: es la salida más barata y la que
    # menos toca de lo ya armado.
    if retiradas:
        sin_sitio = _segunda_oportunidad(horarios_df, retiradas)
        recolocadas = len(retiradas) - len(sin_sitio)
        if recolocadas:
            print(
                f"      -> Segunda oportunidad: {recolocadas} de {len(retiradas)} visita(s) "
                f"retiradas recolocadas en otro día de su mercaderista."
            )
        retiradas = sin_sitio
    if retiradas:
        horarios_df.drop(index=retiradas, inplace=True)

    return actualizadas, len(grupos), tarde, retiradas


def _km_por_carretera(origen, destino):
    """Kilómetros reales entre dos paradas; si la red falla, recta por factor."""
    tramos = ruta_por_carretera([origen, destino])
    if tramos:
        return float(tramos[0][1])
    return haversine_km(origen[0], origen[1], destino[0], destino[1]) * DISTANCE_FACTOR_CARRETERA


def _segunda_oportunidad(horarios_df, retiradas):
    """Recoloca cada visita retirada en otro día de esa misma semana de su
    mercaderista donde quepa con el reloj real. Devuelve las que siguen sin sitio.

    Se prueban los días que ese mercaderista ya trabaja, nunca uno en el que ya
    visite el punto, y se elige el que más hueco deja. La visita se engancha al
    final de la ruta con su tramo real.
    """
    if not retiradas:
        return []
    col_km = "kilometros entre sucurlas (km)"
    col_viaje = "Tiempo entre sucursal (min)"
    col_serv = "Tiempo Servicio (min)"
    tope = tope_jornada_real()
    fuera = set(retiradas)
    dias_por_merc = {
        merc: list(dict.fromkeys(grupo["Día"]))
        for merc, grupo in horarios_df.groupby("Mercadista", sort=False)
    }

    def minutos(idx):
        total = 0.0
        for col in (col_serv, col_viaje):
            try:
                total += float(horarios_df.at[idx, col] or 0)
            except (TypeError, ValueError):
                pass
        return total

    sin_sitio = []
    for idx in retiradas:
        merc = horarios_df.at[idx, "Mercadista"]
        fecha = horarios_df.at[idx, "Fecha"]
        dia_origen = horarios_df.at[idx, "Día"]
        lat = parse_coordenada_a_float(horarios_df.at[idx, "Latitud"])
        lon = parse_coordenada_a_float(horarios_df.at[idx, "Longitud"])
        if lat is None or lon is None:
            sin_sitio.append(idx)
            continue
        try:
            servicio = float(horarios_df.at[idx, col_serv] or 0)
        except (TypeError, ValueError):
            servicio = 0.0
        clave = _clave_de_fila(horarios_df, idx)

        mejor = None
        for dia in dias_por_merc.get(merc, []):
            if dia == dia_origen:
                continue
            mascara = (
                (horarios_df["Mercadista"] == merc)
                & (horarios_df["Fecha"] == fecha)
                & (horarios_df["Día"] == dia)
            )
            indices = [i for i in horarios_df.index[mascara] if i not in fuera]
            if any(_clave_de_fila(horarios_df, i) == clave for i in indices):
                continue
            total = sum(minutos(i) for i in indices)
            if indices:
                ultimo = max(indices, key=lambda i: float(horarios_df.at[i, "Orden Ruta"] or 0))
                ulat = parse_coordenada_a_float(horarios_df.at[ultimo, "Latitud"])
                ulon = parse_coordenada_a_float(horarios_df.at[ultimo, "Longitud"])
                if ulat is None or ulon is None:
                    continue
                km = _km_por_carretera((ulat, ulon), (lat, lon))
                viaje = minutos_viaje_desde_km(km)
                orden = float(horarios_df.at[ultimo, "Orden Ruta"] or 0) + 1
            else:
                km, viaje, orden = 0.0, 0.0, 1
            if total + viaje + servicio > tope:
                continue
            hueco = tope - (total + viaje + servicio)
            if mejor is None or hueco > mejor[0]:
                mejor = (hueco, dia, km, viaje, orden, indices)

        if mejor is None:
            sin_sitio.append(idx)
            continue
        _hueco, dia, km, viaje, orden, indices = mejor
        horarios_df.at[idx, "Día"] = dia
        horarios_df.at[idx, "Orden Ruta"] = orden
        horarios_df.at[idx, col_km] = round(km, 2)
        horarios_df.at[idx, col_viaje] = round(viaje, 2)
        fuera.discard(idx)
        if not indices and "Horario" in horarios_df.columns:
            inicio = f"{HORA_INICIO_JORNADA // 60:02d}:{HORA_INICIO_JORNADA % 60:02d}"
            horarios_df.at[idx, "Horario"] = f"{inicio} - {inicio}"
        _recomponer_horarios_del_dia(horarios_df, indices + [idx])
    return sin_sitio


def _frecuencia_por_punto(horarios_df):
    """{(descripción, lat, lon): visitas del mes} a partir de la agenda."""
    if horarios_df.empty:
        return {}
    claves = [
        (str(d).strip(), round(float(la or 0), 6), round(float(lo or 0), 6))
        for d, la, lo in zip(
            horarios_df["Descripción"], horarios_df["Latitud"], horarios_df["Longitud"]
        )
    ]
    conteo: dict = {}
    for clave in claves:
        conteo[clave] = conteo.get(clave, 0) + 1
    return conteo


def _clave_de_fila(horarios_df, idx):
    try:
        return (
            str(horarios_df.at[idx, "Descripción"]).strip(),
            round(float(horarios_df.at[idx, "Latitud"] or 0), 6),
            round(float(horarios_df.at[idx, "Longitud"] or 0), 6),
        )
    except (TypeError, ValueError):
        return None


def _visitas_que_no_caben_en_el_dia(horarios_df, indices, frecuencia=None):
    """Índices de las visitas que sobran del día una vez medido de verdad.

    El reloj de la jornada es servicio más desplazamiento. Con la distancia
    estimada el día cuadraba; con la de carretera puede no caber. Se retira
    primero lo de MENOR frecuencia: perder una visita de un punto que se ve
    veinte veces al mes rompe su ritmo, y perder una de uno que se ve cuatro
    cuesta mucho menos. A igual frecuencia se quita la última de la ruta, que
    es la que menos trastoca lo ya encadenado.
    """
    from route_engine.config import tope_jornada_real

    tope = tope_jornada_real()
    total = 0.0
    for idx in indices:
        try:
            total += float(horarios_df.at[idx, "Tiempo Servicio (min)"] or 0)
            total += float(horarios_df.at[idx, "Tiempo entre sucursal (min)"] or 0)
        except (TypeError, ValueError):
            pass
    if total <= tope:
        return []

    frecuencia = frecuencia or {}
    orden_retiro = sorted(
        range(len(indices)),
        key=lambda pos: (frecuencia.get(_clave_de_fila(horarios_df, indices[pos]), 0), -pos),
    )
    sobrantes = []
    for pos in orden_retiro:
        idx = indices[pos]
        if total <= tope or len(indices) - len(sobrantes) <= 1:
            break
        try:
            total -= float(horarios_df.at[idx, "Tiempo Servicio (min)"] or 0)
            total -= float(horarios_df.at[idx, "Tiempo entre sucursal (min)"] or 0)
        except (TypeError, ValueError):
            pass
        sobrantes.append(idx)
    return sobrantes


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
