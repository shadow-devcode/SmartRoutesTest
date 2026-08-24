"""
Pase de rescate y red de seguridad para visitas no asignadas.
"""

from collections import defaultdict

from route_engine.config import (
    DAY_NAMES,
    dias_de_mercadista,
    mercadistas_fin_semana,
    MAX_TRAVEL_MINUTES_EXTREME,
    NUM_SEMANAS_POR_MERCADISTA,
    RADIO_RESCATE_AMPLIADO_KM,
    carga_jornada,
    cuota_mes,
    max_dia_flex,
    max_servicio_dia,
    travel_estimado_por_visita_plan_min,
)
from route_engine.frequency import semanas_distribuidas, semanas_por_frecuencia
from route_engine.geo import estimar_minutos_viaje, haversine_km
from route_engine.mapbox import _geocode_cache, norm_provincia
from route_engine.scheduling import clave_punto, format_duracion


def _travel_desde(prev_lat, prev_lon, inst):
    """Estima desplazamiento (min) desde la última posición conocida al punto de `inst`.
    Devuelve 0.0 si no hay punto previo o si las coordenadas no son válidas.
    """
    if prev_lat is None or prev_lon is None:
        return 0.0
    try:
        lat = float(inst["lat"])
        lon = float(inst["lon"])
    except (TypeError, ValueError, KeyError):
        return 0.0
    try:
        return float(estimar_minutos_viaje(prev_lat, prev_lon, lat, lon))
    except Exception:
        return 0.0


def _coords_por_mercadista(state):
    """{mercadista: [(lat, lon), ...]} de lo ya agendado."""
    coords = defaultdict(set)
    for s in state.all_day_summaries:
        try:
            coords[s.get("Mercadista", "")].add(
                (round(float(s.get("Latitud", 0)), 4), round(float(s.get("Longitud", 0)), 4))
            )
        except (TypeError, ValueError):
            continue
    return {k: list(v) for k, v in coords.items()}


def _salto_admisible(prev_lat, prev_lon, inst):
    """
    ¿Es razonable el trayecto entre la visita anterior y esta?

    Se mide en KILÓMETROS y contra el diámetro de la zona, no en minutos. Topar
    los minutos de viaje deja un alcance de ~22 km, más corto que la propia zona
    de trabajo (60 km de diámetro): el rescate rechazaba entonces visitas
    perfectamente normales dentro del territorio del mercaderista y la cobertura
    caía del 94% al 86%. Lo que hay que impedir no es moverse dentro de la zona,
    sino saltar fuera de ella.
    """
    from route_engine.config import RADIO_ZONA_KM

    if prev_lat is None or prev_lon is None:
        return True
    try:
        return haversine_km(prev_lat, prev_lon, float(inst["lat"]), float(inst["lon"])) <= 2 * RADIO_ZONA_KM
    except (TypeError, ValueError, KeyError):
        return True


def _provincia_por_mercadista(state):
    """
    {mercadista: provincia donde tiene MÁS visitas}.

    Regla de negocio: un mercaderista trabaja su provincia y solo sale de ella
    para rellenar. Se calcula sobre lo ya agendado, que es lo que define de
    hecho su territorio.
    """
    conteo = defaultdict(lambda: defaultdict(int))
    for s in state.all_day_summaries:
        prov = norm_provincia(str(s.get("PROVINCIA", "") or ""))
        if prov:
            conteo[s.get("Mercadista", "")][prov] += 1
    return {
        merc: max(provs.items(), key=lambda kv: kv[1])[0]
        for merc, provs in conteo.items()
        if provs
    }


def _misma_provincia(prov_merc, inst):
    """¿La visita cae en la provincia del mercaderista?"""
    if not prov_merc:
        return True
    return norm_provincia(str(inst.get("provincia_punto", "") or "")) == prov_merc


def _cabe_en_la_zona(coords_merc, inst, radio_km=None):
    """
    ¿Está este punto dentro del radio de trabajo del mercadista?

    Invariante de seguridad para los pases de rescate: ninguna visita puede
    acabar a más de 2 × RADIO_ZONA_KM de los puntos que el mercadista ya
    atiende. Sin esta comprobación aparecían rutas con puntos a 297 km entre sí
    —un mercadista con una tienda en Quito y otras sueltas en Guayaquil,
    Naranjal y Los Ríos—, que es exactamente lo que la zonificación evita en la
    asignación principal. Es preferible dejar la visita pendiente y que se vea.
    """
    from route_engine.config import RADIO_ZONA_KM

    # Un mercaderista sin puntos todavía acepta el primero, que pasa a ser su
    # ancla. Lo que NO puede es seguir aceptando cualquier cosa: las plazas
    # recién abiertas empezaban vacías y, como este control las daba por buenas,
    # recogían puntos de todo el país. De ahí salían saltos de 485 km dentro de
    # una misma jornada.
    if not coords_merc:
        return True
    try:
        lat, lon = float(inst["lat"]), float(inst["lon"])
    except (TypeError, ValueError, KeyError):
        return True
    limite = 2 * float(radio_km if radio_km is not None else RADIO_ZONA_KM)
    return all(haversine_km(lat, lon, la, lo) <= limite for la, lo in coords_merc)


def _estado_dias_existente(state):
    """
    Carga ya agendada por (mercadista, índice de día, semana), en el formato de
    `bucket_state` del pase de rescate.

    La clave usa el índice del día (no su nombre) porque así es como el rescate
    identifica sus buckets. La última posición conocida es la de la visita con
    mayor Orden Ruta: es donde se engancharía una visita añadida al final.
    """
    estado = {}
    ordenes = {}
    for s in state.all_day_summaries:
        # El índice es la posición del día DENTRO de la semana de ese
        # mercaderista: la cuadrilla de fin de semana trabaja miércoles-domingo,
        # así que su día 0 es el miércoles, no el lunes.
        merc_s = s.get("Mercadista", "")
        dias_s = dias_de_mercadista(merc_s)
        dia = s.get("Día")
        if dia not in dias_s:
            continue
        semana_txt = str(s.get("Fecha", "")).strip()
        try:
            semana = int(semana_txt.split()[-1])
        except (ValueError, IndexError):
            continue
        key = (merc_s, dias_s.index(dia), semana)
        st = estado.setdefault(
            key, {"servicio": 0.0, "travel": 0.0, "last_lat": None, "last_lon": None}
        )
        try:
            st["servicio"] += float(s.get("Tiempo Servicio (min)", 0) or 0)
        except (TypeError, ValueError):
            pass
        try:
            st["travel"] += float(s.get("Tiempo entre sucursal (min)", 0) or 0)
        except (TypeError, ValueError):
            pass
        try:
            orden = int(s.get("Orden Ruta", 0) or 0)
            if orden >= ordenes.get(key, -1):
                ordenes[key] = orden
                st["last_lat"] = float(s.get("Latitud", 0))
                st["last_lon"] = float(s.get("Longitud", 0))
        except (TypeError, ValueError):
            pass
    return estado


def _semana_de(valor):
    """'semana 3' -> 3. None si no se reconoce."""
    try:
        return int(str(valor).strip().split()[-1])
    except (ValueError, IndexError, AttributeError):
        return None


def _visitas_por_semana(instancias):
    """
    {semana: [minutos, ...]} de todas las visitas de un punto.

    Las instancias de frecuencia 8/12/16/20 traen `semana_num`; las de
    frecuencia 1, 2 y 4 no, porque una sola instancia representa la visita de
    varias semanas. Leer solo `semana_num` dejaba fuera 335 de las 672 filas de
    la entrada real, y como el traslado es todo-o-nada por mercaderista, bastaba
    un punto de frecuencia 4 para abortarlo: por eso no se disolvía ni una sola
    plaza floja.
    """
    por_semana = defaultdict(list)
    for inst in instancias:
        tiempo = float(inst.get("tiempo") or 0)
        semana = inst.get("semana_num")
        if semana is not None:
            por_semana[semana].append(tiempo)
            continue
        for sem in semanas_distribuidas(
            inst.get("frecuencia_mes", 1), idx_seed=inst.get("idx", 0) or 0
        ):
            por_semana[sem].append(tiempo)
    return por_semana


def _puede_absorber(estado_dias, merc, visitas_por_semana, margen_viaje=None):
    """
    ¿Caben en las jornadas libres de `merc` todas las visitas de un punto?

    Un punto no se puede visitar dos veces el mismo día, así que cada visita de
    una semana necesita un día distinto. Se comprueba emparejando la visita más
    larga con el día que más hueco tiene (asignación óptima para este caso: como
    cada día admite como mucho una visita del punto, si el emparejamiento
    ordenado falla, ningún otro lo consigue).
    """
    for semana, tiempos in visitas_por_semana.items():
        huecos = []
        dia_vacio_disponible = False
        for dia_idx in range(len(DAY_NAMES)):
            st = estado_dias.get((merc, dia_idx, semana))
            usado = (st["servicio"] + st["travel"]) if st else 0.0
            if usado <= 0:
                dia_vacio_disponible = True
            huecos.append(max_dia_flex() - usado)
        huecos.sort(reverse=True)
        if len(tiempos) > len(huecos):
            return False
        reserva = (
            travel_estimado_por_visita_plan_min() if margen_viaje is None else margen_viaje
        )
        for t, hueco in zip(sorted(tiempos, reverse=True), huecos):
            # Excepción del tope diario: una visita más larga que la jornada
            # entera solo cabe en un día para ella sola. Sin esto el punto se
            # daba por imposible aquí y este pase le quitaba las visitas que ya
            # tenía agendadas, mandándolas a pendientes.
            if float(t or 0) > max_dia_flex():
                if not dia_vacio_disponible:
                    return False
                dia_vacio_disponible = False
                continue
            # Se reserva el desplazamiento de enganche al resto de la jornada.
            if t + reserva > hueco:
                return False
    return True


def _candidatos_cercanos(
    state, mercs_por_zona, zona, coords_merc, inst, excluir=None, radio_km=None,
    prov_merc=None,
):
    """
    Mercadistas que pueden hacerse cargo de este punto sin romper la distancia.

    Se empieza por los de su propia zona y se añaden los de zonas VECINAS cuyos
    puntos estén dentro del diámetro admitido. Limitarse a la zona propia
    desperdiciaba flota: el plan asigna ceil(carga_zona / 9600) mercadistas a
    cada zona, y ese redondeo hacia arriba cuesta ~13 personas repartidas en 26
    zonas —una zona con 1,2 mercadistas de trabajo se lleva 2—. La planificación
    real de la empresa tampoco se limita a la zona: 13 de sus 78 mercaderistas
    cubren más de 60 km. La comprobación de distancia sigue siendo la misma, así
    que no se abre la puerta a rutas imposibles.
    """
    propios = [m for m in mercs_por_zona.get(zona, []) if m != excluir]
    otros = [
        m for z, lst in mercs_por_zona.items() if z != zona
        for m in lst if m != excluir
    ]
    vistos, salida = set(), []
    for m in propios + otros:
        if m in vistos:
            continue
        vistos.add(m)
        # Barrera del tipo de carga: con "por cadena" un mercaderista de
        # TRADICIONAL no es candidato para un punto de TIA por muy cerca que
        # esté. Es la regla estricta de negocio, no una preferencia.
        if not state.puede_atender(m, inst):
            continue
        if _cabe_en_la_zona(coords_merc.get(m, []), inst, radio_km=radio_km):
            salida.append(m)

    # Los de la MISMA PROVINCIA que el punto, primero. Salir de la provincia es
    # el último recurso, no una opción más: es regla de negocio.
    if prov_merc:
        salida.sort(key=lambda m: not _misma_provincia(prov_merc.get(m), inst))
    return salida


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


def ejecutar_pase_rescate(state):
    """
    Pases de rescate: asignar visitas pendientes respetando limites.
    Modifica state.all_day_summaries in-place.
    """
    pendientes_total = state.count_remaining()
    if pendientes_total == 0:
        return

    # Cada pase parte de cero: lo que no quepa en ESTE pase es lo que queda.
    state.sin_hueco = []

    # Minutos que ESTE pase añade a cada mercaderista. Vive FUERA del bucle por
    # zona a propósito: desde que el rescate puede ofrecer una visita a un
    # mercaderista de zona vecina, una misma persona recibe trabajo en varias
    # iteraciones del bucle. Reiniciando el contador en cada zona, la segunda
    # empezaba a contar desde cero y el tope mensual se comprobaba contra un
    # total falso: cuatro mercaderistas acababan por encima de sus 9.600 min,
    # hasta 10.920.
    total_merc_rescue = defaultdict(float)
    total_min_por_merc = defaultdict(float)
    for s in state.all_day_summaries:
        # Mismo criterio que el tope contra el que se compara (cuota_mes(),
        # que es la cuota COMBINADA): con el modelo "con tiempo de
        # desplazamiento" contar solo el servicio dejaba al rescate creyendo que
        # quedaba hueco mensual donde ya no lo había. Con el modelo "sin
        # desplazamiento" el viaje aporta 0 y esto es la suma de servicio de
        # siempre.
        total_min_por_merc[s.get("Mercadista", "")] += carga_jornada(
            float(s.get("Tiempo Servicio (min)", 0) or 0),
            float(s.get("Tiempo entre sucursal (min)", 0) or 0),
        )

    print(
        f"      -> Asignando {pendientes_total} visitas pendientes (rescue), "
        f"limite {max_servicio_dia()} min/dia y {cuota_mes()} min/mes por mercadista..."
    )

    for prov_p, lst in list(state.remaining_by_prov.items()):
        if not lst:
            continue
        # Candidatos: TODOS los mercaderistas, no solo los de la zona de la
        # visita. La zona ya no es una región con varias personas dentro —desde
        # que la flota se dimensiona por capacidad mensual hay exactamente una
        # por zona—, así que filtrar por zona dejaba un único candidato: el
        # mismo dueño que ya había dicho que no le cabía. El control geográfico
        # real lo hace `_cabe_en_la_zona` unos renglones más abajo, que exige
        # que el punto entre en el radio operativo del mercaderista.
        mercs_prov = [mn for mn, _pa in state.mercadistas_plan]
        propios = {
            mn for mn, pa in state.mercadistas_plan if pa and norm_provincia(pa) == prov_p
        }
        # Se prueba primero con los de la zona propia: mover trabajo al vecino
        # es la salida, no la preferencia.
        mercs_prov.sort(key=lambda mn: (mn not in propios, mn))
        if not mercs_prov:
            continue

        pares = []
        for inst in lst:
            semana_num = inst.get("semana_num")
            frecuencia_mes = inst.get("frecuencia_mes", 1)
            if semana_num is None:
                num_s = semanas_por_frecuencia(frecuencia_mes)
                semanas_rescue = list(range(1, num_s + 1))
            else:
                semanas_rescue = [semana_num]
            for semana in semanas_rescue:
                pares.append((inst, semana))

        buckets = {}
        # Estado por bucket: {"servicio": float, "travel": float,
        #                      "last_lat": float|None, "last_lon": float|None}
        #
        # Se inicializa con lo que YA tiene agendado cada (mercadista, día,
        # semana). Antes arrancaba vacío, así que `_fits_combinado` daba por
        # libre una jornada que en realidad estaba al tope y le encajaba otros
        # 480 min. Esas visitas se escribían igual, el postproceso las recortaba
        # por pasarse de 480 y acababan en pendientes con el motivo "sin hueco
        # en ningún día" — habiendo desplazado por el camino a otras que sí
        # cabían.
        bucket_state = _estado_dias_existente(state)
        coords_merc = _coords_por_mercadista(state)
        prov_merc = _provincia_por_mercadista(state)


        # Puntos ya presentes en cada bucket (mercadista, día, semana). Un punto
        # no puede visitarse dos veces el mismo día: sin este control el rescate
        # amontonaba las 5 visitas semanales de un punto de frecuencia 20 en el
        # primer día que le cabía, y al escribir el Excel la deduplicación se
        # quedaba con una sola. Las otras cuatro no aparecían ni agendadas ni
        # pendientes: desaparecían del recuento.
        puntos_en_bucket = defaultdict(set)
        for s_ in state.all_day_summaries:
            merc_s = s_.get("Mercadista", "")
            dias_s = dias_de_mercadista(merc_s)
            dia_s = s_.get("Día")
            sem_s = _semana_de(s_.get("Fecha"))
            if dia_s not in dias_s or sem_s is None:
                continue
            try:
                clave_s = (
                    round(float(s_.get("Latitud") or 0), 6),
                    round(float(s_.get("Longitud") or 0), 6),
                    str(s_.get("Descripción", "")).strip(),
                )
            except (TypeError, ValueError):
                continue
            puntos_en_bucket[(merc_s, dias_s.index(dia_s), sem_s)].add(clave_s)

        def _fits_combinado(key, inst_):
            """
            ¿Cabe `inst_` en `buckets[key]`?

            Comprueba, además de la carga: que el punto no se repita ese día y
            que el SALTO desde la última visita de la jornada sea razonable.

            El tope por salto faltaba, y era el agujero por el que salían rutas
            imposibles: la jornada solo miraba el total del día, así que un
            trayecto de 485 km y 515 min entraba mientras el total cuadrara.
            Un mercaderista no cruza medio país entre dos tiendas.
            """
            if clave_punto(inst_) in puntos_en_bucket.get(key, ()):
                return False
            st = bucket_state.get(key, {"servicio": 0.0, "travel": 0.0, "last_lat": None, "last_lon": None})
            nuevo_serv = st["servicio"] + inst_["tiempo"]
            # Excepción del tope diario: una visita más larga que la jornada
            # entera solo cabe en un día vacío, para ella sola. Sin esto el
            # rescate no podía recolocar esos puntos y acababan en pendientes
            # aunque el asignador sí les hubiera hecho sitio.
            dia_vacio = st["servicio"] <= 0
            visita_mayor_que_jornada = float(inst_["tiempo"] or 0) > max_dia_flex()
            if dia_vacio and visita_mayor_que_jornada:
                return _salto_admisible(st["last_lat"], st["last_lon"], inst_)
            if nuevo_serv > max_dia_flex():
                return False
            if not _salto_admisible(st["last_lat"], st["last_lon"], inst_):
                return False
            travel_nuevo = _travel_desde(st["last_lat"], st["last_lon"], inst_)
            combinado = nuevo_serv + st["travel"] + travel_nuevo
            return combinado <= max_dia_flex()

        def _agregar(key, inst_, semana_):
            buckets.setdefault(key, []).append((inst_, semana_))
            puntos_en_bucket[key].add(clave_punto(inst_))
            # Las coordenadas del mercaderista se actualizan AQUÍ, no solo al
            # empezar el pase. Con la foto inicial, cada punto se comparaba
            # contra un conjunto que ya no era el real y la zona se iba
            # estirando visita a visita.
            try:
                coords_merc.setdefault(key[0], []).append(
                    (round(float(inst_["lat"]), 4), round(float(inst_["lon"]), 4))
                )
            except (TypeError, ValueError, KeyError):
                pass
            st = bucket_state.get(key, {"servicio": 0.0, "travel": 0.0, "last_lat": None, "last_lon": None})
            travel_nuevo = _travel_desde(st["last_lat"], st["last_lon"], inst_)
            st["servicio"] += inst_["tiempo"]
            st["travel"] += travel_nuevo
            try:
                st["last_lat"] = float(inst_["lat"])
                st["last_lon"] = float(inst_["lon"])
            except (TypeError, ValueError, KeyError):
                pass
            bucket_state[key] = st
            # Los minutos de viaje que añade esta inserción: el llamador los
            # necesita para descontarlos de la cuota mensual con la misma vara
            # con la que el tope está expresado.
            return travel_nuevo

        # De mayor a menor servicio: las visitas largas son las que menos huecos
        # admiten, así que se colocan primero (first-fit decreasing). Al revés,
        # las cortas se comían los pocos huecos grandes y las largas ya no
        # entraban en ningún sitio.
        pares.sort(key=lambda par: -float(par[0].get("tiempo") or 0))

        for inst, semana in pares:
            asignado = False
            idx_punto = state.get_punto_key(inst)
            # Solo mercadistas a los que este punto puede ir: no asignado o ya asignado a ese merc
            mercs_validos = [
                m for m in mercs_prov
                if (idx_punto not in state.punto_mercadista or state.punto_mercadista[idx_punto] == m)
                and state.puede_atender(m, inst)
                and _cabe_en_la_zona(coords_merc.get(m, []), inst)
            ]
            # Primero el dueño / la zona propia y, dentro de eso, el que menos
            # carga lleva: repartir el sobrante al más descargado es lo que
            # aplana la ocupación en vez de abrir plazas nuevas.
            # Orden de preferencia, de más a menos:
            #   1) mercaderistas de la MISMA PROVINCIA que el punto
            #   2) los de su propia zona
            #   3) los menos cargados
            #
            # La provincia manda porque es regla de negocio: un mercaderista
            # trabaja su provincia y solo sale de ella cuando no queda otra.
            # Antes solo se miraba la zona, y como desde el nuevo dimensionado
            # hay una zona por persona, el criterio no distinguía nada: por eso
            # aparecían rutas Rumiñahui→Puyo o Quevedo→Quito.
            mercs_ordenados = sorted(
                mercs_validos,
                key=lambda m: (
                    not _misma_provincia(prov_merc.get(m), inst),
                    m not in propios,
                    total_min_por_merc[m] + total_merc_rescue[m],
                ),
            )
            for merc in mercs_ordenados:
                if asignado:
                    break
                tot_mes = total_min_por_merc[merc] + total_merc_rescue[merc]
                if tot_mes + inst["tiempo"] > cuota_mes():
                    continue
                # Best-fit: el día con MENOS hueco de los que admiten la visita.
                # Tomar el primero que cabe (first-fit por índice de día) gastaba
                # jornadas casi vacías en visitas pequeñas y dejaba sin sitio a
                # las grandes, que ya no encontraban ningún día holgado.
                opciones = []
                for dia_idx in range(len(DAY_NAMES)):
                    key = (merc, dia_idx, semana)
                    if _fits_combinado(key, inst):
                        st = bucket_state.get(key)
                        usado = (st["servicio"] + st["travel"]) if st else 0.0
                        opciones.append((max_dia_flex() - usado, key))
                if opciones:
                    _, key = min(opciones)
                    travel_nuevo = _agregar(key, inst, semana)
                    total_merc_rescue[merc] += carga_jornada(inst["tiempo"], travel_nuevo)
                    state.asignar_punto(idx_punto, merc)
                    asignado = True
            if not asignado:
                candidatos = []
                for merc in mercs_ordenados:
                    if total_min_por_merc[merc] + total_merc_rescue[merc] + inst["tiempo"] > cuota_mes():
                        continue
                    for d in range(len(DAY_NAMES)):
                        key = (merc, d, semana)
                        if _fits_combinado(key, inst):
                            tot_b = bucket_state.get(key, {}).get("servicio", 0.0)
                            candidatos.append(
                                (key, tot_b, total_min_por_merc[merc] + total_merc_rescue[merc])
                            )
                if candidatos:
                    key_menor, _, _ = min(candidatos, key=lambda x: (x[2], x[1]))
                    travel_nuevo = _agregar(key_menor, inst, semana)
                    total_merc_rescue[key_menor[0]] += carga_jornada(
                        inst["tiempo"], travel_nuevo
                    )
                    state.asignar_punto(idx_punto, key_menor[0])
                else:
                    # Sin hueco en ninguna jornada existente. Se guarda la
                    # instancia para que el procesador pueda abrirle plaza; si
                    # tampoco así cabe, `recolectar_pendientes` la reportará al
                    # final. Antes se cerraba aquí como pendiente definitivo y
                    # esas visitas ya no volvían a intentarse nunca.
                    state.sin_hueco.append(inst)

        for (merc, dia_idx, semana), items in buckets.items():
            seen_punto = set()
            items_uniq = []
            for inst, sem in items:
                clave = clave_punto(inst)
                if clave in seen_punto:
                    continue
                seen_punto.add(clave)
                items_uniq.append((inst, sem))

            dia_nombre = dias_de_mercadista(merc)[dia_idx]
            for orden_dia, (inst, semana) in enumerate(items_uniq, 1):
                key_coord = (round(float(inst["lat"]), 6), round(float(inst["lon"]), 6))
                provincia, ciudad, calle = _geocode_cache.get(key_coord, ("", "", ""))
                state.all_day_summaries.append({
                    "Mercadista": merc,
                    "Día": dia_nombre,
                    "Orden Ruta": orden_dia,
                    "Descripción": inst["descripcion"],
                    "Latitud": inst["lat"],
                    "Longitud": inst["lon"],
                    "PROVINCIA": provincia,
                    "CIUDAD": ciudad,
                    "CALLE": calle,
                    "Tiempo Servicio (min)": inst["tiempo"],
                    "Duración (hh:mm)": format_duracion(inst["tiempo"]),
                    "Tiempo entre sucursal (min)": 0.0,
                    "kilometros entre sucurlas (km)": 0.0,
                    "Horario": "Pendiente",
                    "Fecha": f"semana {semana}",
                })

        state.remaining_by_prov[prov_p] = []


def ejecutar_red_seguridad(state):
    """
    Red de seguridad: que la suma de visitas (FRECUENCIA MES)
    coincida con filas generadas. Anade faltantes.
    """
    filas_ya_emitidas = set()
    for s in state.all_day_summaries:
        try:
            lat_f = float(s.get("Latitud", 0))
            lon_f = float(s.get("Longitud", 0))
        except (TypeError, ValueError):
            continue
        desc = str(s.get("Descripción", "")).strip()
        fecha = str(s.get("Fecha", "")).strip()
        filas_ya_emitidas.add((round(lat_f, 6), round(lon_f, 6), desc, fecha))

    faltantes = []
    added_clave_semana = set()
    for inst in state.visit_instances:
        try:
            lat_f = float(inst.get("lat", 0))
            lon_f = float(inst.get("lon", 0))
        except (TypeError, ValueError):
            continue
        desc = str(inst.get("descripcion", "")).strip()
        cp = (round(lat_f, 6), round(lon_f, 6), desc)
        sn = inst.get("semana_num")
        if sn is None:
            for semana in range(1, 5):
                key = (*cp, f"semana {semana}")
                if key in added_clave_semana or key in filas_ya_emitidas:
                    continue
                faltantes.append((inst, semana))
                filas_ya_emitidas.add(key)
                added_clave_semana.add(key)
        else:
            key = (*cp, f"semana {sn}")
            if key in added_clave_semana or key in filas_ya_emitidas:
                continue
            faltantes.append((inst, sn))
            filas_ya_emitidas.add(key)
            added_clave_semana.add(key)

    total_esperado_visitas = len(state.visit_instances)
    if not faltantes or len(state.all_day_summaries) >= total_esperado_visitas:
        return

    max_a_anadir = total_esperado_visitas - len(state.all_day_summaries)
    faltantes = faltantes[:max_a_anadir]
    print(f"      -> Anadiendo {len(faltantes)} visitas faltantes (red de seguridad, tope {total_esperado_visitas})...")

    # Servicio acumulado, desplazamiento acumulado y última posición conocida
    # por grupo (Mercadista, Día, Fecha). Última posición = la del Orden Ruta
    # mayor en cada grupo, que es donde se "engancharía" una visita añadida.
    tot_por_grupo = defaultdict(float)
    travel_por_grupo = defaultdict(float)
    last_pos_por_grupo = {}  # k -> (orden_ruta, lat, lon)
    for s in state.all_day_summaries:
        k = (s.get("Mercadista"), s.get("Día"), s.get("Fecha"))
        try:
            tot_por_grupo[k] += float(s.get("Tiempo Servicio (min)", 0) or 0)
        except (TypeError, ValueError):
            pass
        try:
            travel_por_grupo[k] += float(s.get("Tiempo entre sucursal (min)", 0) or 0)
        except (TypeError, ValueError):
            pass
        try:
            orden_actual = int(s.get("Orden Ruta", 0) or 0)
            lat_s = float(s.get("Latitud", 0))
            lon_s = float(s.get("Longitud", 0))
            prev = last_pos_por_grupo.get(k)
            if prev is None or orden_actual > prev[0]:
                last_pos_por_grupo[k] = (orden_actual, lat_s, lon_s)
        except (TypeError, ValueError):
            pass

    # Carga mensual por mercadista, para repartir los faltantes en el que tenga
    # más hueco dentro de su zona en vez de en el primero que aparezca.
    carga_por_merc = defaultdict(float)
    for s in state.all_day_summaries:
        carga_por_merc[s.get("Mercadista", "")] += carga_jornada(
            float(s.get("Tiempo Servicio (min)", 0) or 0),
            float(s.get("Tiempo entre sucursal (min)", 0) or 0),
        )
    coords_merc = _coords_por_mercadista(state)

    for inst, semana in faltantes:
        idx_punto = state.get_punto_key(inst)
        # Si el punto ya pertenece a un mercaderista, solo puede ir a ese —pero
        # únicamente si de verdad le pilla cerca. Tener dueño no bastaba: la
        # propiedad se acepta sin mirar la distancia y las pasadas de
        # replanificación pueden haber dejado al punto con un dueño que quedó a
        # 200 km. En ese caso es mejor dejarlo pendiente y que se vea.
        if idx_punto in state.punto_mercadista:
            merc = state.punto_mercadista[idx_punto]
            if not _cabe_en_la_zona(
                coords_merc.get(merc, []), inst, radio_km=RADIO_RESCATE_AMPLIADO_KM
            ):
                state.puntos_pendientes.append({
                    "motivo": "red de seguridad: su mercaderista queda demasiado lejos del punto",
                    "descripcion": inst.get("descripcion", ""),
                    "lat": inst.get("lat"),
                    "lon": inst.get("lon"),
                    "semana": semana,
                    "tiempo": inst.get("tiempo", 60),
                    "frecuencia_mes": inst.get("frecuencia_mes"),
                    "provincia_punto": inst.get("provincia_punto", ""),
                })
                continue
        else:
            # Por ZONA, no por provincia. El plan guarda claves de zona
            # ("GUAYAS Z01"), así que compararlas con el nombre de la provincia
            # del punto ("GUAYAS") no casaba nunca: el `merc` caía siempre al
            # fallback y todos los faltantes sin dueño se apilaban en un mismo
            # mercadista, sin ninguna relación geográfica entre ellos (así
            # aparecía un mercadista con puntos a 371 km unos de otros).
            zona = state.zona_de(inst)
            mercs_zona = [
                mn for mn, pa in state.mercadistas_plan
                if pa and norm_provincia(pa) == zona
            ]
            mercs_zona = [
                m for m in mercs_zona
                # Barrera del tipo de carga, igual que en los demás pases.
                if state.puede_atender(m, inst)
                and _cabe_en_la_zona(coords_merc.get(m, []), inst)
            ]
            merc = min(mercs_zona, key=lambda m: carga_por_merc[m]) if mercs_zona else None
            if merc is None:
                # Sin mercadistas en la zona del punto: dejarlo pendiente es
                # correcto. Colocarlo en cualquier otro lo pondría a cientos de
                # km de su ruta, que es peor que no colocarlo.
                state.puntos_pendientes.append({
                    "motivo": "red de seguridad: sin mercadista en la zona del punto",
                    "descripcion": inst.get("descripcion", ""),
                    "lat": inst.get("lat"),
                    "lon": inst.get("lon"),
                    "semana": semana,
                    "tiempo": inst.get("tiempo", 60),
                    "frecuencia_mes": inst.get("frecuencia_mes"),
                    "provincia_punto": inst.get("provincia_punto", ""),
                })
                continue

        tiempo_min = inst.get("tiempo", 60)
        fecha_s = f"semana {semana}"
        # Igual que en el resto de controles mensuales: se mide con lo que
        # consume jornada según el modelo activo, porque el tope contra el que
        # se compara (cuota_mes()) es la cuota combinada.
        tot_mes_merc = sum(
            carga_jornada(tot_por_grupo[k], travel_por_grupo.get(k, 0.0))
            for k in tot_por_grupo
            if k[0] == merc
        )

        def _fits_combinado_safety(k):
            """¿Caben los `tiempo_min` de servicio + el viaje estimado a ese grupo?"""
            serv_actual = tot_por_grupo[k]
            travel_actual = travel_por_grupo.get(k, 0.0)
            prev = last_pos_por_grupo.get(k)
            travel_nuevo = 0.0
            if prev is not None:
                try:
                    travel_nuevo = float(estimar_minutos_viaje(
                        prev[1], prev[2], float(inst.get("lat", 0)), float(inst.get("lon", 0))
                    ))
                except Exception:
                    travel_nuevo = 0.0
            if serv_actual + tiempo_min > max_dia_flex():
                return False, travel_nuevo
            # Tope por SALTO, igual que en el pase de rescate. Sin él este pase
            # encadenaba visitas a cualquier distancia mientras el total del día
            # cuadrara: puntos de Sucumbíos sin provincia geocodificada acababan
            # en una jornada de Quito, a 201 km de la visita anterior.
            if prev is not None and not _salto_admisible(prev[1], prev[2], inst):
                return False, travel_nuevo
            combinado = serv_actual + tiempo_min + travel_actual + travel_nuevo
            return combinado <= max_dia_flex(), travel_nuevo

        con_hueco = []
        for k in tot_por_grupo:
            if k[0] != merc or k[2] != fecha_s:
                continue
            if tot_mes_merc + tiempo_min > cuota_mes():
                continue
            ok, travel_nuevo = _fits_combinado_safety(k)
            if ok:
                con_hueco.append((k, tot_por_grupo[k], travel_nuevo))
        if con_hueco:
            mejor = min(con_hueco, key=lambda x: x[1])
            mejor_k = mejor[0]
            mejor_travel_nuevo = mejor[2]
            dia_asig = mejor_k[1]
        else:
            # No hay hueco en mercadistas existentes; no se crean adicionales.
            # Registrar como pendiente para visibilidad en reporte y Excel.
            state.puntos_pendientes.append({
                "motivo": "red de seguridad: sin hueco respetando 480 min/dia combinado",
                "descripcion": inst.get("descripcion", ""),
                "lat": inst.get("lat"),
                "lon": inst.get("lon"),
                "semana": semana,
                "tiempo": tiempo_min,
                "frecuencia_mes": inst.get("frecuencia_mes"),
                "provincia_punto": inst.get("provincia_punto", ""),
            })
            continue

        tot_por_grupo[mejor_k] = tot_por_grupo.get(mejor_k, 0) + tiempo_min
        travel_por_grupo[mejor_k] = travel_por_grupo.get(mejor_k, 0.0) + mejor_travel_nuevo
        try:
            lat_n = float(inst.get("lat", 0))
            lon_n = float(inst.get("lon", 0))
            prev = last_pos_por_grupo.get(mejor_k)
            nuevo_orden = (prev[0] if prev else 0) + 1
            last_pos_por_grupo[mejor_k] = (nuevo_orden, lat_n, lon_n)
        except (TypeError, ValueError):
            pass
        if not state.asignar_punto(idx_punto, merc, inst):
            # La regla del tipo de carga manda sobre el relleno: antes que
            # mezclar cadenas en un mercaderista, la visita queda pendiente.
            state.puntos_pendientes.append({
                "motivo": "tipo de carga: no hay mercaderista de esa cadena/ciudad con hueco",
                "descripcion": inst.get("descripcion", ""),
                "lat": inst.get("lat"),
                "lon": inst.get("lon"),
                "semana": semana,
                "tiempo": tiempo_min,
                "frecuencia_mes": inst.get("frecuencia_mes"),
                "provincia_punto": inst.get("provincia_punto", ""),
            })
            continue
        carga_por_merc[merc] += carga_jornada(tiempo_min, mejor_travel_nuevo)
        state.all_day_summaries.append({
            "Mercadista": merc,
            "Día": dia_asig,
            "Orden Ruta": 1,
            "Descripción": inst.get("descripcion", ""),
            "Latitud": inst.get("lat"),
            "Longitud": inst.get("lon"),
            "PROVINCIA": "",
            "CIUDAD": "",
            "CALLE": "",
            "Tiempo Servicio (min)": tiempo_min,
            "Duración (hh:mm)": format_duracion(tiempo_min),
            "Tiempo entre sucursal (min)": 0.0,
            "kilometros entre sucurlas (km)": 0.0,
            "Horario": "Pendiente",
            "Fecha": fecha_s,
        })


def reinyectar_frecuencias_incompletas(state, df):
    """
    Devuelve al pool las visitas que faltan para completar la frecuencia mensual
    de cada punto, para que el rescate intente colocarlas.

    El motor detectaba este hueco solo al ESCRIBIR el Excel
    (`reconciliar_pendientes_por_frecuencia`), cuando ya no se podía hacer nada:
    ahí las visitas faltantes se limitan a anotarse como pendientes. Y son la
    mayor parte del faltante —336 de 474 visitas en la última medición— pese a
    que la capacidad para colocarlas existe: 376 jornadas tenían 240 min libres
    o más, y las visitas que faltaban miden entre 60 y 240.

    Aquí se hace la misma cuenta antes de escribir y se reinyectan como
    instancias normales, con lo que el pase de rescate las coloca con sus reglas
    de siempre (tope diario, tope mensual, distancia y un punto una vez por día).

    Retorna cuántas visitas se reinyectaron.
    """
    from route_engine.excel_reader import clave_punto_frecuencia
    from route_engine.excel_writer import _info_puntos_desde_df

    info = _info_puntos_desde_df(df)
    if not info:
        return 0

    # 1) Lo que ya está agendado, por (punto, tiempo de servicio). El tiempo
    #    distingue las distintas filas de una misma tienda.
    agendadas = defaultdict(int)
    for s in state.all_day_summaries:
        key = clave_punto_frecuencia(
            s.get("Descripción"), s.get("Latitud"), s.get("Longitud")
        )
        if key[1] is None:
            continue
        try:
            t = float(s.get("Tiempo Servicio (min)", 0) or 0)
        except (TypeError, ValueError):
            continue
        agendadas[(key, t)] += 1

    # 2) Instancias disponibles para clonar, por (punto, tiempo).
    plantillas = {}
    for inst in state.visit_instances:
        key = clave_punto_frecuencia(
            inst.get("descripcion"), inst.get("lat"), inst.get("lon")
        )
        if key[1] is None:
            continue
        plantillas.setdefault((key, float(inst.get("tiempo") or 0)), inst)

    # 3) Reinyectar la diferencia.
    reinyectadas = 0
    for key, configs in info.items():
        for cfg in configs:
            try:
                tiempo = float(cfg.get("tiempo") or 0)
                requeridas = int(cfg.get("frecuencia") or 0)
            except (TypeError, ValueError):
                continue
            if tiempo <= 0 or requeridas <= 0:
                continue
            faltan = requeridas - agendadas.get((key, tiempo), 0)
            if faltan <= 0:
                continue
            base = plantillas.get((key, tiempo))
            if base is None:
                continue

            # Repartidas entre las 4 semanas: concentrarlas en una sola las
            # haría competir por los mismos cinco días y volverían a caerse.
            for i in range(faltan):
                nueva = dict(base)
                nueva["semana_num"] = (i % NUM_SEMANAS_POR_MERCADISTA) + 1
                nueva["required_day"] = None
                zona = state.zona_de(nueva)
                state.remaining_by_prov.setdefault(zona, []).append(nueva)
                reinyectadas += 1

    if reinyectadas:
        print(
            f"      -> Frecuencias incompletas: {reinyectadas} visita(s) devueltas "
            f"al pool para intentar colocarlas antes de darlas por pendientes"
        )
    return reinyectadas
