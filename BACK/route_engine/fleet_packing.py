"""
Dimensionado de la flota por CAPACIDAD MENSUAL (bin packing).

Por qué existe este módulo
--------------------------
El motor repartía el trabajo semana a semana y día a día, y decidía el tamaño
de la flota con `ceil(carga_zona / 9600)` zona por zona. Eso arrastraba dos
pérdidas grandes:

1. El redondeo por zona. Con 26 zonas, cada una desperdicia hasta media plaza:
   hasta 13 mercaderistas de capacidad regalada sobre una flota de 78.

2. El reparto miope. Al llenar semana a semana sin mirar el mes completo, los
   primeros mercaderistas adoptaban puntos que no podrían atender en las
   semanas siguientes, y las visitas sobrantes ya no podía recogerlas nadie
   (un punto pertenece a un solo mercaderista). El resultado medido: 74-84% de
   ocupación y ~185.000 min sin colocar.

Lo que hace
-----------
Trata el problema por lo que es: meter la carga MENSUAL de cada punto en cajas
de 9.600 min, que es lo que la empresa contrata. Usa first-fit decreasing con
best-fit —la heurística estándar de bin packing— y una restricción de
compatibilidad geográfica: todos los puntos de una caja deben caber en un
círculo de 2 x RADIO_ZONA_KM de diámetro, igual que las zonas de antes.

Cada caja resultante ES la zona de trabajo de un mercaderista. Al construirse
así ya no hay redondeo por zona: la zona no se define antes de saber la carga
sino a la vez, y sale exactamente una persona por zona.

El plan real de la empresa hace justo esto: reparte minutos mensuales por
persona y no programa el día. Aquí sí se programa el día después, pero partiendo
de un reparto mensual que sí cabe.
"""

from __future__ import annotations

import math
import os
from collections import defaultdict

from route_engine.config import (
    cuota_dia,
    RADIO_ZONA_KM,
    carga_jornada,
    cuota_mes,
    travel_estimado_por_visita_plan_min,
)
from route_engine.geo import haversine_km
from route_engine.load_grouping import clave_grupo, normalizar_tipo_carga

# Diámetro máximo de la zona de un mercaderista. Mismo invariante que la
# clusterización anterior: dos puntos de la misma zona nunca distan más de
# 2 x RADIO_ZONA_KM.
DIAMETRO_MAX_KM = 2 * RADIO_ZONA_KM

# Diámetro ampliado que se permite SOLO para fundir cajas que se quedaron a
# medio llenar (ver `_consolidar_cajas_flojas`). Por debajo de este umbral de
# ocupación una caja se considera floja y es candidata a fundirse.
DIAMETRO_CONSOLIDACION_KM = float(
    os.environ.get("DIAMETRO_CONSOLIDACION_KM", str(2.5 * DIAMETRO_MAX_KM))  # 150 km
)
UMBRAL_CAJA_FLOJA = float(os.environ.get("UMBRAL_CAJA_FLOJA", "0.75"))


def _punto_key(inst):
    """Fila del Excel: la unidad indivisible (ver AsignacionState.get_punto_key)."""
    return inst.get("punto_key", inst.get("idx"))


def _coords(inst):
    try:
        lat, lon = float(inst.get("lat") or 0), float(inst.get("lon") or 0)
    except (TypeError, ValueError):
        return None
    if lat == 0 and lon == 0:
        return None
    return (lat, lon)


class _Caja:
    """Un mercaderista: su carga mensual comprometida y sus coordenadas."""

    __slots__ = ("carga", "coords", "puntos", "nombre", "zona", "dias", "grupo")

    def __init__(self, nombre=None, zona=None, carga=0.0, coords=None, grupo=None):
        # `nombre`/`zona` vienen informados cuando la caja es un mercaderista
        # que YA existe y al que solo se le está aprovechando el hueco libre.
        self.nombre = nombre
        self.zona = zona
        self.carga = float(carga or 0)
        self.coords = list(coords or [])
        self.puntos = []
        # Minutos ya comprometidos en cada día de una semana tipo por los puntos
        # de día fijo (frecuencias 8/12/16/20). El mes puede cuadrar y la semana
        # ser imposible: dos puntos de frecuencia 20 ocupan los cinco días.
        self.dias = {}
        # Partición de negocio a la que pertenece esta persona (cadena o
        # ciudad, según el tipo de carga). None = sin barrera.
        self.grupo = grupo

    def hueco(self, capacidad):
        return capacidad - self.carga

    def admite_geografia(self, coords_nuevas):
        """
        ¿Siguen cabiendo todos los puntos en un círculo de DIAMETRO_MAX_KM?

        Se comprueba contra todas las coordenadas ya presentes, no contra un
        centroide: un centroide se desplaza al añadir puntos y deja pasar
        cadenas A→B→C que acaban abarcando el triple del radio.
        """
        for nueva in coords_nuevas:
            for ya in self.coords:
                if haversine_km(nueva[0], nueva[1], ya[0], ya[1]) > DIAMETRO_MAX_KM:
                    return False
        return True

    def distancia_a(self, coords_nuevas):
        """Distancia mínima entre el punto candidato y lo que ya tiene la caja."""
        if not self.coords or not coords_nuevas:
            return 0.0
        return min(
            haversine_km(n[0], n[1], y[0], y[1])
            for n in coords_nuevas
            for y in self.coords
        )

    def agregar(self, pk, carga, coords_nuevas, patron_dias=None, minutos_visita=0.0):
        self.carga += carga
        self.puntos.append(pk)
        for c in coords_nuevas:
            if c not in self.coords:
                self.coords.append(c)
        for d in (patron_dias or ()):
            self.dias[d] = self.dias.get(d, 0.0) + float(minutos_visita or 0)

    def admite_grupo(self, grupo):
        """
        ¿Puede esta persona atender un punto de esa partición?

        Es la regla estricta del tipo de carga: una caja que ya empezó con la
        cadena TRADICIONAL no admite puntos de ninguna otra, aunque estén al
        lado y le sobre mes.
        """
        if grupo is None or self.grupo is None:
            return True
        return self.grupo == grupo

    def admite_dias(self, patrones, minutos_visita, tope_dia):
        """
        ¿Existe algún patrón de días fijos con el que este punto quepa?

        Devuelve el mejor patrón, o None si con ninguno cabe. Sin esta
        comprobación el planificador solo miraba minutos al mes: a una persona
        le podían caer tres puntos que exigen los mismos días, el mes le
        cuadraba y la semana era imposible, y ese exceso terminaba en
        pendientes.
        """
        if not patrones:
            return ()
        mejor, mejor_pico = None, None
        for patron in patrones:
            pico = max(
                self.dias.get(d, 0.0) + float(minutos_visita or 0) for d in patron
            )
            if pico > tope_dia:
                continue
            if mejor_pico is None or pico < mejor_pico:
                mejor, mejor_pico = patron, pico
        return mejor


def _consolidar_cajas_flojas(cajas, capacidad, carga_punto, coords_punto):
    """
    Funde las cajas que quedaron a medio llenar, ampliando el diámetro.

    Una comarca con 600 min de trabajo al mes necesita igualmente una persona si
    nadie más puede llegar hasta ella: con 26 zonas eso regalaba hasta 13 plazas.
    La planificación real de la empresa resuelve esto exactamente así —13 de sus
    78 mercaderistas cubren radios de más de 60 km, y el mayor llega a 323 km—,
    de modo que ampliar el alcance para las cajas flojas no es una licencia: es
    lo que se hace de verdad.

    Solo se tocan las cajas por debajo del umbral, y solo se funden entre ellas:
    una caja bien llena nunca se ensancha.
    """
    if DIAMETRO_CONSOLIDACION_KM <= DIAMETRO_MAX_KM:
        return cajas

    umbral = capacidad * UMBRAL_CAJA_FLOJA
    flojas = sorted((c for c in cajas if c.carga < umbral), key=lambda c: c.carga)
    llenas = [c for c in cajas if c.carga >= umbral]
    if len(flojas) < 2:
        return cajas

    absorbidas = set()
    for i, origen in enumerate(flojas):
        if id(origen) in absorbidas:
            continue
        for destino in flojas[i + 1:]:
            if id(destino) in absorbidas or id(destino) == id(origen):
                continue
            if destino.carga + origen.carga > capacidad:
                continue
            # Nunca se funden dos personas de particiones distintas: eso
            # rompería la regla del tipo de carga (una cadena por mercaderista).
            if destino.grupo != origen.grupo:
                continue
            if not _compatible_ampliado(destino, origen):
                continue
            for pk in origen.puntos:
                destino.agregar(pk, carga_punto[pk], [coords_punto[pk]])
            absorbidas.add(id(origen))
            break

    return llenas + [c for c in flojas if id(c) not in absorbidas]


def _compatible_ampliado(destino, origen):
    """¿Caben las dos cajas juntas dentro del diámetro ampliado?"""
    for a in origen.coords:
        for b in destino.coords:
            if haversine_km(a[0], a[1], b[0], b[1]) > DIAMETRO_CONSOLIDACION_KM:
                return False
    return True


def planificar_flota_por_capacidad(
    visit_instances, capacidad_mes=None, numero_inicial=1, cajas_previas=None,
    tipo_carga=None,
):
    """
    Reparte los puntos entre mercaderistas por capacidad mensual.

    Devuelve (plan, propiedad, desglose):
      plan       : list[(merc_name, zona_key)] — una zona por mercaderista
      propiedad  : dict {punto_key: merc_name} — regla «un punto, un mercaderista»
      desglose   : list[(zona_key, carga_min, n_puntos)] para el log

    Anota además `inst["zona"]` con la caja asignada, de modo que el resto del
    motor (que particiona `remaining_by_prov` por zona) vea exactamente los
    puntos de cada mercaderista.
    """
    capacidad = float(capacidad_mes or cuota_mes())
    tipo_carga_norm = normalizar_tipo_carga(tipo_carga)

    # --- 1) Carga mensual por punto (fila) ---------------------------------
    carga_punto = defaultdict(float)
    coords_punto = {}
    afinidad_punto = {}
    insts_punto = defaultdict(list)
    minutos_visita_punto = {}
    frecuencia_punto = {}
    grupo_punto = {}
    sin_ubicar = []

    for inst in visit_instances:
        pk = _punto_key(inst)
        c = _coords(inst)
        tiempo = float(inst.get("tiempo") or 0)
        if c is None or tiempo <= 0:
            sin_ubicar.append(inst)
            continue
        # La reserva de viaje solo cuenta si el desplazamiento consume cuota
        # (ver `jornada_incluye_viaje`). Con el modelo de la empresa es 0.
        carga_punto[pk] += carga_jornada(tiempo, travel_estimado_por_visita_plan_min())
        coords_punto.setdefault(pk, c)
        afinidad_punto.setdefault(pk, inst.get("group_key") or pk)
        insts_punto[pk].append(inst)
        grupo_punto.setdefault(pk, clave_grupo(inst, tipo_carga_norm))
        if inst.get("fixed_mercadista"):
            minutos_visita_punto[pk] = tiempo
            frecuencia_punto[pk] = inst.get("frecuencia_mes")

    if not carga_punto:
        return [], {}, []

    # Patrones de días posibles de cada punto de día fijo. El planificador los
    # usa para no cargar a una persona con puntos cuyos días chocan.
    from route_engine.day_balance import patrones_de_frecuencia

    patrones_punto = {
        pk: patrones_de_frecuencia(frecuencia_punto[pk]) for pk in frecuencia_punto
    }
    tope_dia_plan = cuota_dia()

    # --- 2) Orden: first-fit decreasing, tiendas juntas --------------------
    # Se ordena por carga del LOCAL y, dentro de él, por carga de la fila. Así
    # las filas de una misma tienda se procesan seguidas y tienden a caer en el
    # mismo mercaderista sin necesidad de forzarlo (cuando no caben, se separan,
    # que es exactamente lo que hace el plan real con 48 de sus 362 locales).
    carga_local = defaultdict(float)
    for pk, carga in carga_punto.items():
        carga_local[afinidad_punto[pk]] += carga

    orden = sorted(
        carga_punto.keys(),
        key=lambda pk: (-carga_local[afinidad_punto[pk]], afinidad_punto[pk], -carga_punto[pk]),
    )

    # --- 3) Empaquetado: sembrar y llenar ----------------------------------
    #
    # Se abre una caja con el punto más pesado que quede libre y se llena con
    # los puntos compatibles más grandes que quepan, hasta agotar la capacidad.
    #
    # El best-fit clásico (recorrer los puntos y buscarles caja) no sirve aquí:
    # la restricción geográfica hace que un punto solo pueda entrar en las cajas
    # de su vecindad, así que las cajas se quedaban a medio llenar y salían 92
    # para un mínimo teórico de 78 (84% de aprovechamiento). Sembrando y
    # llenando, cada caja se completa con su propia vecindad antes de abrir la
    # siguiente.
    libres = set(orden)
    cajas: list[_Caja] = []

    # --- 3.a) Primero, el hueco libre de los mercaderistas que YA existen ----
    #
    # Abrir plazas nuevas mientras la plantilla actual tiene mes libre es cómo
    # el motor acababa con 86 personas al 86% de ocupación: cada visita que no
    # cabía en SU día generaba plaza en vez de buscar quién tenía sitio. Se
    # rellena lo existente antes de contratar.
    # `grupo` es imprescindible con reparto por cadena/canal/multicanal: una
    # caja sin grupo admite cualquier partición, y el hueco libre de un
    # mercaderista de ROSADO se llenaba con puntos de TIA.
    previas = [
        _Caja(nombre=c["merc"], zona=c["zona"], carga=c.get("carga", 0.0),
              coords=c.get("coords") or [], grupo=c.get("grupo"))
        for c in (cajas_previas or [])
    ]
    if previas:
        for pk in sorted(libres, key=lambda k: -carga_punto[k]):
            carga = carga_punto[pk]
            coords_nuevas = [coords_punto[pk]]
            mejor, mejor_clave = None, None
            mejor_patron = None
            for caja in previas:
                hueco = caja.hueco(capacidad)
                if hueco < carga or not caja.admite_geografia(coords_nuevas):
                    continue
                if not caja.admite_grupo(grupo_punto.get(pk)):
                    continue
                patron = caja.admite_dias(
                    patrones_punto.get(pk), minutos_visita_punto.get(pk, 0.0), tope_dia_plan
                )
                if patron is None:
                    continue
                clave = (hueco - carga, caja.distancia_a(coords_nuevas))
                if mejor_clave is None or clave < mejor_clave:
                    mejor, mejor_clave, mejor_patron = caja, clave, patron
            if mejor is not None:
                mejor.agregar(
                    pk, carga, coords_nuevas,
                    patron_dias=mejor_patron,
                    minutos_visita=minutos_visita_punto.get(pk, 0.0),
                )
                libres.discard(pk)
        cajas.extend(c for c in previas if c.puntos)

    # --- 3.b) Lo que no cupo abre plazas nuevas -----------------------------
    for pk_semilla in orden:
        if pk_semilla not in libres:
            continue

        caja = _Caja(grupo=grupo_punto.get(pk_semilla))
        cajas.append(caja)
        libres.discard(pk_semilla)
        caja.agregar(
            pk_semilla, carga_punto[pk_semilla], [coords_punto[pk_semilla]],
            patron_dias=(patrones_punto.get(pk_semilla) or [()])[0],
            minutos_visita=minutos_visita_punto.get(pk_semilla, 0.0),
        )

        # Un punto que por sí solo supera la capacidad mensual se queda con la
        # caja entera; el reparto por días lo trocea después entre jornadas.
        if caja.carga >= capacidad:
            continue

        while True:
            hueco = caja.hueco(capacidad)
            if hueco <= 0:
                break

            mejor, mejor_clave, mejor_patron = None, None, None
            for pk in libres:
                carga = carga_punto[pk]
                if carga > hueco:
                    continue
                coords_nuevas = [coords_punto[pk]]
                if not caja.admite_geografia(coords_nuevas):
                    continue
                if not caja.admite_grupo(grupo_punto.get(pk)):
                    continue
                patron = caja.admite_dias(
                    patrones_punto.get(pk), minutos_visita_punto.get(pk, 0.0), tope_dia_plan
                )
                if patron is None:
                    continue
                # Primero las filas del mismo local (misma tienda, mismo
                # mercaderista siempre que quepa). Después POR CERCANÍA, en
                # bandas de 5 km, y dentro de cada banda lo más grande que
                # quepa. Antes mandaba el tamaño y la distancia solo
                # desempataba: la zona se llevaba el punto más pesado de su
                # radio aunque estuviera a 50 km, y salían rutas con paradas a
                # 147 km entre sí. Con bandas se llena igual —misma flota— y la
                # dispersión mediana baja de 44,5 a 26 km.
                misma_tienda = 0 if afinidad_punto[pk] in {
                    afinidad_punto[p] for p in caja.puntos
                } else 1
                banda = round(caja.distancia_a(coords_nuevas) / 5)
                clave = (misma_tienda, banda, -carga)
                if mejor_clave is None or clave < mejor_clave:
                    mejor, mejor_clave, mejor_patron = pk, clave, patron

            if mejor is None:
                break
            libres.discard(mejor)
            caja.agregar(
                mejor, carga_punto[mejor], [coords_punto[mejor]],
                patron_dias=mejor_patron,
                minutos_visita=minutos_visita_punto.get(mejor, 0.0),
            )

    nuevas = [c for c in cajas if c.nombre is None]
    nuevas = _consolidar_cajas_flojas(nuevas, capacidad, carga_punto, coords_punto)
    cajas = [c for c in cajas if c.nombre is not None] + nuevas

    propiedad = {}

    # --- 4) Nombrar, anotar zona y devolver --------------------------------
    cajas.sort(key=lambda c: (c.nombre is None, -c.carga))

    plan = []
    desglose = []
    zona_de_punto = {}
    siguiente = int(numero_inicial)
    for caja in cajas:
        if caja.nombre is not None:
            merc, zona = caja.nombre, caja.zona
        else:
            merc = f"Mercadista {siguiente:02d}"
            zona = f"Z{siguiente:03d}"
            siguiente += 1
            plan.append((merc, zona))
        desglose.append((zona, caja.carga, len(caja.puntos)))
        for pk in caja.puntos:
            propiedad[pk] = merc
            zona_de_punto[pk] = zona

    for pk, insts in insts_punto.items():
        zona = zona_de_punto.get(pk)
        if zona:
            for inst in insts:
                inst["zona"] = zona

    # Las visitas sin coordenada o sin tiempo no forman grupo geográfico; se
    # dejan en una clave aparte que el refuerzo de flota ignora, igual que
    # hacía la clusterización anterior.
    for inst in sin_ubicar:
        inst["zona"] = "SIN_UBICAR"

    return plan, propiedad, desglose


def resumen_plan(desglose, capacidad_mes=None):
    """Texto de una línea con el aprovechamiento del plan, para el log."""
    capacidad = float(capacidad_mes or cuota_mes())
    if not desglose:
        return "sin plan"
    total = sum(c for _z, c, _n in desglose)
    n = len(desglose)
    ocup = total / (n * capacidad) * 100 if n else 0
    piso = math.ceil(total / capacidad)
    return (
        f"{n} mercadista(s) | {total:.0f} min/mes | ocupación media {ocup:.1f}% | "
        f"mínimo teórico {piso}"
    )
