"""
Reubicación de puntos y disolución de mercaderistas infrautilizados.

Mueve puntos enteros entre mercaderistas —la regla de un punto, un
mercaderista no se relaja— y vacía a quien queda por debajo del umbral de
ocupación repartiendo su cartera entre vecinos con hueco. Ambos pases tocan el
calendario ya escrito, así que retiran las filas antes de reasignar.
"""
from __future__ import annotations

from collections import defaultdict

from route_engine.config import (
    DAY_NAMES,
    RADIO_RESCATE_AMPLIADO_KM,
    carga_jornada,
    cuota_mes,
    dias_de_mercadista,
    max_dia_flex,
    mercadistas_fin_semana,
)
from route_engine.geo import haversine_km
from route_engine.mapbox import norm_provincia
from route_engine.scheduling import clave_punto
from route_engine.rescate_utiles import (
    _cabe_en_la_zona,
    _candidatos_cercanos,
    _coords_por_mercadista,
    _estado_dias_existente,
    _misma_provincia,
    _provincia_por_mercadista,
    _puede_absorber,
    _semana_de,
    _visitas_por_semana,
)


def reasignar_puntos_varados(state):
    """
    Traslada a otro mercadista los puntos cuyo dueño no puede atender todas sus
    visitas.

    El problema que resuelve: el primer mercadista que toca un punto se queda
    con él para todo el mes. Si después no consigue colocar todas sus visitas,
    esas visitas quedan varadas sin salida posible —su dueño no tiene hueco y
    ningún otro puede tocarlas, porque un punto lo atiende siempre la misma
    persona—. Se veía en el log: añadir 27 mercadistas de refuerzo dejaba el
    faltante en exactamente los mismos minutos, porque los recién llegados no
    encontraban ni un punto libre que adoptar.

    El traslado mueve el punto ENTERO: se retiran también las visitas que ya
    estaban agendadas y se vuelven a colocar bajo el nuevo dueño. Mover un punto
    completo respeta la regla de negocio; lo que la rompería es partirlo.

    Solo se traslada si el destino puede absorber TODAS las visitas del punto en
    jornadas que sigan por debajo de 480 min combinados; si no, el punto se
    queda donde está y sus visitas seguirán apareciendo como pendientes, que es
    información honesta.
    """
    varados = defaultdict(list)
    for zona, insts in state.remaining_by_prov.items():
        for inst in insts:
            pk = state.get_punto_key(inst)
            if pk in state.punto_mercadista:
                varados[pk].append((zona, inst))
    if not varados:
        return 0

    # Todas las instancias de cada punto, para poder recolocarlo completo.
    insts_por_punto = defaultdict(list)
    for inst in state.visit_instances:
        insts_por_punto[state.get_punto_key(inst)].append(inst)

    mercs_por_zona = defaultdict(list)
    for mn, pa in state.mercadistas_plan:
        if pa:
            mercs_por_zona[norm_provincia(pa)].append(mn)

    estado_dias = _estado_dias_existente(state)
    coords_merc = _coords_por_mercadista(state)
    prov_merc = _provincia_por_mercadista(state)
    movidos = 0

    # Los puntos más cargados primero: son los que más difícil tienen encontrar
    # acomodo y los que más capacidad liberan en su dueño actual.
    def carga_punto(pk):
        return sum(float(i.get("tiempo") or 0) for i in insts_por_punto.get(pk, ()))

    for pk in sorted(varados, key=carga_punto, reverse=True):
        instancias = insts_por_punto.get(pk)
        if not instancias:
            continue
        dueno = state.punto_mercadista.get(pk)
        zona = state.zona_de(instancias[0])
        candidatos = _candidatos_cercanos(
            state, mercs_por_zona, zona, coords_merc, instancias[0], excluir=dueno,
            prov_merc=prov_merc,
        )
        if not candidatos:
            continue

        # Mismo criterio que la compactación: las frecuencias 1, 2 y 4 no traen
        # `semana_num`, y saltárselas dejaba fuera la mayoría de los puntos.
        visitas_por_semana = _visitas_por_semana(instancias)
        if not visitas_por_semana:
            continue

        # Del más cargado al menos cargado (best-fit): se prefiere encajar el
        # punto en alguien que ya trabaja y aún tiene sitio, antes que estrenar
        # un mercadista casi vacío. Repartir hacia los vacíos también coloca las
        # visitas, pero infla la flota y baja la ocupación media, que es
        # justamente lo que se quiere evitar.
        carga_actual = defaultdict(float)
        for (mn, _dia, _sem), st in estado_dias.items():
            carga_actual[mn] += st["servicio"] + st["travel"]
        candidatos.sort(key=lambda m: carga_actual[m], reverse=True)

        # Tope MENSUAL además del diario. `_puede_absorber` solo comprueba que
        # las visitas quepan en las jornadas libres; sin esto el traslado metía
        # el punto en alguien que ya rozaba sus 9.600 min y lo dejaba por encima
        # del mes contratado (dos mercaderistas al 105% y 107,5% en la salida
        # real). El mes es el límite duro del modelo.
        carga_mes_punto = sum(float(i.get("tiempo") or 0) for i in instancias)
        elegido = None
        for merc in candidatos:
            if carga_actual.get(merc, 0.0) + carga_mes_punto > cuota_mes():
                continue
            if _puede_absorber(estado_dias, merc, visitas_por_semana):
                elegido = merc
                break
        if elegido is None:
            continue

        # Se retira el punto entero del calendario y vuelve al pool de su zona
        # bajo el nuevo dueño, para que el rescate lo recoloque con las jornadas
        # reales del destino.
        _retirar_punto_del_calendario(state, instancias, pk, zona, elegido)
        movidos += 1

        estado_dias = _estado_dias_existente(state)
        coords_merc = _coords_por_mercadista(state)

    if movidos:
        print(
            f"      -> {movidos} punto(s) trasladados a un mercadista de su zona "
            f"con jornadas libres (su dueño no podía atenderlos completos)."
        )
    return movidos


def _retirar_punto_del_calendario(state, instancias, pk, zona, nuevo_merc):
    """Saca todas las visitas de un punto del calendario y del pool, y las
    devuelve al pool de su zona bajo `nuevo_merc` para que el rescate las
    recoloque con las jornadas reales del destino."""
    clave = clave_punto(instancias[0])
    state.all_day_summaries[:] = [
        s for s in state.all_day_summaries
        if (
            round(float(s.get("Latitud") or 0), 6),
            round(float(s.get("Longitud") or 0), 6),
            str(s.get("Descripción", "")).strip(),
        ) != clave
    ]
    for lst in state.remaining_by_prov.values():
        lst[:] = [i for i in lst if state.get_punto_key(i) != pk]
    state.remaining_by_prov.setdefault(zona, []).extend(dict(i) for i in instancias)
    state.asignar_punto(pk, nuevo_merc)


# Reserva de desplazamiento al evaluar una disolución. Es mayor que la del
# reparto normal a propósito: aquí el punto llega a un mercadista cuya ruta ya
# está armada, así que se engancha al final de la jornada y el trayecto real
# suele salir más caro que la media. Con la reserva optimista se aprobaban
# traslados que después el rescate no podía materializar, y las visitas
# terminaban en pendientes habiendo desmontado ya la ruta de origen.
MARGEN_VIAJE_DISOLUCION_MIN = 30


def disolver_mercadistas_infrautilizados(state, umbral_ocupacion=0.5, minimo_activos=None):
    """
    Reparte los puntos de los mercadistas con poca carga entre sus vecinos de
    zona, para que dejen de consumir una plaza entera.

    El dimensionado de flota redondea hacia arriba por zona, y el reparto deja
    colas: mercadistas con un 5-20% de jornada que ocupan una plaza completa.
    Como un punto lo atiende siempre la misma persona, la única forma legítima
    de recuperarlos es mover sus puntos ENTEROS a otro mercadista de la zona.

    Es todo o nada por mercadista: si alguno de sus puntos no encuentra destino
    con hueco suficiente, no se mueve ninguno. Vaciarlo a medias no ahorraría la
    plaza y además desordenaría rutas que ya estaban bien.
    """
    estado_dias = _estado_dias_existente(state)
    coords_merc = _coords_por_mercadista(state)
    prov_merc = _provincia_por_mercadista(state)

    carga = defaultdict(float)
    for (mn, _d, _s), st in estado_dias.items():
        carga[mn] += st["servicio"] + st["travel"]

    insts_por_punto = defaultdict(list)
    for inst in state.visit_instances:
        insts_por_punto[state.get_punto_key(inst)].append(inst)

    mercs_por_zona = defaultdict(list)
    for mn, pa in state.mercadistas_plan:
        if pa:
            mercs_por_zona[norm_provincia(pa)].append(mn)

    limite = cuota_mes() * umbral_ocupacion
    # La compactación no mezcla calendarios: una plaza floja de fin de semana se
    # funde con otra de fin de semana, y una de lunes a viernes con las suyas.
    # Repartir puntos de la cuadrilla entre vecinos de lunes a viernes los
    # devolvería a pendientes, que es exactamente de donde vinieron.
    fin_semana = mercadistas_fin_semana()
    flojos = sorted((m for m in carga if 0 < carga[m] < limite), key=lambda m: carga[m])
    disueltos = 0

    # Suelo de plantilla. Compactar es bueno hasta que la flota deja de tener
    # capacidad para la demanda: por debajo de ceil(demanda / 9600) no hay
    # reparto posible y el trabajo sobrante se convierte en pendientes. Medido:
    # sin este suelo la flota bajaba a 74 para una demanda que exige 78, y la
    # cobertura caía del 95% al 89% aunque la ocupación media subiera.
    activos = sum(1 for m in carga if carga[m] > 0)

    for flojo in flojos:
        if minimo_activos is not None and activos - disueltos <= int(minimo_activos):
            print(
                f"      -> Compactación detenida: {activos - disueltos} mercadista(s) "
                f"es el mínimo que admite la carga total"
            )
            break
        puntos = [pk for pk, m in state.punto_mercadista.items() if m == flojo]
        if not puntos:
            continue

        # Simulación: se buscan destinos para TODOS sus puntos sobre una copia
        # del estado de jornadas, y solo si salen todos se aplica de verdad.
        simulado = {k: dict(v) for k, v in estado_dias.items()}
        carga_simulada = dict(carga)
        # Partición reservada durante la simulación: si en este mismo traslado
        # un destino ya recibió un punto de CORAL, deja de ser candidato para
        # uno de ROSADO aunque el estado real todavía no lo refleje.
        grupo_simulado = dict(getattr(state, "grupo_de_merc", {}))
        plan_traslado = []
        viable = True
        for pk in sorted(puntos, key=lambda p: -sum(float(i.get("tiempo") or 0) for i in insts_por_punto.get(p, ()))):
            instancias = insts_por_punto.get(pk)
            if not instancias:
                continue
            zona = state.zona_de(instancias[0])
            por_semana = _visitas_por_semana(instancias)
            if not por_semana:
                viable = False
                break

            # Alcance ampliado: si el punto se queda donde está, la plaza floja
            # sigue abierta. El vecino más cercano puede estar a más de 60 km
            # justamente porque la zona está aislada, que es la causa de que la
            # plaza esté floja.
            candidatos = _candidatos_cercanos(
                state, mercs_por_zona, zona, coords_merc, instancias[0], excluir=flojo,
                radio_km=RADIO_RESCATE_AMPLIADO_KM, prov_merc=prov_merc,
            )
            candidatos.sort(key=lambda m: carga[m], reverse=True)
            carga_punto_mes = sum(float(i.get("tiempo") or 0) for i in instancias)
            destino = None
            grupo_punto = state.grupo_de_punto.get(pk) if hasattr(state, "grupo_de_punto") else None
            for merc in candidatos:
                # Mismo calendario que el mercaderista que se está vaciando.
                if (merc in fin_semana) != (flojo in fin_semana):
                    continue
                # Tope MENSUAL. `_puede_absorber` solo mira si las visitas caben
                # en las jornadas libres; sin esta comprobación la compactación
                # empujaba a dos mercaderistas al 105% y 107,5% del mes
                # contratado, que es la única regla que no se puede negociar.
                if carga_simulada.get(merc, 0.0) + carga_punto_mes > cuota_mes():
                    continue
                if (
                    grupo_punto is not None
                    and grupo_simulado.get(merc) is not None
                    and grupo_simulado.get(merc) != grupo_punto
                ):
                    continue
                if _puede_absorber(
                    simulado, merc, por_semana, margen_viaje=MARGEN_VIAJE_DISOLUCION_MIN
                ):
                    destino = merc
                    break
            if destino is None:
                viable = False
                break

            carga_simulada[destino] = carga_simulada.get(destino, 0.0) + carga_punto_mes
            if grupo_punto is not None:
                grupo_simulado.setdefault(destino, grupo_punto)

            # Reservar el hueco en la simulación: los días más libres primero,
            # que es como los repartirá `_puede_absorber` en la siguiente vuelta.
            for semana, tiempos in por_semana.items():
                libres = sorted(
                    range(len(DAY_NAMES)),
                    key=lambda d: (
                        simulado.get((destino, d, semana), {"servicio": 0.0, "travel": 0.0})["servicio"]
                        + simulado.get((destino, d, semana), {"servicio": 0.0, "travel": 0.0})["travel"]
                    ),
                )
                for t, dia_idx in zip(sorted(tiempos, reverse=True), libres):
                    st = simulado.setdefault(
                        (destino, dia_idx, semana),
                        {"servicio": 0.0, "travel": 0.0, "last_lat": None, "last_lon": None},
                    )
                    st["servicio"] += t
                    st["travel"] += MARGEN_VIAJE_DISOLUCION_MIN
            plan_traslado.append((pk, instancias, zona, destino))

        if not viable or not plan_traslado:
            continue

        for pk, instancias, zona, destino in plan_traslado:
            _retirar_punto_del_calendario(state, instancias, pk, zona, destino)
        disueltos += 1
        estado_dias = _estado_dias_existente(state)
        coords_merc = _coords_por_mercadista(state)
        carga = defaultdict(float)
        for (mn, _d, _s), st in estado_dias.items():
            carga[mn] += st["servicio"] + st["travel"]

    if disueltos:
        print(
            f"      -> {disueltos} mercadista(s) infrautilizados disueltos: sus puntos "
            f"pasaron enteros a compañeros de su misma zona."
        )
    return disueltos
