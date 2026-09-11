import os
from contextlib import contextmanager

# Token de Mapbox. DEBE venir de la variable de entorno (cargada desde BACK/.env
# en app.py antes que cualquier import). Si falta, las funciones que lo usan
# devuelven None y el sistema degrada (estimaciones haversine en vez de
# directions/geocoding reales).
MAPBOX_ACCESS_TOKEN = os.environ.get("MAPBOX_ACCESS_TOKEN", "")

# Factor de corrección haversine → distancia por carretera.
# La distancia entre dos puntos calculada como línea recta (haversine) es
# siempre menor que la ruta real por calles (rodeos, manzanas, ríos, sentidos
# prohibidos). En ciudades el ratio típico está entre 1.3 y 1.6; en zonas
# rurales se acerca a 1.1. Multiplicamos haversine por este factor para
# acercarnos a la distancia real sin llamar a Mapbox Directions por cada par.
# Se puede ajustar vía variable de entorno DISTANCE_FACTOR_CARRETERA.
DISTANCE_FACTOR_CARRETERA = float(os.environ.get("DISTANCE_FACTOR_CARRETERA", "1.4"))

# ---------------------------------------------------------------------------
# UNIDAD DE MEDIDA DE LA JORNADA
# ---------------------------------------------------------------------------
# La jornada de 8 h se llena con el tiempo dentro del punto de venta ("Tiempo
# Servicio"). La pregunta de negocio es si el desplazamiento entre puntos
# ("Tiempo entre sucursal") consume además esa misma cuota.
#
# El modelo activo decide cuál de los dos se aplica:
#
#   False ("sin tiempo de desplazamiento") — modelo de la planificación real de
#       la empresa. La cuota de 480 min/día y 9600 min/mes es de SERVICIO. El
#       desplazamiento se calcula, se escribe y se reporta, pero no descuenta
#       de la cuota. Es como está dimensionada la plantilla de verdad: en
#       "minoristas_ubicaciones prueba PLANTILLA - MODERNA" 73 de los 78
#       mercaderistas están exactamente al 100,0% de 9600 min de servicio.
#
#   True ("con tiempo de desplazamiento") — el viaje descuenta de la cuota.
#       Más conservador y más realista como horario, pero cambia el
#       dimensionado: con esta demanda (743.280 min de servicio) el mínimo
#       matemático pasa de 78 a 84 mercaderistas, porque el viaje se lleva un
#       7-8% de cada jornada. Ningún reparto puede bajar de ahí.
#
# Los dos modelos son legítimos; lo que no se puede es pedir el headcount de
# uno con la contabilidad del otro. Al cambiar de modelo el número de
# mercaderistas cambia, y es esperado.
#
# El modelo se elige POR EJECUCIÓN desde la pantalla de carga (procesar_minoristas
# recibe `incluir_tiempo_desplazamiento`). La variable de entorno solo fija el
# valor por defecto para las ejecuciones que no lo especifican.
# ---------------------------------------------------------------------------
JORNADA_INCLUYE_VIAJE_POR_DEFECTO = os.environ.get(
    "JORNADA_INCLUYE_VIAJE", "false"
).strip().lower() in ("1", "true", "yes", "si", "sí")

# Modelo activo en este momento. Lo cambia `jornada_incluye_viaje_temporal()`
# mientras dura una ejecución del motor; fuera de ella vale el valor por
# defecto. Es estado de módulo (no por hilo) a propósito: el procesamiento
# corre en un único hilo worker a la vez (ver excel_processing_service), y los
# hilos de request que leen métricas resuelven el modelo del dataset que están
# leyendo, no el global (ver `carga_jornada(..., incluye_viaje=...)`).
_jornada_incluye_viaje = JORNADA_INCLUYE_VIAJE_POR_DEFECTO


def jornada_incluye_viaje() -> bool:
    """True si el desplazamiento descuenta de la cuota en la ejecución actual."""
    return _jornada_incluye_viaje


def set_jornada_incluye_viaje(valor) -> None:
    """
    Fija el modelo de jornada. `None` restaura el valor por defecto del entorno.

    Preferir `jornada_incluye_viaje_temporal()`, que garantiza la restauración
    aunque el procesamiento falle o se cancele.
    """
    global _jornada_incluye_viaje
    _jornada_incluye_viaje = (
        JORNADA_INCLUYE_VIAJE_POR_DEFECTO if valor is None else bool(valor)
    )


@contextmanager
def jornada_incluye_viaje_temporal(valor):
    """
    Aplica un modelo de jornada durante un bloque y restaura el anterior al
    salir, pase lo que pase. `None` deja el modelo que ya estuviera activo.

    Restaurar importa: si una ejecución "con desplazamiento" deja el flag
    encendido, el siguiente procesamiento que no lo especifique dimensionaría
    la flota con un modelo que nadie pidió.
    """
    global _jornada_incluye_viaje
    anterior = _jornada_incluye_viaje
    if valor is not None:
        _jornada_incluye_viaje = bool(valor)
    try:
        yield _jornada_incluye_viaje
    finally:
        _jornada_incluye_viaje = anterior


# ---------------------------------------------------------------------------
# CUOTA DE JORNADA
# ---------------------------------------------------------------------------
# Todo el dimensionado sale de UN número: los minutos de jornada diaria. La
# semana son 5 días y el mes 4 semanas, así que basta cambiar el diario para
# que semana y mes acompañen sin poder desincronizarse.
#
# Presets ofrecidos en la pantalla de carga:
#     480 min/día -> 2.400/semana ->  9.600/mes   (jornada completa)
#     400 min/día -> 2.000/semana ->  8.000/mes   (jornada reducida)
#
# Es estado por ejecución, igual que el modelo de desplazamiento: se fija con
# `cuota_dia_temporal()` mientras dura un procesamiento.
CUOTA_DIA_POR_DEFECTO = int(os.environ.get("MAX_TIEMPO_DIA_MIN", "480"))

_cuota_dia = CUOTA_DIA_POR_DEFECTO

DIAS_SEMANA_LABORALES = 5
SEMANAS_MES = 4


def cuota_dia() -> int:
    """Minutos de jornada que se pueden consumir en un día."""
    return _cuota_dia


def cuota_semana() -> int:
    """Minutos de jornada por semana (5 días)."""
    return _cuota_dia * DIAS_SEMANA_LABORALES


def cuota_mes() -> int:
    """Minutos de jornada por mes (20 días). Es el límite duro del modelo."""
    return cuota_semana() * SEMANAS_MES


def set_cuota_dia(minutos) -> None:
    """Fija la cuota diaria. `None` restaura el valor por defecto."""
    global _cuota_dia
    if minutos is None:
        _cuota_dia = CUOTA_DIA_POR_DEFECTO
        return
    valor = int(minutos)
    if valor <= 0:
        raise ValueError(f"Cuota diaria inválida: {minutos}")
    _cuota_dia = valor


@contextmanager
def cuota_dia_temporal(minutos):
    """Aplica una cuota diaria durante un bloque y restaura la anterior al salir."""
    global _cuota_dia
    anterior = _cuota_dia
    if minutos is not None:
        set_cuota_dia(minutos)
    try:
        yield _cuota_dia
    finally:
        _cuota_dia = anterior


def viaje_en_agenda(minutos, incluye_viaje=None):
    """
    Minutos de desplazamiento que se reflejan en la AGENDA y en el Excel.

    Con "sin tiempo de desplazamiento" la respuesta es 0: las visitas se
    encadenan una detrás de otra y la columna "Tiempo entre sucursal (min)"
    queda a cero, de modo que ni el horario ni el tiempo total de trabajo
    incluyen el trayecto. El motor sigue calculando el desplazamiento real por
    dentro —lo necesita para ordenar la ruta y para rechazar saltos absurdos
    entre dos puntos—, pero no lo reporta.

    Los kilómetros NO se tocan: son distancia, no jornada.
    """
    incluye = _jornada_incluye_viaje if incluye_viaje is None else bool(incluye_viaje)
    if not incluye:
        return 0.0
    try:
        return float(minutos or 0)
    except (TypeError, ValueError):
        return 0.0


def carga_jornada(servicio, viaje=0.0, incluye_viaje=None):
    """
    Minutos que estas visitas consumen de la cuota del mercadista.

    Único punto donde se decide si el desplazamiento cuenta. Usar esta función
    en vez de sumar a mano evita que una parte del motor dimensione la flota
    con un criterio y otra valide con el contrario, que es lo que hacía que los
    objetivos resultaran inalcanzables.

    `incluye_viaje` permite forzar el modelo en vez de usar el activo. Lo usan
    los lectores de un Excel ya generado (dashboard), que deben medir con el
    modelo con el que se generó ESE archivo y no con el de la ejecución en
    curso.

    Acepta escalares o Series/arrays de pandas: el postproceso y el dashboard
    la aplican sobre columnas enteras.
    """
    incluye = _jornada_incluye_viaje if incluye_viaje is None else bool(incluye_viaje)
    if not incluye:
        viaje = 0.0
    try:
        return float(servicio or 0) + float(viaje or 0)
    except (TypeError, ValueError):
        # Series/array: la aritmética vectorizada ya hace lo correcto.
        return servicio + viaje

# ---------------------------------------------------------------------------
# TECHO DIARIO
# ---------------------------------------------------------------------------
# Hay dos regímenes, y cuál aplica lo decide el modelo de desplazamiento:
#
#   "Sin tiempo de desplazamiento" -> TECHO RÍGIDO en la cuota diaria.
#       Ningún día pasa de los minutos contratados. Es lo que pide negocio:
#       si la jornada son 480 min de tienda, un día no puede tener 560.
#       Única excepción, ya contemplada por el confirmador: una visita que por
#       sí sola dura más que la jornada (p. ej. 520 min) se acepta si el día
#       está vacío, porque si no ese punto no se podría atender nunca.
#
#   "Con tiempo de desplazamiento" -> JORNADA FLEXIBLE (125% del diario).
#       Aquí el techo diario tiene que absorber la variabilidad del trayecto,
#       que no se conoce hasta armar la ruta. Con un techo rígido cada jornada
#       acaba con un hueco de 100-150 min donde ya no cabe ninguna visita
#       (duran 60-540 min) y la cobertura se estanca; el mes sigue siendo el
#       límite duro, así que un martes de 560 se compensa con otro día de 400.
FACTOR_JORNADA_FLEXIBLE = float(os.environ.get("FACTOR_JORNADA_FLEXIBLE", "1.25"))


def max_dia_flex() -> int:
    """Techo de minutos que puede alcanzar un día concreto."""
    override = os.environ.get("MAX_TIEMPO_DIA_FLEX_MIN")
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    if jornada_incluye_viaje():
        return int(cuota_dia() * FACTOR_JORNADA_FLEXIBLE)
    return cuota_dia()


def max_semana_flex() -> int:
    """
    Techo semanal coherente con el techo diario.

    El reparto mensual no cae uniforme en las 4 semanas (un punto de frecuencia
    1 concentra su carga en una sola), así que topar la semana en 5 x cuota
    dejaba fuera trabajo que el mes sí admitía. El límite que de verdad protege
    al mercaderista es el mensual.
    """
    return max_dia_flex() * DIAS_SEMANA_LABORALES


# Objetivo de llenado (90% de la cuota). No es un tope: es el punto a partir del
# cual se considera que el día/semana/mes está suficientemente aprovechado y no
# vale la pena forzar más visitas.
FACTOR_LLENADO_OBJETIVO = 0.90


def min_dia() -> int:
    return int(cuota_dia() * FACTOR_LLENADO_OBJETIVO)


def min_semana() -> int:
    return min_dia() * DIAS_SEMANA_LABORALES


def min_mes() -> int:
    return min_semana() * SEMANAS_MES


def max_servicio_dia() -> int:
    """
    Tope de SERVICIO puro por día.

    No es un objetivo a alcanzar (eso lo gobiernan las cuotas de arriba), solo
    una red de seguridad frente a datos de entrada absurdos: ninguna jornada
    puede tener más minutos dentro de tiendas que la jornada completa.
    """
    return cuota_dia()


def max_servicio_overflow() -> int:
    return max_servicio_dia() + 5

# ---------------------------------------------------------------------------
# MODELO DE DESPLAZAMIENTO
# ---------------------------------------------------------------------------
# Un único modelo para todo el motor. Antes había dos que no coincidían: el
# pre-filtro estimaba a 25 km/h continuos y el cálculo real cobraba
# ceil(km/2)×15, con un mínimo de 15 min aunque el siguiente local estuviera
# cruzando la calle. El pre-filtro dejaba pasar candidatos que el cálculo real
# rechazaba acto seguido, y el mínimo de 15 min inflaba la jornada hasta 15x.
#
# Modelo actual: distancia por carretera / velocidad del tramo + un fijo de
# acceso (estacionar, encontrar el local, entrar). La velocidad sube con la
# distancia porque un tramo largo usa vías rápidas y uno corto no sale del
# casco urbano.
TRAVEL_ACCESO_FIJO_MIN = float(os.environ.get("TRAVEL_ACCESO_FIJO_MIN", "5"))

# (hasta_km, velocidad_kmh). El último tramo aplica de ahí en adelante.
VELOCIDAD_POR_TRAMO_KMH = (
    (2.0, 18.0),     # casco urbano denso: semáforos, tráfico
    (10.0, 25.0),    # urbano
    (30.0, 40.0),    # periurbano / interurbano corto
    (float("inf"), 60.0),  # carretera
)

# Estimación media de minutos de desplazamiento reservados POR VISITA al
# dimensionar la flota, antes de conocer las rutas reales. No es un tope
# operativo, es un colchón. Coherente con el modelo de arriba: ~5 min de
# acceso + ~5 min de trayecto urbano corto.
#
# Solo se reserva si el viaje descuenta de la cuota; con el modelo de la
# empresa la reserva es 0, porque la cuota es de servicio puro. Reservar
# igualmente inflaba la flota: el número de mercaderistas sale de un `ceil` por
# zona, así que un colchón que no corresponde cruza el redondeo y añade
# personas que después quedan medio vacías.
#
# Es una función y no una constante porque el modelo de jornada se elige por
# ejecución: como constante quedaba congelada en el valor que tuviera el flag
# al importar el módulo, y una ejecución "con desplazamiento" habría seguido
# dimensionando con reserva 0.
TRAVEL_ESTIMADO_POR_VISITA_PLAN_DEFAULT_MIN = 10


def travel_estimado_por_visita_plan_min() -> int:
    """Minutos de viaje reservados por visita al dimensionar la flota."""
    return TRAVEL_ESTIMADO_POR_VISITA_PLAN_DEFAULT_MIN if jornada_incluye_viaje() else 0

# Límites de desplazamiento entre dos sucursales consecutivas.
# MAX_TRAVEL_MINUTES es el tramo "cómodo" con el que se arma la ruta; el
# relajado y el extremo se usan en las pasadas de relleno y rescate cuando
# quedan huecos de jornada por llenar.
#
# Con el modelo de viaje actual estos minutos equivalen a un alcance de:
#   30 min ->  6.9 km   45 min -> 13.9 km   60 min -> 21.6 km  (línea recta)
# Los valores anteriores (20/35/50) daban 3.9 / 9.1 / 16.3 km. Un alcance de
# 3.9 km es demasiado corto frente al radio de zona (30 km): al buscar con qué
# llenar el resto del día, el motor no veía ningún punto y la jornada se
# quedaba con una sola visita y media jornada libre (910 jornadas así en la
# salida real). El desplazamiento supone solo el 7,6% del tiempo total, así que
# hay margen de sobra: cambiar 30 min de carretera por una visita de 240 min es
# un buen negocio. El tope combinado de 480 min/día sigue acotándolo todo.
MAX_TRAVEL_MINUTES = int(os.environ.get("MAX_TRAVEL_MINUTES", "30"))
MAX_TRAVEL_MINUTES_RELAXED = int(os.environ.get("MAX_TRAVEL_MINUTES_RELAXED", "45"))
MAX_TRAVEL_MINUTES_EXTREME = int(os.environ.get("MAX_TRAVEL_MINUTES_EXTREME", "60"))

# Dias laborales
DAY_NAMES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes"]
DAY_COLUMNS = ["LUNES", "MARTES", "MIERCOLES", "JUEVES", "VIERNES"]

# ---------------------------------------------------------------------------
# CUADRILLA DE FIN DE SEMANA
# ---------------------------------------------------------------------------
# Un mercaderista trabaja SIEMPRE cinco días; lo que cambia es cuáles. La
# cuadrilla de fin de semana corre su semana al miércoles-domingo para recoger
# las visitas que no cupieron de lunes a viernes.
#
# Ojo con lo que esto NO hace: no da más minutos. Cada persona sigue teniendo
# sus 5 x 480 a la semana y sus 9.600 al mes. Lo que aporta son más ranuras de
# día donde colocar las visitas de día fijo (frecuencias 8/12/16/20), que es lo
# que de verdad las mandaba a pendientes: a una misma persona le coincidían
# varios puntos exigiendo el mismo día mientras el resto de su semana quedaba a
# medias.
DIAS_FIN_SEMANA = ["Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

# Interruptor de la cuadrilla. Sirve para comparar una ejecución con y sin ella
# sin tocar código (CUADRILLA_FIN_SEMANA=0 la apaga).
CUADRILLA_FIN_SEMANA_ACTIVA = os.environ.get("CUADRILLA_FIN_SEMANA", "1") != "0"
DAY_COLUMNS_FIN_SEMANA = ["MIERCOLES", "JUEVES", "VIERNES", "SABADO", "DOMINGO"]

# Los siete días en orden natural. Sirve para fechar (lunes + desplazamiento) y
# para ordenar salidas y pantallas donde conviven las dos jornadas.
ORDEN_SEMANA = [
    "Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo",
]

# Nombres de los mercaderistas que van a fin de semana en la ejecución actual.
# Es estado de ejecución, igual que el modelo de jornada: se llena al crear la
# cuadrilla y se limpia al empezar cada procesamiento.
_mercadistas_fin_semana: set = set()


def registrar_mercadistas_fin_semana(nombres) -> None:
    """Marca esos mercaderistas como cuadrilla de fin de semana."""
    for nombre in nombres or []:
        if nombre:
            _mercadistas_fin_semana.add(str(nombre))


def limpiar_mercadistas_fin_semana() -> None:
    _mercadistas_fin_semana.clear()


def mercadistas_fin_semana() -> set:
    return set(_mercadistas_fin_semana)


def es_mercadista_fin_semana(nombre) -> bool:
    return bool(nombre) and str(nombre) in _mercadistas_fin_semana


def dias_de_mercadista(nombre) -> list:
    """Los cinco días laborables de ESE mercaderista."""
    return list(DIAS_FIN_SEMANA if es_mercadista_fin_semana(nombre) else DAY_NAMES)


def columnas_de_mercadista(nombre) -> list:
    """Columnas del Excel maestro donde se marca su asistencia."""
    return list(
        DAY_COLUMNS_FIN_SEMANA if es_mercadista_fin_semana(nombre) else DAY_COLUMNS
    )


def offset_calendario(dia) -> int:
    """Días desde el lunes: 0 = Lunes … 6 = Domingo. Sirve para fechar."""
    try:
        return ORDEN_SEMANA.index(dia)
    except ValueError:
        return 0


def dia_equivalente_fin_semana(dia) -> str:
    """
    Mismo hueco de la semana, corrido a la jornada de fin de semana.

    El primer día laborable de la cuadrilla es el miércoles, así que un punto
    que exigía lunes pasa a miércoles, el que exigía martes pasa a jueves, y
    así. Se conserva la separación entre visitas del patrón original: un punto
    de frecuencia 8 en "lunes y jueves" queda en "miércoles y domingo", no dos
    días seguidos.
    """
    try:
        return DIAS_FIN_SEMANA[DAY_NAMES.index(dia)]
    except (ValueError, IndexError):
        return dia

# Jornada laboral (en minutos desde medianoche)
HORA_INICIO_JORNADA = 8 * 60       # 08:00

# Hora límite para cerrar la última visita del día.
#
# Con el viaje dentro de la cuota, 480 min de trabajo + 60 de almuerzo caben
# justo entre las 08:00 y las 17:00. Sin el viaje dentro de la cuota un día
# puede tener 480 min de servicio Y ADEMÁS el desplazamiento, así que la
# agenda no cierra a las 17:00: mantener ese corte rechazaría las últimas
# visitas de cada día y las mandaría a pendientes, contradiciendo el
# dimensionado. El horario se extiende para que quepa lo que la cuota permite.
#
# Se deriva del techo diario para que la hora de cierre no se convierta en un
# segundo tope escondido: si la cuota permite un día de 600 min de servicio pero
# la agenda cierra antes, las últimas visitas se rechazan por horario y acaban
# en pendientes sin que ninguna métrica de carga lo explique.
#
# Igual que la reserva de viaje, se resuelve en cada llamada: depende del
# modelo de jornada, que se elige por ejecución.
def hora_fin_jornada() -> int:
    """Minuto del día (desde medianoche) en que debe estar cerrada la última visita."""
    override = os.environ.get("HORA_FIN_JORNADA_MIN")
    if override:
        try:
            return int(override)
        except ValueError:
            pass
    if jornada_incluye_viaje():
        return 17 * 60
    return min(21 * 60, 8 * 60 + max_dia_flex() + 60 + 60)
ALMUERZO_INICIO = 12 * 60          # 12:00
ALMUERZO_FIN = 13 * 60             # 13:00

NUM_SEMANAS_POR_MERCADISTA = 4

# ---------------------------------------------------------------------------
# ZONAS DE TRABAJO
# ---------------------------------------------------------------------------
# Radio máximo (km) de la zona de un mercadista, medido desde el punto ancla
# de la zona. Ningún par de puntos de una misma zona queda a más de
# 2 × RADIO_ZONA_KM, así que este valor acota el desplazamiento operativo.
#
# Las zonas se construyen agrupando PUNTOS DE VENTA, no provincias. Agrupar por
# provincia no sirve como control geográfico: Guayas por sí sola abarca más de
# 129 km, y en cambio dos puntos separados por 15 km a ambos lados de un límite
# provincial son perfectamente atendibles por la misma persona. Lo que importa
# es la distancia real entre puntos, no la frontera administrativa.
RADIO_ZONA_KM = float(os.environ.get("RADIO_ZONA_KM", "30"))

# Radio de la zona de un mercaderista medido DESDE SU CENTRO.
#
# Antes el límite se comprobaba entre todos los pares de puntos: ninguno podía
# estar a más de 60 km de otro del mismo dueño. Esa forma de medirlo fabricaba
# mercaderistas al 5% de ocupación. Caso real: HERMANOS BORBOR, un punto de 8
# visitas de 60 min, acabó con una persona para él solo estando a 1,9 km de un
# punto del Mercadista 46 —que tenía el miércoles con 382 min libres y el jueves
# con 292—, porque también estaba a 72 km de otros dos puntos de esa persona.
# Y el Mercadista 46 ya arrastraba 75 km de dispersión propia, así que su zona
# quedaba congelada: no podía crecer nunca más aunque le sobraran mes y días.
#
# Midiendo desde el centro, una zona ancha sigue admitiendo puntos cercanos a su
# núcleo. Medido de extremo a extremo sobre el rutero nacional: 71 -> 68
# mercaderistas, los de ocupación por debajo del 60% pasan de 23 a 15 y los de
# menos del 30% de 8 a 5, a cambio de un punto de cobertura (97,2% -> 96,2%).
RADIO_CENTRO_ZONA_KM = float(os.environ.get("RADIO_CENTRO_ZONA_KM", "40"))

# Alcance ampliado, permitido SOLO para recuperar plazas casi vacías.
#
# El radio de 30 km (60 de diámetro) es el correcto para una ruta normal, pero
# aplicado también al rescate deja abiertas plazas al 0,6-20% en comarcas
# aisladas: nadie más llega hasta allí, así que su carga no puede cederse y la
# plaza se queda. Medido sobre la entrada real, esa cola vale ~9,6 mercaderistas
# de capacidad desperdiciada.
#
# La planificación real de la empresa resuelve esto exactamente así: 13 de sus
# 78 mercaderistas cubren más de 60 km y el mayor abarca 323. Este límite es más
# conservador que el suyo, y solo se aplica cuando la alternativa es mantener
# una plaza entera para unas pocas horas de trabajo al mes.
RADIO_RESCATE_AMPLIADO_KM = float(
    os.environ.get("RADIO_RESCATE_AMPLIADO_KM", str(2.5 * RADIO_ZONA_KM))  # 75 km -> 150 de diámetro
)

# Rondas máximas de refuerzo de flota. Tras asignar, las visitas que no cupieron
# en su zona generan mercadistas adicionales en esa misma zona y se reasigna.
# El tope evita un bucle infinito si alguna visita es imposible de colocar
# (p. ej. un servicio más largo que la propia jornada).
MAX_RONDAS_REFUERZO_FLOTA = int(os.environ.get("MAX_RONDAS_REFUERZO_FLOTA", "4"))

# Holgura sobre el mínimo teórico de plantilla.
#
# ceil(demanda / 9600) es el suelo aritmético, pero dejar la flota justo ahí no
# funciona: con 743.280 min de demanda y 78 personas sobran 5.520 min en todo el
# mes (0,7%), así que TODAS las jornadas tendrían que salir casi perfectas y lo
# que no encaja no tiene dónde ir. Medido: a 78 exactos la cobertura se queda en
# el 91%; con un 3% de holgura sube sin salirse de la banda que pide negocio.
HOLGURA_FLOTA = float(os.environ.get("HOLGURA_FLOTA", "1.00"))

# Los alias de los nombres antiguos en servicio puro (MIN_SERVICIO_MINUTOS,
# MAX_MES_MINUTOS, ...) desaparecieron al pasar las cuotas a funciones: como
# constantes quedaban congeladas en 480/9600 y una ejecución con la jornada
# reducida (400/8000) habría seguido validando contra la cuota completa. Usa
# `cuota_dia()`, `cuota_semana()`, `cuota_mes()`, `min_dia()`, `min_semana()`,
# `min_mes()`, `max_dia_flex()`, `max_semana_flex()` y `max_servicio_dia()`.

# Categorias (implementacion temporal neutra)
CATEGORIAS = {}
FARMACIAS_HASTA_15 = set()
