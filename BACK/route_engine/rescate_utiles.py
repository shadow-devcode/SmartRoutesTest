"""
Utilidades compartidas por los pases de rescate.

Distancias y saltos admisibles, provincia y zona de cada mercaderista, estado
de las jornadas ya agendadas y búsqueda de candidatos cercanos. Nada de esto
decide nada por sí solo: son las preguntas que los pases de rescate necesitan
responder antes de mover una visita.
"""
from __future__ import annotations

from collections import defaultdict

from route_engine.config import (
    DAY_NAMES,
    RADIO_ZONA_KM,
    carga_jornada,
    cuota_dia,
    dias_de_mercadista,
    tope_dia_combinado,
    travel_estimado_por_visita_plan_min,
)
from route_engine.frequency import semanas_distribuidas
from route_engine.geo import estimar_minutos_viaje, haversine_km
from route_engine.mapbox import norm_provincia


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
            huecos.append(tope_dia_combinado() - usado)
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
            if float(t or 0) > tope_dia_combinado():
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
