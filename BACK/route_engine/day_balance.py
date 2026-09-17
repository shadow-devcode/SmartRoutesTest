"""
Elección de los días fijos y de las semanas equilibrando la carga de cada
mercaderista, en vez de por el número de fila del Excel.

El problema medido
------------------
Las frecuencias 8/12/16/20 exigen días concretos de la semana, y son el 72% de
las visitas (3.472 de 4.817 en el archivo real). Hoy el patrón lo elige
`dias_fijos_por_frecuencia(frecuencia, idx_seed=idx)`: rota según el número de
FILA del Excel. Es estable, pero ciega a quién atiende el punto, así que a un
mismo mercaderista le pueden tocar cinco puntos que exigen todos "Martes y
Jueves".

Resultado sobre el archivo real, antes de tocar nada:
  - 58 días quedaban por encima de 480 min SOLO con visitas de día fijo
  - desbalance medio entre el día más cargado y el más vacío: 323 min
  - caso extremo: [Lun 120, Mar 1080, Mié 0, Jue 660, Vie 540]

Esas visitas obligatorias que no caben se rechazan y acaban en
'Pendientes_Sin_Asignar', mientras los días vacíos del mismo mercaderista no se
pueden aprovechar: las visitas de otros puntos tienen su propio día fijo.

Qué hace
--------
Con el reparto de puntos ya decidido, recorre los puntos de día fijo de mayor a
menor carga y le da a cada uno el patrón —de entre los alternativos legítimos de
su frecuencia— que deje la semana del dueño más equilibrada.

Qué NO cambia
-------------
- La frecuencia mensual ni el número de visitas por semana: un punto de
  frecuencia 12 sigue teniendo 3 visitas por semana, solo que quizá en
  martes/jueves/viernes en vez de lunes/miércoles/viernes.
- El número de días por semana de cada frecuencia: 8 -> 2 días, 12 -> 3,
  16 -> 4, 20 -> los cinco. Lo que sí cambió es CUÁLES: antes se elegía entre
  cinco combinaciones fijas y sesgadas —cuatro de las cinco de frecuencia 8
  incluían el lunes, y las cinco de frecuencia 12 también—, y ahora se elige
  entre todas las posibles. Medido sobre el rutero nacional, esa sola
  ampliación bajó el plan de 67 a 60 mercaderistas (ocupación media del 77,7%
  al 86,8%): con los patrones sesgados el lunes de cada persona se llenaba
  antes que el resto y bloqueaba puntos que tenían sitio de sobra el jueves.
- La frecuencia 20 (los cinco días) no tiene alternativa y no se toca.
"""
from __future__ import annotations

import os
from route_engine.geo import km_por_carretera

from collections import defaultdict
from itertools import combinations

from route_engine.config import (
    DAY_NAMES,
    dia_equivalente_fin_semana,
    dias_de_mercadista,
    es_mercadista_fin_semana,
)
from route_engine.frequency import dias_fijos_por_frecuencia

NUM_SEMANAS = 4

# Días por semana que exige cada frecuencia mensual (4 semanas).
_DIAS_POR_SEMANA = {
    8: 2,
    12: 3,
    16: 4,
    20: 5,
}


def patrones_de_frecuencia(frecuencia) -> list[list[str]]:
    """Todas las combinaciones de días laborables válidas para esa frecuencia.

    Un punto de frecuencia 8 necesita dos días de la semana: cualquiera de las
    diez parejas posibles sirve, no solo las cinco que rotaba
    `dias_fijos_por_frecuencia`. Aquellas cinco estaban sesgadas —cuatro
    incluían el lunes— y con ellas el lunes de cada mercaderista se saturaba
    mientras el jueves quedaba libre, así que el planificador rechazaba puntos
    que en realidad cabían.

    El orden importa: se devuelven empezando por los patrones que reparten los
    días de forma más separada, que es lo que prefiere quien solo mira el
    primero.
    """
    try:
        frecuencia_int = int(frecuencia or 0)
    except (TypeError, ValueError):
        return []

    n = _DIAS_POR_SEMANA.get(frecuencia_int, 0)
    if not n:
        dias = dias_fijos_por_frecuencia(frecuencia, idx_seed=0)
        return [dias] if dias else []

    laborables = list(DAY_NAMES[:5])
    if n >= len(laborables):
        return [list(laborables)]

    def separacion(patron):
        # Cuanto más repartidos por la semana, mejor: la distancia mínima entre
        # dos visitas del mismo punto es lo que hace útil la frecuencia.
        indices = sorted(laborables.index(d) for d in patron)
        huecos = [b - a for a, b in zip(indices, indices[1:])]
        return (-min(huecos) if huecos else 0, indices)

    return [list(p) for p in sorted(combinations(laborables, n), key=separacion)]


# Al elegir los días de un punto se prefieren aquellos donde el mercaderista ya
# visita vecinos: es lo que hace un planificador humano («lunes y jueves al
# valle, martes y viernes al sur») y evita jornadas que cruzan la ciudad. Dos
# patrones cuya lejanía difiera menos que este tramo se consideran iguales y
# decide el equilibrio de carga. En 0 la cercanía no cuenta (como antes).
#
# Por defecto solo actúa cuando el desplazamiento consume jornada. Medido: ahí
# recorta los kilómetros un 27% en Pichincha y un 16% en el nacional con la
# misma cobertura; sin desplazamiento, en cambio, el nacional perdía 316
# visitas. AGRUPAR_DIAS_POR_CERCANIA_KM lo fuerza en cualquier modo.


def _tramo_agrupar() -> float:
    manual = os.environ.get("AGRUPAR_DIAS_POR_CERCANIA_KM")
    if manual not in (None, ""):
        try:
            return float(manual)
        except ValueError:
            pass
    from route_engine.config import jornada_incluye_viaje

    return 2.0 if jornada_incluye_viaje() else 0.0


# Lo que «cuesta» estrenar un día vacío: menos que ir lejos, más que ir al lado.
DIA_VACIO_EQUIVALE_KM = float(os.environ.get("DIA_VACIO_EQUIVALE_KM", "4"))


def _fraccion_holgada() -> float:
    """Fracción del tope a partir de la cual un día se considera apretado.

    Hay que dejar sitio a las visitas sin día fijo y, si el viaje consume
    jornada, también al desplazamiento. DIA_HOLGADO_FRACCION lo fija a mano.
    """
    manual = os.environ.get("DIA_HOLGADO_FRACCION")
    if manual:
        try:
            return float(manual)
        except ValueError:
            pass
    from route_engine.config import jornada_incluye_viaje

    return 0.8 if jornada_incluye_viaje() else 1.0


def _lejania(coords_dias: dict | None, patron: list[str], punto) -> float:
    """Kilómetros del punto a su vecino más cercano en cada día del patrón."""
    if not coords_dias or punto is None:
        return 0.0
    total = 0.0
    for d in patron:
        vecinos = coords_dias.get(d)
        if not vecinos:
            total += DIA_VACIO_EQUIVALE_KM
            continue
        total += min(km_por_carretera(punto[0], punto[1], la, lo) for la, lo in vecinos)
    return total


def _coste(
    carga_dias: dict, patron: list[str], minutos: float, tope: float, dias: list[str] | None = None,
    coords_dias: dict | None = None, punto=None,
) -> tuple:
    """
    Cuánto empeora la semana del mercaderista al meter este punto en `patron`.

    Por orden: no pasarse del tope diario (lo que de verdad manda visitas a
    pendientes), no apretar los días, juntar el punto con sus vecinos y, solo
    al final, dejar la semana pareja.
    """
    simulada = dict(carga_dias)
    for d in patron:
        simulada[d] = simulada.get(d, 0.0) + minutos
    exceso = sum(max(0.0, v - tope) for v in simulada.values())
    vals = [simulada.get(d, 0.0) for d in (dias or DAY_NAMES)]
    equilibrio = (max(vals) - min(vals), sum(v * v for v in vals))
    tramo_km = _tramo_agrupar()
    if tramo_km <= 0:
        return (exceso, *equilibrio)
    apretado = sum(max(0.0, simulada[d] - tope * _fraccion_holgada()) for d in patron)
    tramo = round(_lejania(coords_dias, patron, punto) / tramo_km)
    return (exceso, round(apretado / 30.0), tramo, *equilibrio)


def equilibrar_dias_fijos(visit_instances, propiedad, tope_dia: float) -> dict:
    """Reasigna `required_day` de los puntos de día fijo equilibrando por dueño."""
    if not propiedad:
        return {"puntos_movidos": 0, "mercaderistas": 0}

    por_punto: dict = defaultdict(list)
    for inst in visit_instances:
        if not inst.get("fixed_mercadista"):
            continue
        pk = inst.get("punto_key", inst.get("idx"))
        if pk is not None:
            por_punto[pk].append(inst)

    # Carga diaria de una semana tipo (todas las semanas son iguales para los
    # puntos de día fijo, así que basta equilibrar una).
    # La semana tipo de cada dueño, con SUS cinco días (la cuadrilla de fin de
    # semana tiene otros, no lunes-viernes).
    carga: dict = {}
    # Dónde está ya cada día de cada dueño, para juntar vecinos.
    coords: dict = {}

    def _carga_de(merc):
        if merc not in carga:
            carga[merc] = {d: 0.0 for d in dias_de_mercadista(merc)}
        return carga[merc]

    def _coords_de(merc):
        return coords.setdefault(merc, defaultdict(list))

    def _punto_de(insts):
        try:
            return (float(insts[0]["lat"]), float(insts[0]["lon"]))
        except (TypeError, ValueError, KeyError):
            return None

    puntos = sorted(
        por_punto.items(),
        key=lambda kv: -(float(kv[1][0].get("tiempo") or 0) * len(kv[1])),
    )

    movidos = 0
    for pk, insts in puntos:
        merc = propiedad.get(pk)
        if not merc:
            continue
        frecuencia = insts[0].get("frecuencia_mes")
        minutos = float(insts[0].get("tiempo") or 0)
        opciones = patrones_de_frecuencia(frecuencia)
        punto = _punto_de(insts)
        if len(opciones) <= 1:
            propia = _carga_de(merc)
            for d in {i.get("required_day") for i in insts if i.get("required_day")}:
                propia[d] = propia.get(d, 0.0) + minutos
                if punto is not None:
                    _coords_de(merc)[d].append(punto)
            continue

        dias_merc = dias_de_mercadista(merc)
        if es_mercadista_fin_semana(merc):
            # Los patrones están escritos en lunes-viernes: se corren al mismo
            # hueco de la jornada de fin de semana.
            opciones = [[dia_equivalente_fin_semana(d) for d in p] for p in opciones]
        mejor = min(
            opciones,
            key=lambda p: _coste(
                _carga_de(merc), p, minutos, tope_dia, dias_merc, _coords_de(merc), punto
            ),
        )
        actual = sorted({i.get("required_day") for i in insts if i.get("required_day")})
        if sorted(mejor) != actual:
            movidos += 1

        # Reescribir los días: cada semana repite el mismo patrón.
        por_semana: dict = defaultdict(list)
        for inst in insts:
            por_semana[int(inst.get("semana_num") or 1)].append(inst)
        for semana, lista in por_semana.items():
            for inst, dia in zip(lista, mejor):
                inst["required_day"] = dia
        propia = _carga_de(merc)
        for d in mejor:
            propia[d] = propia.get(d, 0.0) + minutos
            if punto is not None:
                _coords_de(merc)[d].append(punto)

    return {"puntos_movidos": movidos, "mercaderistas": len(carga)}
