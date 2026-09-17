"""
Kilómetros reales por carretera entre pares de puntos (Mapbox Matrix API).

El motor decidía con línea recta × factor y solo al final medía la carretera
de verdad: jornadas aprobadas con la estimación se desbordaban al medirlas.
Aquí se precargan los pares que el motor va a encadenar —los puntos de un mismo
mercaderista— y `geo.km_por_carretera` los consulta antes de estimar.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict, deque

import requests

from route_engine.config import MAPBOX_ACCESS_TOKEN

# MATRIZ_CARRETERA=0 deja al motor con la estimación de siempre.
MATRIZ_ACTIVA = os.environ.get("MATRIZ_CARRETERA", "1") != "0"

_MAX_COORDS = 25          # tope de la Matrix API con el perfil driving
_BLOQUE = 12              # dos bloques caben juntos en una petición
_DECIMALES = 5            # ~1 m: la misma tienda es la misma clave
_PETICIONES_POR_MIN = 55  # el límite de Mapbox es 60
_SIN_RUTA = -1.0

_ARCHIVO = os.environ.get("MATRIZ_CARRETERA_CACHE") or os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache", "matriz_carretera.json"
)

_km: dict = {}            # (lat1, lon1, lat2, lon2) -> km dirigido
_cargada = False
_lock = threading.RLock()
_peticiones: deque = deque()


def _r(valor) -> float:
    return round(float(valor), _DECIMALES)


def _cargar() -> None:
    global _cargada
    with _lock:
        if _cargada:
            return
        _cargada = True
        try:
            with open(_ARCHIVO, encoding="utf-8") as fh:
                for clave, km in json.load(fh).items():
                    _km[tuple(float(x) for x in clave.split(","))] = float(km)
        except (OSError, ValueError):
            pass


def _guardar() -> None:
    with _lock:
        try:
            os.makedirs(os.path.dirname(_ARCHIVO), exist_ok=True)
            temporal = f"{_ARCHIVO}.tmp-{os.getpid()}"
            with open(temporal, "w", encoding="utf-8") as fh:
                json.dump({",".join(map(str, k)): v for k, v in _km.items()}, fh)
            os.replace(temporal, _ARCHIVO)
        except OSError:
            pass


def km_real(lat1, lon1, lat2, lon2):
    """Kilómetros por carretera de ese par si están precargados; si no, None."""
    if not MATRIZ_ACTIVA:
        return None
    if not _cargada:
        _cargar()
    try:
        km = _km.get((_r(lat1), _r(lon1), _r(lat2), _r(lon2)))
    except (TypeError, ValueError):
        return None
    return km if km is not None and km >= 0 else None


def _esperar_turno() -> None:
    """Respeta el límite por minuto de Mapbox sin frenar las ráfagas cortas."""
    ahora = time.monotonic()
    while _peticiones and ahora - _peticiones[0] > 60:
        _peticiones.popleft()
    if len(_peticiones) >= _PETICIONES_POR_MIN:
        time.sleep(max(0.0, 60 - (ahora - _peticiones[0])) + 0.2)
    _peticiones.append(time.monotonic())


def _pedir_matriz(lote):
    """Matriz de metros [origen][destino] de esas coordenadas, o None."""
    puntos = ";".join(f"{lon},{lat}" for lat, lon in lote)
    url = f"https://api.mapbox.com/directions-matrix/v1/mapbox/driving/{puntos}"
    params = {"access_token": MAPBOX_ACCESS_TOKEN, "annotations": "distance"}
    for intento in range(2):
        _esperar_turno()
        try:
            r = requests.get(url, params=params, timeout=30)
            if r.status_code == 429 and intento == 0:
                time.sleep(12)
                continue
            r.raise_for_status()
            data = r.json()
            return data.get("distances") if data.get("code") == "Ok" else None
        except Exception:
            return None
    return None


def _lotes(coords):
    """Peticiones de ≤25 coordenadas que cubren todos los pares del grupo."""
    if len(coords) <= _MAX_COORDS:
        return [coords]
    bloques = [coords[i:i + _BLOQUE] for i in range(0, len(coords), _BLOQUE)]
    return [bloques[i] + bloques[j] for i in range(len(bloques)) for j in range(i + 1, len(bloques))]


def precargar(grupos) -> int:
    """Trae de Mapbox los pares que falten dentro de cada grupo de coordenadas.

    Devuelve cuántos pares nuevos se guardaron. Lo ya consultado vive en disco,
    así que reprocesar el mismo rutero no vuelve a salir a la red.
    """
    if not MATRIZ_ACTIVA or not MAPBOX_ACCESS_TOKEN or MAPBOX_ACCESS_TOKEN == "pk.YOUR_MAPBOX_ACCESS_TOKEN":
        return 0
    _cargar()
    nuevos = peticiones = 0
    for grupo in grupos:
        try:
            coords = sorted({(_r(la), _r(lo)) for la, lo in grupo})
        except (TypeError, ValueError):
            continue
        if len(coords) < 2:
            continue
        for lote in _lotes(coords):
            if all((a + b) in _km for a in lote for b in lote if a != b):
                continue
            distancias = _pedir_matriz(lote)
            peticiones += 1
            if not distancias:
                continue
            with _lock:
                for i, a in enumerate(lote):
                    for j, b in enumerate(lote):
                        if a == b or (a + b) in _km:
                            continue
                        metros = distancias[i][j]
                        _km[a + b] = round(metros / 1000.0, 3) if metros is not None else _SIN_RUTA
                        nuevos += 1
    if nuevos:
        _guardar()
        print(f"      -> Matriz por carretera: {nuevos} par(es) nuevos en {peticiones} petición(es)")
    return nuevos


def precargar_por_mercadista(instancias, propiedad, clave_de) -> int:
    """Precarga los pares entre los puntos que comparte cada mercaderista."""
    if not MATRIZ_ACTIVA:
        return 0
    grupos = defaultdict(set)
    for inst in instancias or []:
        merc = (propiedad or {}).get(clave_de(inst))
        if not merc:
            continue
        try:
            grupos[merc].add((float(inst["lat"]), float(inst["lon"])))
        except (TypeError, ValueError, KeyError):
            continue
    return precargar(grupos.values())
