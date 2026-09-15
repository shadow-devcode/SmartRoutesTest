"""
Modulo de scheduling: horarios, almuerzo, confirmacion de visitas.
Consolida la logica que estaba duplicada 3+ veces en el main.py original.
"""

from datetime import timedelta

from route_engine.config import (
    ALMUERZO_FIN,
    ALMUERZO_INICIO,
    carga_jornada,
    cuota_dia,
    hora_fin_jornada,
    max_dia_flex,
    max_servicio_dia,
    viaje_en_agenda,
    tope_jornada_real,
    jornada_incluye_viaje,
)
from route_engine.frequency import semanas_por_frecuencia
from route_engine.geo import estimar_minutos_viaje
from route_engine.mapbox import _geocode_cache, calcular_tiempo_entre


def format_duracion(tiempo_min):
    """Formatea minutos a string legible: '1:30 horas' o '45 mins'."""
    horas = int(tiempo_min // 60)
    mins = int(tiempo_min % 60)
    return f"{horas}:{mins:02d} horas" if horas > 0 else f"{mins} mins"


def calcular_horario_almuerzo(hora_inicio_propuesto, tiempo_servicio, aplicar_almuerzo):
    """
    Calcula inicio/fin reales considerando almuerzo.
    Retorna (temp_inicio, temp_fin).
    """
    temp_inicio = hora_inicio_propuesto
    temp_fin = temp_inicio + tiempo_servicio

    if aplicar_almuerzo:
        if ALMUERZO_INICIO <= temp_inicio < ALMUERZO_FIN:
            temp_inicio = ALMUERZO_FIN
            temp_fin = temp_inicio + tiempo_servicio
        elif temp_inicio < ALMUERZO_INICIO < temp_fin:
            dur_antes = ALMUERZO_INICIO - temp_inicio
            dur_despues = tiempo_servicio - dur_antes
            temp_fin = ALMUERZO_FIN + dur_despues

    return temp_inicio, temp_fin


def formato_horario(hora_inicio_propuesto, tiempo_servicio, hora_inicio_real, hora_final, aplicar_almuerzo):
    """
    Genera el string de horario. Si cruza almuerzo, muestra horario dividido.
    """
    if aplicar_almuerzo and (hora_inicio_propuesto < ALMUERZO_INICIO < hora_inicio_propuesto + tiempo_servicio):
        h1 = f"{int(hora_inicio_propuesto) // 60:02d}:{int(hora_inicio_propuesto) % 60:02d}"
        h2 = f"{int(ALMUERZO_INICIO) // 60:02d}:{int(ALMUERZO_INICIO) % 60:02d}"
        h3 = f"{int(ALMUERZO_FIN) // 60:02d}:{int(ALMUERZO_FIN) % 60:02d}"
        h4 = f"{int(hora_final) // 60:02d}:{int(hora_final) % 60:02d}"
        return f"{h1} - {h2}, {h3} - {h4}"
    else:
        h_inicio = f"{int(hora_inicio_real) // 60:02d}:{int(hora_inicio_real) % 60:02d}"
        h_fin = f"{int(hora_final) // 60:02d}:{int(hora_final) % 60:02d}"
        return f"{h_inicio} - {h_fin}"


def crear_resumen_visita(
    merc_name,
    day,
    orden_idx,
    it,
    tiempo_entre_minutes,
    tiempo_entre_km,
    horario,
    semana,
):
    """
    Crea el diccionario de resumen de visita para day_summaries.
    Esta funcion estaba duplicada 3 veces en el original.
    """
    key_coord = (round(float(it["lat"]), 6), round(float(it["lon"]), 6))
    provincia, ciudad, calle = _geocode_cache.get(key_coord, ("", "", ""))

    return {
        "Mercadista": merc_name,
        "Día": day,
        "Orden Ruta": orden_idx,
        "Descripción": it["descripcion"],
        "Latitud": it["lat"],
        "Longitud": it["lon"],
        "PROVINCIA": provincia,
        "CIUDAD": ciudad,
        "CALLE": calle,
        "Tiempo Servicio (min)": it["tiempo"],
        "Duración (hh:mm)": format_duracion(it["tiempo"]),
        "Tiempo entre sucursal (min)": round(tiempo_entre_minutes, 2),
        "kilometros entre sucurlas (km)": round(tiempo_entre_km, 2),
        "Horario": horario,
        "Fecha": f"semana {semana}",
    }


def generar_resumenes_por_semana(
    merc_name, day, orden_idx, it, tiempo_entre_minutes, tiempo_entre_km, horario,
    fecha_base, emitidos_punto_semana,
):
    """
    Genera resumenes (day_summaries) para todas las semanas que correspondan a esta visita.
    Retorna lista de dicts y actualiza emitidos_punto_semana in-place.
    """
    semana_num = it.get("semana_num")
    frecuencia_mes = it.get("frecuencia_mes", 1)

    if semana_num is None:
        num_semanas = semanas_por_frecuencia(frecuencia_mes)
        semanas_a_generar = range(1, num_semanas + 1)
    else:
        semanas_a_generar = [semana_num]

    # Clave por FILA (idx), no por local: dos filas del mismo punto de venta
    # —una tienda listada dos veces con servicios distintos— comparten
    # (lat, lon, descripción). Deduplicando por local, la segunda fila se
    # descartaba en silencio y luego reaparecía como "visita faltante" en la
    # reconciliación de frecuencia.
    clave_it = (clave_punto(it), it.get("punto_key", it.get("idx")))
    resumenes = []
    for semana in semanas_a_generar:
        if (clave_it, semana) in emitidos_punto_semana:
            continue
        emitidos_punto_semana.add((clave_it, semana))
        resumenes.append(
            crear_resumen_visita(
                merc_name, day, orden_idx, it,
                tiempo_entre_minutes, tiempo_entre_km, horario, semana,
            )
        )
    return resumenes


def clave_punto(inst):
    """Clave unica de un punto fisico (lat, lon, descripcion)."""
    return (
        round(float(inst.get("lat", 0)), 6),
        round(float(inst.get("lon", 0)), 6),
        str(inst.get("descripcion", "")).strip(),
    )


class VisitaConfirmador:
    """
    Encapsula el estado necesario para confirmar visitas en un dia:
    - Tracking de tiempo total, hora actual, posicion previa
    - Logica de almuerzo
    - Generacion de resumenes

    Elimina la necesidad de pasar 10+ variables entre funciones.
    """

    def __init__(
        self, merc_name, day, fecha_base, aplicar_almuerzo, tope_dia=None,
        presupuesto_mes=None,
    ):
        self.merc_name = merc_name
        self.day = day
        self.fecha_base = fecha_base
        self.aplicar_almuerzo = aplicar_almuerzo
        # Techo de ESTE día. Por defecto la jornada nominal; quien arma el día
        # puede subirlo hasta max_dia_flex() cuando lo que está en
        # juego es dejar la visita sin colocar. El control de que nadie trabaje
        # de más lo ejerce el tope mensual de 9.600, no este número.
        self.tope_dia = float(tope_dia or cuota_dia())
        # Minutos de cuota MENSUAL que le quedan al mercaderista al empezar el
        # día. Es el límite duro del modelo: la jornada flexible permite un día
        # de 600, pero el mes sigue siendo 9.600. Sin este control los pases de
        # relleno llenaban 20 días a 600 y el mercaderista acababa al 107% del
        # mes contratado.
        self.presupuesto_mes = (
            float(presupuesto_mes) if presupuesto_mes is not None else None
        )
        self.prev_lat = None
        self.prev_lon = None
        self.prev_fin_minutos = 8 * 60  # 08:00
        self.tiempo_total_dia = 0.0
        # Tiempo de desplazamiento acumulado del día. Sumado al servicio_total
        # debe quedar por debajo de cuota_dia() (regla 480 min/día).
        self.travel_total_dia = 0.0
        self.visitas_confirmadas = []
        self.day_summaries = []
        self.emitidos_punto_semana = set()

    @property
    def servicio_total(self):
        return sum(x["tiempo"] for x in self.visitas_confirmadas)

    @property
    def combinado_total(self):
        """
        Jornada consumida según el modelo activo (ver `carga_jornada`).

        Es la magnitud contra la que hay que decidir si un día está lleno.
        Usar solo `servicio_total` hacía que un día con 300 min en tienda y
        180 de carretera —una jornada de 8 h completa— se considerara medio
        vacío, y el motor intentara inútilmente meterle más visitas.
        """
        return carga_jornada(self.servicio_total, self.travel_total_dia)

    def intentar_confirmar(self, it, travel_limit=None, skip_distance_check=False):
        """
        Intenta confirmar una visita. Retorna True si se confirmo, False si se rechazo.
        Maneja: estimacion de viaje, calculo de tiempo, almuerzo, limites.
        """
        # 1) Estimacion rapida de distancia
        if self.prev_lat is not None and not skip_distance_check and travel_limit is not None:
            approx_min = estimar_minutos_viaje(self.prev_lat, self.prev_lon, it["lat"], it["lon"])
            # El límite de desplazamiento aplica a TODOS, también a las visitas
            # de día fijo (`fixed_mercadista`, las de frecuencia 8/12/16/20).
            # El bypass existía para no perderlas, pero lo que hacía era
            # colarlas a cualquier distancia: producía jornadas con saltos de
            # 485 km —Rumiñahui a Puyo, Quevedo a Quito— y horarios que se iban
            # más allá de medianoche. Rechazarlas aquí deja que el asignador les
            # busque otro día o que el rescate se las pase a un mercaderista de
            # su provincia, que es lo correcto.
            if approx_min > travel_limit + 5:
                return False

        # 2) Calculo real de tiempo entre sucursales
        tiempo_entre_minutes, tiempo_entre_km = calcular_tiempo_entre(
            self.prev_lat, self.prev_lon, it["lat"], it["lon"]
        )

        # 3) Validar limite de viaje real
        if self.prev_lat is not None and travel_limit is not None:
            if tiempo_entre_minutes > travel_limit:
                return False

        # 4) Límite diario de servicio (tope absoluto sobre Tiempo Servicio puro)
        #
        # Excepción: una visita que por sí sola dura más que la jornada. Hay 3
        # en la entrada real (hasta 540 min). Rechazarlas siempre las condena a
        # 'Pendientes' para toda la eternidad, pase lo que pase con el reparto.
        # Si el día está vacío se acepta y el día vale lo que valga esa visita:
        # el dato de entrada manda, y así el punto queda atendido.
        servicio_total_si_agrego = self.servicio_total + it["tiempo"]
        dia_vacio = not self.visitas_confirmadas
        visita_mayor_que_jornada = float(it["tiempo"] or 0) > self.tope_dia
        if servicio_total_si_agrego > max(max_servicio_dia(), self.tope_dia):
            if not (dia_vacio and visita_mayor_que_jornada):
                return False

        # 4.b) Tope de la jornada. `carga_jornada` decide si el desplazamiento
        # descuenta de la cuota o no (ver `jornada_incluye_viaje` en config).
        #
        # Sin excepciones, tampoco para `fixed_mercadista`. Ese bypass existía
        # para no perder las visitas de los puntos de frecuencia alta, pero no
        # las salvaba: el día quedaba en 600-700 min, el postproceso recortaba
        # el exceso y esas mismas visitas terminaban en 'Pendientes_Sin_Asignar'
        # igualmente, solo que después de haber desplazado a otras. Rechazarlas
        # aquí permite que el asignador les busque hueco en otro día de su
        # semana, que sí las coloca.
        carga_si_agrego = carga_jornada(
            self.servicio_total + it["tiempo"],
            self.travel_total_dia + tiempo_entre_minutes,
        )
        if carga_si_agrego > self.tope_dia:
            if not (dia_vacio and visita_mayor_que_jornada):
                return False

        # 4.c) Tope del RELOJ de la jornada: servicio MÁS desplazamiento, mida
        # lo que mida la cuota.
        #
        # Con el modelo "sin tiempo de desplazamiento" la comprobación anterior
        # ignora la carretera, así que un día podía cerrar con 480 min de
        # servicio y otros 500 de coche encima. Medido antes de este tope: 506
        # de 1.313 jornadas pasaban de 480 min reales, 27 pasaban de 720, y la
        # peor llegaba a 994 (435 de servicio + 559 de viaje). El día tiene las
        # horas que tiene.
        reloj_si_agrego = (
            self.servicio_total
            + float(it["tiempo"] or 0)
            + self.travel_total_dia
            + tiempo_entre_minutes
        )
        # Solo con el modelo que cuenta el desplazamiento: en el que no lo cuenta,
        # la carretera no decide qué visitas caben.
        if jornada_incluye_viaje() and reloj_si_agrego > tope_jornada_real():
            if not (dia_vacio and visita_mayor_que_jornada):
                return False

        # 4.c) Cuota mensual. Límite duro, sin excepciones.
        if self.presupuesto_mes is not None and carga_si_agrego > self.presupuesto_mes:
            return False

        # 5) Calculo de horario con almuerzo
        #
        # El trayecto solo desplaza el reloj si el modelo lo cuenta. Con "sin
        # tiempo de desplazamiento" la siguiente visita empieza cuando termina
        # la anterior: es lo que significa que la jornada sean solo los puntos.
        viaje_agenda = viaje_en_agenda(tiempo_entre_minutes)
        hora_inicio_propuesto = self.prev_fin_minutos + viaje_agenda
        temp_inicio, temp_fin = calcular_horario_almuerzo(
            hora_inicio_propuesto, it["tiempo"], self.aplicar_almuerzo
        )

        # 6) Limite 15:00 para farmacias (comentado en original, preservado)
        # if it.get("farmacia_hasta_15"):
        #     if temp_fin > 15 * 60:
        #         return False

        # 7) Fin de jornada
        if temp_fin > hora_fin_jornada():
            return False

        # Confirmar
        horario = formato_horario(
            hora_inicio_propuesto, it["tiempo"], temp_inicio, temp_fin,
            self.aplicar_almuerzo,
        )
        orden_idx = len(self.visitas_confirmadas) + 1

        resumenes = generar_resumenes_por_semana(
            self.merc_name, self.day, orden_idx, it,
            viaje_agenda, tiempo_entre_km, horario,
            self.fecha_base, self.emitidos_punto_semana,
        )
        self.day_summaries.extend(resumenes)
        self.visitas_confirmadas.append(it)
        # tiempo_total_dia se mantiene como servicio puro (compatibilidad con
        # el resto del pipeline). travel_total_dia se acumula aparte para que
        # la próxima iteración pueda evaluar la regla combinada 480 min/día.
        self.tiempo_total_dia = self.servicio_total
        self.travel_total_dia += float(tiempo_entre_minutes or 0)
        self.prev_fin_minutos = int(round(temp_fin))
        self.prev_lat = it["lat"]
        self.prev_lon = it["lon"]

        return True


def mostrar_progreso(paso, total, mensaje="Procesando"):
    """Muestra barra de progreso en consola."""
    if total <= 0:
        total = 1
    paso_clamped = max(0, min(total, paso))
    porcentaje = int((paso_clamped / total) * 100)
    barra_llena = int(porcentaje / 5)
    barra = "#" * barra_llena + "-" * (20 - barra_llena)
    print(f"\r{mensaje}: [{barra}] {porcentaje}% ({paso_clamped}/{total})", end="", flush=True)
    if paso_clamped >= total:
        print()
