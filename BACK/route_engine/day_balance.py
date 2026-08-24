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
- Los patrones son los mismos que ya definía `frequency.py`; no se inventan
  combinaciones nuevas.
- La frecuencia 20 (los cinco días) no tiene alternativa y no se toca.
"""
from __future__ import annotations

from collections import defaultdict

from route_engine.config import (
    DAY_NAMES,
    dia_equivalente_fin_semana,
    dias_de_mercadista,
    es_mercadista_fin_semana,
)
from route_engine.frequency import dias_fijos_por_frecuencia

NUM_SEMANAS = 4

# Mismos patrones que `frequency.dias_fijos_por_frecuencia`, expuestos como
# alternativas entre las que elegir.
_ALTERNATIVAS = {
    8: 5,
    12: 5,
    16: 5,
}


def patrones_de_frecuencia(frecuencia) -> list[list[str]]:
    """Todas las alternativas de días para esa frecuencia, sin repetir."""
    n = _ALTERNATIVAS.get(int(frecuencia or 0), 0)
    if not n:
        dias = dias_fijos_por_frecuencia(frecuencia, idx_seed=0)
        return [dias] if dias else []
    vistos, salida = set(), []
    for seed in range(n):
        dias = dias_fijos_por_frecuencia(frecuencia, idx_seed=seed)
        clave = tuple(dias)
        if dias and clave not in vistos:
            vistos.add(clave)
            salida.append(dias)
    return salida


def _coste(
    carga_dias: dict, patron: list[str], minutos: float, tope: float, dias: list[str] | None = None
) -> tuple:
    """
    Cuánto empeora la semana del mercaderista al meter este punto en `patron`.

    Prima primero no pasarse del tope diario (lo que de verdad manda visitas a
    pendientes) y, en segundo lugar, dejar la semana pareja.
    """
    simulada = dict(carga_dias)
    for d in patron:
        simulada[d] = simulada.get(d, 0.0) + minutos
    exceso = sum(max(0.0, v - tope) for v in simulada.values())
    vals = [simulada.get(d, 0.0) for d in (dias or DAY_NAMES)]
    return (exceso, max(vals) - min(vals), sum(v * v for v in vals))


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

    def _carga_de(merc):
        if merc not in carga:
            carga[merc] = {d: 0.0 for d in dias_de_mercadista(merc)}
        return carga[merc]

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
        if len(opciones) <= 1:
            propia = _carga_de(merc)
            for d in {i.get("required_day") for i in insts if i.get("required_day")}:
                propia[d] = propia.get(d, 0.0) + minutos
            continue

        dias_merc = dias_de_mercadista(merc)
        if es_mercadista_fin_semana(merc):
            # Los patrones están escritos en lunes-viernes: se corren al mismo
            # hueco de la jornada de fin de semana.
            opciones = [[dia_equivalente_fin_semana(d) for d in p] for p in opciones]
        mejor = min(
            opciones, key=lambda p: _coste(_carga_de(merc), p, minutos, tope_dia, dias_merc)
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

    return {"puntos_movidos": movidos, "mercaderistas": len(carga)}
