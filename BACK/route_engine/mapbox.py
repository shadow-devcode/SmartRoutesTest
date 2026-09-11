import math
import unicodedata

import requests

from route_engine.config import DISTANCE_FACTOR_CARRETERA, MAPBOX_ACCESS_TOKEN
from route_engine.geo import haversine_km, minutos_viaje_desde_km

# Caches a nivel de modulo (compartidos durante toda la ejecucion)
_distance_cache = {}
_geocode_cache = {}


def obtener_tiempo_mapbox_seconds(lat1, lon1, lat2, lon2, api_key=None):
    """Devuelve (segundos, texto) o (None, 'N/A'). Usa Mapbox Matrix API (driving)."""
    if api_key is None:
        api_key = MAPBOX_ACCESS_TOKEN

    try:
        key = (round(float(lat1), 6), round(float(lon1), 6), round(float(lat2), 6), round(float(lon2), 6))
    except Exception:
        return (None, "N/A")

    if key in _distance_cache:
        return _distance_cache[key]

    if not api_key or api_key == "pk.YOUR_MAPBOX_ACCESS_TOKEN":
        _distance_cache[key] = (None, "N/A")
        return (None, "N/A")

    coords = f"{lon1},{lat1};{lon2},{lat2}"
    url = f"https://api.mapbox.com/directions-matrix/v1/mapbox/driving/{coords}"
    params = {"access_token": api_key, "annotations": "duration"}

    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("code") != "Ok" or not data.get("durations"):
            _distance_cache[key] = (None, "N/A")
            return (None, "N/A")
        duration_sec = data["durations"][0][1]
        if duration_sec is None:
            _distance_cache[key] = (None, "N/A")
            return (None, "N/A")
        duration_sec = int(duration_sec)
        duration_text = f"{duration_sec // 60} min"
        _distance_cache[key] = (duration_sec, duration_text)
        return (duration_sec, duration_text)
    except Exception:
        _distance_cache[key] = (None, "N/A")
        return (None, "N/A")


def calcular_tiempo_entre(prev_lat, prev_lon, lat, lon):
    """
    Devuelve (minutos entre sucursales, km entre sucursales).
    Si es el inicio (prev es None), ambos valores son 0.0.

    El cálculo parte de la distancia haversine (línea recta) y la corrige al
    valor aproximado por carretera multiplicándola por
    `DISTANCE_FACTOR_CARRETERA` (factor configurable, por defecto 1.4). Esto
    acerca las distancias y los tiempos al recorrido real sin llamar a
    Mapbox Directions por cada par de puntos.

    El tiempo sale de `minutos_viaje_desde_km`, el modelo único del motor
    (velocidad por tramo + fijo de acceso). Antes esta función aplicaba
    `ceil(km / 2) × 15`, una escalera cuyo mínimo no nulo eran 15 minutos:
    dos locales separados por 300 m costaban lo mismo que dos separados por
    2.8 km, y el pre-filtro de `scheduling` usaba otro modelo distinto que no
    coincidía. Eso consumía la jornada en desplazamiento ficticio y obligaba
    a repartir el trabajo entre muchos más mercadistas de los necesarios.

    Ejemplos (con factor 1.4):
      0.30 km recta → 0.42 km real →  6.4 min
      1.40 km recta → 1.96 km real → 11.5 min
      3.94 km recta → 5.52 km real → 18.2 min
      8.50 km recta → 11.9 km real → 22.9 min
    """
    if prev_lat is None or prev_lon is None:
        return 0.0, 0.0

    dist_recta = haversine_km(prev_lat, prev_lon, lat, lon)

    if dist_recta == 0.0:
        return 0.0, 0.0

    factor = DISTANCE_FACTOR_CARRETERA if DISTANCE_FACTOR_CARRETERA > 0 else 1.0
    dist_km = dist_recta * factor

    return minutos_viaje_desde_km(dist_km), dist_km


def obtener_direccion_desde_coordenadas(lat, lon, api_key=None):
    """
    Obtiene PROVINCIA, CIUDAD (localidad especifica), CALLE desde coordenadas (Mapbox Geocoding v6 reverse).
    Para la ciudad usa la jerarquia mas especifica: locality > place > district.
    Retorna (provincia, ciudad, calle).
    """
    if api_key is None:
        api_key = MAPBOX_ACCESS_TOKEN

    if not lat or not lon or lat == 0.0 or lon == 0.0:
        return ("", "", "")

    try:
        key = (round(float(lat), 6), round(float(lon), 6))
    except Exception:
        return ("", "", "")

    if key in _geocode_cache:
        return _geocode_cache[key]

    if not api_key or api_key == "pk.YOUR_MAPBOX_ACCESS_TOKEN":
        _geocode_cache[key] = ("", "", "")
        return ("", "", "")

    url = "https://api.mapbox.com/search/geocode/v6/reverse"
    params = {
        "longitude": float(lon),
        "latitude": float(lat),
        "access_token": api_key,
        "language": "es",
        "limit": 1,
    }

    try:
        r = requests.get(url, params=params, timeout=10)
        r.raise_for_status()
        data = r.json()

        if not data.get("features"):
            _geocode_cache[key] = ("", "", "")
            return ("", "", "")

        feat = data["features"][0]
        props = feat.get("properties", {})
        ctx = props.get("context", {})

        def _ctx_name(key_ctx):
            obj = ctx.get(key_ctx)
            return obj.get("name", "").strip() if isinstance(obj, dict) else ""

        # Se canonicaliza aquí, en el único punto donde entra el dato, para que
        # todo lo que consume el cache (horarios, pendientes, rescate) reciba ya
        # un nombre de provincia estable.
        provincia = provincia_display(_ctx_name("region"))
        locality = _ctx_name("locality")
        place = _ctx_name("place")
        district = _ctx_name("district")
        ciudad = locality or place or district or ""
        calle = _ctx_name("street") or _ctx_name("address") or (props.get("name") or "")

        _geocode_cache[key] = (provincia, ciudad, calle)
        return (provincia, ciudad, calle)
    except Exception:
        _geocode_cache[key] = ("", "", "")
        return ("", "", "")


_PREFIJOS_PROVINCIA = ("PROVINCIA DE ", "PROVINCIA DEL ", "PROVINCIA ", "PROV. ", "PROV ")

# Nombre oficial de cada provincia del Ecuador indexado por su clave canónica
# (mayúsculas sin tildes, la que produce `norm_provincia`).
#
# El mismo lugar entra al sistema escrito de varias formas: Mapbox devuelve
# "Pichincha", el motor guarda la clave interna "PICHINCHA" en la hoja de
# pendientes y una visita reinsertada desde ahí se lleva esa clave a la columna
# PROVINCIA. Sin una forma única, los desplegables mostraban "Pichincha" y
# "PICHINCHA" como si fueran dos provincias distintas y cada filtro dejaba
# fuera las visitas de la otra.
_PROVINCIAS_EC = {
    "AZUAY": "Azuay",
    "BOLIVAR": "Bolívar",
    "CANAR": "Cañar",
    "CARCHI": "Carchi",
    "CHIMBORAZO": "Chimborazo",
    "COTOPAXI": "Cotopaxi",
    "EL ORO": "El Oro",
    "ESMERALDAS": "Esmeraldas",
    "GALAPAGOS": "Galápagos",
    "GUAYAS": "Guayas",
    "IMBABURA": "Imbabura",
    "LOJA": "Loja",
    "LOS RIOS": "Los Ríos",
    "MANABI": "Manabí",
    "MORONA SANTIAGO": "Morona Santiago",
    "NAPO": "Napo",
    "ORELLANA": "Orellana",
    "PASTAZA": "Pastaza",
    "PICHINCHA": "Pichincha",
    "SANTA ELENA": "Santa Elena",
    "SANTO DOMINGO DE LOS TSACHILAS": "Santo Domingo de los Tsáchilas",
    "SUCUMBIOS": "Sucumbíos",
    "TUNGURAHUA": "Tungurahua",
    "ZAMORA CHINCHIPE": "Zamora Chinchipe",
}


def provincia_display(val):
    """
    Nombre de provincia para mostrar, en una sola forma.

    Mapbox no es consistente: para unos puntos devuelve "Provincia de
    Tungurahua" y para otros "Tungurahua". Y el propio sistema mezcla el nombre
    legible con la clave interna en mayúsculas, porque las pendientes guardan
    esta última y una visita reinsertada la arrastra hasta la columna PROVINCIA.
    Como este texto es el que agrupan el dashboard y los desplegables de filtro,
    cada variante aparecía como una provincia distinta.

    Por eso todas las variantes de una provincia ecuatoriana se resuelven contra
    `_PROVINCIAS_EC`, que da el nombre oficial con sus tildes. Lo que no esté en
    esa tabla —o venga vacío— se devuelve limpio de prefijo y de espacios, tal
    como llegó.

    A diferencia de `norm_provincia`, que produce la clave interna en mayúsculas
    y sin tildes, esto es solo para lectura humana.
    """
    if val is None:
        return ""

    s = " ".join(str(val).strip().split())
    if not s or s.lower() in ("nan", "none", "nat"):
        return ""

    oficial = _PROVINCIAS_EC.get(norm_provincia(s))
    if oficial:
        return oficial

    sin_tildes = unicodedata.normalize("NFD", s)
    sin_tildes = "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn").upper()

    for prefijo in _PREFIJOS_PROVINCIA:
        if sin_tildes.startswith(prefijo):
            return s[len(prefijo):].strip()

    return s


def norm_provincia(val):
    """
    Clave canónica de una provincia.

    Mapbox no es consistente en el nombre de región que devuelve: para unos
    puntos responde "Provincia de Tungurahua" y para otros "Tungurahua", y las
    tildes tampoco son estables. Como esta cadena es la clave con la que se
    particionan las visitas y se dimensiona la flota, dos variantes del mismo
    nombre se convertían en dos provincias distintas, cada una con su mínimo
    de un mercadista: flota duplicada y carga fragmentada entre ambos.

    Normaliza a mayúsculas sin tildes, sin el prefijo "Provincia de" y sin
    espacios repetidos, de modo que todas las variantes colapsen en una clave.
    """
    if val is None:
        return ""

    s = unicodedata.normalize("NFD", str(val).strip())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = " ".join(s.upper().split())

    for prefijo in _PREFIJOS_PROVINCIA:
        if s.startswith(prefijo):
            s = s[len(prefijo):].strip()
            break

    return s
