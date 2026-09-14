import math
import re

# Primer número con signo en texto sucio (p. ej. '"" "-1.00672"' tras quitar comillas).
_RE_FLOAT_EN_TEXTO = re.compile(
    r"[-+]?(?:\d+\.?\d*|\d*\.?\d+)(?:[eE][-+]?\d+)?"
)


def parse_coordenada_a_float(valor):
    """
    Convierte una celda de coordenada a float.

    Acepta números nativos y textos con comillas o basura (p. ej. '"" "-1.0067211534888993"').
    Devuelve None si no hay un número válido.
    """
    if valor is None:
        return None
    try:
        import pandas as pd

        if pd.isna(valor):
            return None
    except Exception:
        pass
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        if isinstance(valor, float) and (math.isnan(valor) or math.isinf(valor)):
            return None
        return float(valor)

    s = str(valor).strip()
    for ch in ('"', "'", "\u201c", "\u201d", "\u2018", "\u2019", "\u00ab", "\u00bb"):
        s = s.replace(ch, "")
    s = s.strip()
    if not s:
        return None
    if s.count(",") == 1 and "." not in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        pass
    m = _RE_FLOAT_EN_TEXTO.search(s.replace(",", "."))
    if m:
        try:
            return float(m.group(0))
        except ValueError:
            pass
    return None


def _recortar_a_rango_geografico(f: float, tipo: str):
    """
    Si abs(f) excede el rango geográfico válido, recupera la coordenada dividiendo
    por 10 iterativamente. Maneja celdas Excel donde se perdió el punto decimal
    (e.g. -32702E+14 → -3.2702, -799343E+14 → -79.9343).
    Devuelve el float corregido o None si no es recuperable.
    """
    max_abs = 90.0 if tipo == "lat" else 180.0
    prefer_abs = 9.0 if tipo == "lat" else 99.0
    if abs(f) <= max_abs:
        return f
    sign = -1.0 if f < 0 else 1.0
    av = abs(f)
    while av > max_abs:
        av /= 10.0
        if av == 0.0:
            return None
    if av > prefer_abs:
        av /= 10.0
    return (sign * av) if av != 0.0 else None


def normalizar_coord_geografica(valor, tipo="lon"):
    """
    Parsea y normaliza una coordenada al rango geográfico válido usando recorte.
    A diferencia de corregir_coordenada, devuelve None (no 0.0) para distinguir
    'inválida/irrecuperable' de 'coordenada cero genuina'.
    """
    f = parse_coordenada_a_float(valor)
    if f is None:
        return None
    return _recortar_a_rango_geografico(f, tipo)


def corregir_coordenada(valor, tipo="lon"):
    """
    Normaliza coordenadas para el pipeline Excel.
    Aplica recorte automático para coordenadas fuera de rango (Excel sin decimal).
    Devuelve 0.0 si el valor es inválido o irrecuperable.
    """
    f = parse_coordenada_a_float(valor)
    if f is None:
        return 0.0
    resultado = _recortar_a_rango_geografico(f, tipo)
    return resultado if resultado is not None else 0.0


def haversine_km(lat1, lon1, lat2, lon2):
    """Distancia en km entre dos puntos geograficos (formula de Haversine)."""
    radio_tierra_km = 6371.0
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return radio_tierra_km * c


def km_por_carretera(lat1, lon1, lat2, lon2):
    """Distancia aproximada por calle: haversine corregida por el factor de rodeo."""
    from route_engine.config import DISTANCE_FACTOR_CARRETERA  # import local para evitar ciclos

    factor = DISTANCE_FACTOR_CARRETERA if DISTANCE_FACTOR_CARRETERA > 0 else 1.0
    return haversine_km(lat1, lon1, lat2, lon2) * factor


def minutos_viaje_desde_km(dist_km):
    """
    Minutos de desplazamiento para una distancia por carretera.

    Fuente ÚNICA de verdad del coste de viaje del motor. Antes había dos
    modelos que no coincidían (hasta 15x de diferencia entre el pre-filtro y el
    cálculo real), lo que hacía que se aceptaran candidatos que el cálculo
    definitivo rechazaba y que la jornada se inflara artificialmente.

    La regla es la del negocio: cada medio kilómetro recorrido cuesta cuatro
    minutos (ver `MINUTOS_POR_TRAMO_VIAJE` y `KM_POR_TRAMO_VIAJE`). Sustituye al
    modelo de velocidades por tramo que había antes, que calculaba 1 km en
    8,3 min pero 20 km en 44; con esta regla esos 20 km cuestan 160 min.

    Se aplica proporcionalmente y no por bloques empezados, de modo que el coste
    crece de forma continua: con bloques, dos destinos a 0,6 y a 1,0 km
    costarían lo mismo y el optimizador podría preferir el más lejano.
    """
    from route_engine.config import KM_POR_TRAMO_VIAJE, MINUTOS_POR_TRAMO_VIAJE

    if dist_km is None or dist_km <= 0:
        return 0.0
    if KM_POR_TRAMO_VIAJE <= 0:
        return 0.0
    return float(dist_km) / KM_POR_TRAMO_VIAJE * MINUTOS_POR_TRAMO_VIAJE


def estimar_minutos_viaje(lat1, lon1, lat2, lon2):
    """
    Minutos de viaje entre dos coordenadas. Idéntico al que aplica
    `calcular_tiempo_entre` al confirmar la visita: pre-filtro y cálculo
    definitivo deben coincidir o el motor se contradice a sí mismo.
    """
    return minutos_viaje_desde_km(km_por_carretera(lat1, lon1, lat2, lon2))
