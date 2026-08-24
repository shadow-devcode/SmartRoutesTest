from collections import defaultdict

from route_engine.config import (
    max_servicio_dia,
)
from route_engine.geo import haversine_km


def compute_route_with_start(locations, start=None):
    """Nearest neighbor route ordering with optional start."""
    if not locations:
        return []
    if len(locations) == 1:
        return locations

    if start is None:
        avg_lat = sum(loc["lat"] for loc in locations) / len(locations)
        avg_lon = sum(loc["lon"] for loc in locations) / len(locations)
        start = min(locations, key=lambda loc: haversine_km(avg_lat, avg_lon, loc["lat"], loc["lon"]))
    else:
        if start not in locations:
            start = min(locations, key=lambda loc: haversine_km(start["lat"], start["lon"], loc["lat"], loc["lon"]))

    route = [start]
    remaining = [loc for loc in locations if loc != start]
    current_lat, current_lon = start["lat"], start["lon"]

    while remaining:
        next_loc = min(remaining, key=lambda loc: haversine_km(current_lat, current_lon, loc["lat"], loc["lon"]))
        route.append(next_loc)
        remaining.remove(next_loc)
        current_lat, current_lon = next_loc["lat"], next_loc["lon"]
    return route


def compute_route_por_ciudad(locations):
    """
    Ordena la ruta por ciudad/localidad para evitar saltos Quito -> Sangolqui -> Quito.
    Agrupa por localidad_punto (ciudad); ordena las ciudades por distancia desde el
    punto de inicio; dentro de cada ciudad ordena por nearest neighbor.
    """
    if not locations:
        return []
    if len(locations) == 1:
        return locations

    por_ciudad = defaultdict(list)
    for v in locations:
        ciudad = (v.get("localidad_punto") or v.get("provincia_punto") or "").strip() or "SIN_LOCALIDAD"
        por_ciudad[ciudad].append(v)

    avg_lat = sum(loc["lat"] for loc in locations) / len(locations)
    avg_lon = sum(loc["lon"] for loc in locations) / len(locations)
    start = min(locations, key=lambda loc: haversine_km(avg_lat, avg_lon, loc["lat"], loc["lon"]))
    start_ciudad = (start.get("localidad_punto") or start.get("provincia_punto") or "").strip() or "SIN_LOCALIDAD"

    def centroide(lst):
        if not lst:
            return avg_lat, avg_lon
        lat = sum(x["lat"] for x in lst) / len(lst)
        lon = sum(x["lon"] for x in lst) / len(lst)
        return lat, lon

    ciudades_orden = sorted(
        por_ciudad.keys(),
        key=lambda c: (0 if c == start_ciudad else 1, haversine_km(start["lat"], start["lon"], *centroide(por_ciudad[c]))),
    )

    ruta_final = []
    ref_lat, ref_lon = start["lat"], start["lon"]
    for ciudad in ciudades_orden:
        grupo = list(por_ciudad[ciudad])
        while grupo:
            siguiente = min(grupo, key=lambda x: haversine_km(ref_lat, ref_lon, x["lat"], x["lon"]))
            ruta_final.append(siguiente)
            grupo.remove(siguiente)
            ref_lat, ref_lon = siguiente["lat"], siguiente["lon"]
    return ruta_final


def ordenar_por_localidad_y_cercania(pool, ref_lat, ref_lon):
    """
    Ordena visitas por localidad (sector/parroquia) y luego por cercania.
    Agrupa por localidad_punto; ordena grupos por distancia del centroide al ref;
    dentro de cada grupo ordena por cercania a ref (o al anterior).
    """
    if not pool:
        return []

    por_localidad = defaultdict(list)
    for v in pool:
        loc = (v.get("localidad_punto") or v.get("provincia_punto") or "").strip() or "SIN_LOCALIDAD"
        por_localidad[loc].append(v)

    def centroide(lst):
        if not lst:
            return ref_lat, ref_lon
        lat = sum(x["lat"] for x in lst) / len(lst)
        lon = sum(x["lon"] for x in lst) / len(lst)
        return lat, lon

    localidades_orden = sorted(
        por_localidad.keys(),
        key=lambda loc: haversine_km(ref_lat, ref_lon, *centroide(por_localidad[loc])),
    )

    ordenado = []
    for loc in localidades_orden:
        grupo = por_localidad[loc]
        ref_a, ref_b = ref_lat, ref_lon
        while grupo:
            siguiente = min(grupo, key=lambda x: haversine_km(ref_a, ref_b, x["lat"], x["lon"]))
            ordenado.append(siguiente)
            grupo.remove(siguiente)
            ref_a, ref_b = siguiente["lat"], siguiente["lon"]
    return ordenado


def construir_dia_por_cercania(week_remaining, target_service):
    """Construye un dia de visitas seleccionando por cercania hasta alcanzar el target."""
    if not week_remaining:
        return [], []

    avg_lat = sum(loc["lat"] for loc in week_remaining) / len(week_remaining)
    avg_lon = sum(loc["lon"] for loc in week_remaining) / len(week_remaining)
    start = min(week_remaining, key=lambda loc: haversine_km(avg_lat, avg_lon, loc["lat"], loc["lon"]))

    day_assigned = []
    used_idx = set()
    remaining = list(week_remaining)
    total = 0.0

    def pick_next(current):
        candidates = [c for c in remaining if c["idx"] not in used_idx]
        if not candidates:
            return None
        ref_lat, ref_lon = (current["lat"], current["lon"]) if current else (avg_lat, avg_lon)
        ordenados = ordenar_por_localidad_y_cercania(candidates, ref_lat, ref_lon)
        return ordenados[0] if ordenados else None

    current = None
    while True:
        if total >= target_service:
            break
        candidate = pick_next(current)
        if candidate is None:
            break
        # Excepción a la cuota diaria: una visita que POR SÍ SOLA dura más que
        # la jornada (p. ej. 540 min con cuota de 480) no cabe en ningún día
        # acompañada de nada. Si el día está vacío se acepta y ese día vale lo
        # que valga esa visita; el dato de entrada manda. Sin esto, esos puntos
        # no se atenderían jamás y acabarían siempre en pendientes.
        dia_vacio = not day_assigned
        visita_mayor_que_jornada = float(candidate["tiempo"] or 0) > max_servicio_dia()
        if total + candidate["tiempo"] > max_servicio_dia() and not (
            dia_vacio and visita_mayor_que_jornada
        ):
            fit = [c for c in remaining if c["idx"] not in used_idx and total + c["tiempo"] <= max_servicio_dia()]
            if not fit:
                break
            ref_lat, ref_lon = (current["lat"], current["lon"]) if current else (avg_lat, avg_lon)
            ordenados_fit = ordenar_por_localidad_y_cercania(fit, ref_lat, ref_lon)
            candidate = (
                ordenados_fit[0]
                if ordenados_fit
                else min(fit, key=lambda loc: haversine_km(ref_lat, ref_lon, loc["lat"], loc["lon"]))
            )

        day_assigned.append(candidate)
        used_idx.add(candidate["idx"])
        remaining.remove(candidate)
        total += candidate["tiempo"]
        current = candidate

    if not day_assigned:
        day_assigned.append(start)
        remaining.remove(start)

    return day_assigned, remaining
