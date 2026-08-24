"""
Motor de asignacion de mercadistas a rutas por dia y semana.
Contiene el loop principal de asignacion extraido de procesar_minoristas.
"""

import copy
import math
from collections import defaultdict
from datetime import date, timedelta

from route_engine.config import (
    DAY_COLUMNS,
    DAY_NAMES,
    ORDEN_SEMANA,
    columnas_de_mercadista,
    dias_de_mercadista,
    offset_calendario,
    MAX_TRAVEL_MINUTES,
    MAX_TRAVEL_MINUTES_EXTREME,
    MAX_TRAVEL_MINUTES_RELAXED,
    NUM_SEMANAS_POR_MERCADISTA,
    carga_jornada,
    cuota_dia,
    cuota_mes,
    max_dia_flex,
    max_semana_flex,
    min_dia,
    min_mes,
    min_semana,
    travel_estimado_por_visita_plan_min,
)
from route_engine.geo import haversine_km
from route_engine.load_grouping import clave_grupo, normalizar_tipo_carga
from route_engine.mapbox import norm_provincia
from route_engine.routing import (
    compute_route_por_ciudad,
    construir_dia_por_cercania,
    ordenar_por_localidad_y_cercania,
)
from route_engine.scheduling import (
    VisitaConfirmador,
    clave_punto,
    mostrar_progreso,
)


def _fechas_semana_referencia():
    # Los siete días: con cuadrilla de fin de semana también hay que fechar
    # sábado y domingo.
    hoy = date.today()
    monday = hoy - timedelta(days=hoy.weekday())
    mapa = {}
    for i, dia in enumerate(ORDEN_SEMANA):
        fecha = monday + timedelta(days=i)
        mapa[dia] = fecha.strftime("%d/%m/%Y")
    return mapa


def _lunes_siguiente():
    hoy = date.today()
    return hoy + timedelta(days=(7 - hoy.weekday()))


def carga_combinada(insts):
    """
    Coste de jornada de un conjunto de visitas: servicio + reserva de viaje.

    Al repartir carga entre mercadistas todavía no existe la ruta, así que el
    desplazamiento real se desconoce. Se reserva un estimado por visita para
    que el presupuesto semanal esté en la misma unidad que los topes (minutos
    combinados). Antes se comparaba servicio puro contra topes combinados, y
    por eso los objetivos resultaban inalcanzables.
    """
    return sum(
        carga_jornada(x.get("tiempo"), travel_estimado_por_visita_plan_min()) for x in insts
    )


def _parse_merc_num(nombre):
    if not isinstance(nombre, str):
        return None
    if not nombre.startswith("Mercadista "):
        return None
    try:
        return int(nombre.replace("Mercadista", "").strip())
    except ValueError:
        return None


class AsignacionState:
    """Estado compartido durante el proceso de asignacion."""

    def __init__(
        self, mercadistas_plan, visit_instances, df, tipo_ruta="tiempo_completo",
        propiedad_inicial=None, tipo_carga=None,
    ):
        self.mercadistas_plan = mercadistas_plan
        self.visit_instances = visit_instances
        self.df = df
        self.tipo_ruta = tipo_ruta
        self.plan_names_base = set(m[0] for m in mercadistas_plan)
        self.extra_mercadistas = set()
        self.puntos_pendientes = []
        # Visitas que el rescate no pudo encajar en ninguna jornada existente.
        # Se guarda la instancia entera (no solo el reporte) para que el
        # procesador pueda abrirles plaza; antes se anotaban únicamente como
        # texto en `puntos_pendientes` y con eso ya no había forma de volver a
        # intentarlo.
        self.sin_hueco = []
        self.puntos_sin_coordenadas = []
        self.all_day_summaries = []
        self.fechas_map = _fechas_semana_referencia()
        self.mercadistas_asignados = 0
        # Un punto (idx o punto_key) solo puede pertenecer a un mercadista en
        # todas sus visitas. Si el planificador de flota ya repartió los puntos
        # por capacidad mensual, se parte de ese reparto: el asignador entonces
        # solo decide QUÉ DÍA hace cada visita, no de quién es el punto.
        self.punto_mercadista = dict(propiedad_inicial or {})

        # Tipo de carga y partición (cadena/ciudad) de cada mercaderista. La
        # barrera no basta con aplicarla al planificar: el asignador y los pases
        # de rescate mueven puntos entre personas, y sin comprobarlo aquí un
        # mercaderista de TRADICIONAL acababa con puntos de TIA.
        self.tipo_carga = normalizar_tipo_carga(tipo_carga)
        self.grupo_de_punto = {}
        self.grupo_de_merc = {}
        if self.tipo_carga != "zona":
            for inst in visit_instances:
                pk = inst.get("punto_key", inst.get("idx"))
                if pk is not None:
                    self.grupo_de_punto.setdefault(pk, clave_grupo(inst, self.tipo_carga))
            for pk, merc in self.punto_mercadista.items():
                grupo = self.grupo_de_punto.get(pk)
                if merc and grupo is not None:
                    self.grupo_de_merc.setdefault(merc, grupo)

        # Particionar por ZONA de trabajo, que viene anotada en cada visita por
        # `_clusterizar_visitas`. La zona es un grupo compacto de puntos (radio
        # acotado), no una provincia: es lo que garantiza que un mercadista no
        # acabe con puntos a 200 km unos de otros.
        self.remaining_by_prov = {}
        for inst in copy.deepcopy(visit_instances):
            self.remaining_by_prov.setdefault(self.zona_de(inst), []).append(inst)

        self.total_visitas = self.count_remaining()

    def zona_de(self, inst):
        """
        Zona de trabajo de una visita. Si no viene anotada (flujos que no pasan
        por el planificador), se cae a la provincia para no perder la visita.
        """
        zona = inst.get("zona")
        if zona:
            return zona
        return norm_provincia(inst.get("provincia_punto", "")) or "SIN_PROVINCIA"

    def get_punto_key(self, inst):
        """
        Identidad del PUNTO DE VENTA a efectos de la regla «un punto, un
        mercadista»: la FILA del Excel de entrada.

        La fila es la unidad indivisible. Todas las visitas de una fila van
        siempre al mismo mercadista; eso es lo que garantiza la regla.

        Antes se usaba la identidad física del local (lat|lon|descripción) para
        que una tienda listada en varias filas no se repartiera entre dos
        personas. Era una regla MÁS ESTRICTA que la de la propia empresa: en el
        plan real (`minoristas_ubicaciones ... MODERNA.xlsx`) 48 de los 362
        locales están repartidos entre dos mercaderistas —HIPERMARKET MANTA,
        MONTECRISTI, SUR, RIOCEIBOS...— sencillamente porque no caben en una
        sola persona: el local más cargado suma 32.760 min/mes, casi 3,5 veces
        la jornada mensual de 9.600.

        Fundir las filas creaba bloques inasignables: 30 locales pasaban de 480
        min si sus filas coincidían en un día, y 9 exigían más de 20 visitas al
        mes (imposible en 20 días laborables). Esos 9 locales solos explicaban
        62.880 min de los 185.490 que quedaban sin colocar.

        La afinidad de local no se pierde: `afinidad_de` la conserva como
        preferencia blanda al armar el pool semanal.
        """
        return inst.get("punto_key", inst.get("idx"))

    def afinidad_de(self, inst):
        """
        Local físico al que pertenece la fila (lat|lon|descripción).

        No es una restricción: es la preferencia de mantener juntas las filas de
        una misma tienda en el mismo mercadista mientras quepan.
        """
        return inst.get("group_key") or self.get_punto_key(inst)

    def puede_atender(self, merc_name, inst):
        """
        ¿Puede este mercaderista quedarse con esta visita?

        Con tipo de carga "zona" siempre sí. Con "cadena" o "ciudad", solo si
        aún no tiene partición asignada o coincide con la del punto.
        """
        if self.tipo_carga == "zona" or not merc_name:
            return True
        pk = inst.get("punto_key", inst.get("idx")) if isinstance(inst, dict) else inst
        grupo = self.grupo_de_punto.get(pk)
        if grupo is None:
            return True
        actual = self.grupo_de_merc.get(merc_name)
        return actual is None or actual == grupo

    def asignar_punto(self, pk, merc_name, inst=None):
        """
        Registra el dueño de un punto y fija la partición del mercaderista.

        Devuelve False y NO asigna si rompería la regla del tipo de carga. Es la
        última línea de defensa: los pases que eligen destino comprueban antes
        con `puede_atender`, pero algunos deciden sobre una simulación (la
        disolución de plazas flojas planifica varios traslados y los aplica de
        golpe) y para entonces la partición del destino aún no estaba fijada.
        """
        grupo = self.grupo_de_punto.get(pk)
        if grupo is None and inst is not None and self.tipo_carga != "zona":
            grupo = clave_grupo(inst, self.tipo_carga)
            if grupo is not None:
                self.grupo_de_punto[pk] = grupo
        actual = self.grupo_de_merc.get(merc_name)
        if grupo is not None and actual is not None and actual != grupo:
            return False
        self.punto_mercadista[pk] = merc_name
        if merc_name and grupo is not None:
            self.grupo_de_merc.setdefault(merc_name, grupo)
        return True

    def count_remaining(self):
        return sum(len(v) for v in self.remaining_by_prov.values())

    def next_extra_mercadista(self):
        """No se crean mercadistas adicionales; solo el plan definido en Excel."""
        return None


def ejecutar_asignacion(
    visit_instances, mercadistas_plan, df, tipo_ruta="tiempo_completo",
    propiedad_inicial=None, tipo_carga=None,
):
    """
    Ejecuta la asignacion completa de mercadistas a rutas.
    Retorna (all_day_summaries, remaining_by_prov, puntos_pendientes, state).
    """
    # Los objetivos se expresan en minutos COMBINADOS (servicio + viaje), que
    # es como se mide la jornada. Al repartir carga entre mercadistas todavía
    # no se conocen las rutas, así que se descuenta del presupuesto semanal la
    # reserva de viaje estimada por visita: lo que queda es el presupuesto de
    # servicio puro con el que se arma el pool de la semana.
    total_servicio_all = sum(inst["tiempo"] for inst in visit_instances)
    max_servicio_semana = max_semana_flex()
    min_servicio_semana = min_semana()

    state = AsignacionState(
        mercadistas_plan, visit_instances, df, tipo_ruta=tipo_ruta,
        propiedad_inicial=propiedad_inicial, tipo_carga=tipo_carga,
    )

    # Cuántos mercadistas del plan quedan por procesar en cada zona. Se
    # decrementa al terminar cada uno para repartir la carga de la zona entre
    # los que realmente quedan.
    pendientes_por_zona = defaultdict(int)
    for _, zona_plan in mercadistas_plan:
        pendientes_por_zona[norm_provincia(zona_plan) if zona_plan else None] += 1

    for merc_name, provincia_asignada in mercadistas_plan:
        if state.count_remaining() == 0:
            break

        if provincia_asignada:
            provincia_asignada = norm_provincia(provincia_asignada)
            remaining = state.remaining_by_prov.get(provincia_asignada, [])
        else:
            remaining = []
            for v in state.remaining_by_prov.values():
                remaining.extend(v)

        if not remaining:
            continue

        # Los cinco días de ESTE mercaderista: lunes-viernes los de la
        # plantilla normal, miércoles-domingo los de la cuadrilla de fin de
        # semana. Siempre son cinco, así que el reparto semanal no cambia.
        dias_merc = dias_de_mercadista(merc_name)
        columnas_merc = columnas_de_mercadista(merc_name)

        minutos_merc_mes = 0.0
        # Minutos que este mercaderista ya tiene ESCRITOS en el calendario.
        # Es la única cuenta fiable para el tope mensual: una instancia de
        # frecuencia 4 genera cuatro filas de agenda, así que contar instancias
        # (como hace `minutos_merc_mes`, pensado para repartir carga) deja el
        # presupuesto corto y rechaza visitas que sí cabían.
        servicio_emitido_merc = 0.0
        # Carga MENSUAL completa de los puntos que este mercadista ha adoptado.
        # No es lo mismo que `minutos_merc_mes` (lo ya colocado): adoptar un
        # punto compromete todas sus visitas del mes, incluidas las de semanas
        # que aún no se han procesado.
        compromiso_mes_merc = 0.0
        techo_fijo_semana_merc = None
        dias_por_idx = {idx: [] for idx in df.index}

        for semana_idx in range(NUM_SEMANAS_POR_MERCADISTA):
            visitas_asignadas = state.total_visitas - state.count_remaining()
            mostrar_progreso(visitas_asignadas, state.total_visitas, "      Progreso")

            # Reobtener remaining
            if provincia_asignada:
                remaining = state.remaining_by_prov.get(provincia_asignada, [])
            else:
                remaining = []
                for v in state.remaining_by_prov.values():
                    remaining.extend(v)

            # En minutos combinados, misma unidad que los topes semanales/mensuales
            total_servicio_restante = carga_combinada(remaining)
            total_remaining_prov = total_servicio_restante
            if provincia_asignada:
                num_mercs_en_prov = len(
                    [m for m, p in mercadistas_plan if p and norm_provincia(p) == provincia_asignada]
                )
            else:
                num_mercs_en_prov = len(mercadistas_plan)
            num_mercs_en_prov = max(1, num_mercs_en_prov)

            if techo_fijo_semana_merc is None and total_remaining_prov > 0:
                # Priorizar empacado eficiente (Greedy Packing) permitiendo hasta max_servicio_semana
                # para consolidar visitas en menos mercadistas antes de delegar a otro.
                techo_fijo_semana_merc = max_servicio_semana

            if not remaining:
                objetivo_semana = 0.0
            else:
                hueco_mes = max(0, cuota_mes() - minutos_merc_mes)
                semanas_restantes = NUM_SEMANAS_POR_MERCADISTA - semana_idx
                min_ideal_resto_mes = max(0, min_mes() - minutos_merc_mes)
                objetivo_min_semana = min_ideal_resto_mes / semanas_restantes if semanas_restantes else 0

                techo_semana_para_repartir = (
                    techo_fijo_semana_merc
                    if techo_fijo_semana_merc is not None
                    else (total_remaining_prov / num_mercs_en_prov) / NUM_SEMANAS_POR_MERCADISTA
                )
                techo_semana_para_repartir = (
                    min(techo_semana_para_repartir, total_remaining_prov)
                    if total_remaining_prov > 0
                    else 0
                )
                # Cumplir estándar: 2400–2500 min/semana cuando hay puntos suficientes
                if total_servicio_restante >= min_servicio_semana and hueco_mes >= min_servicio_semana:
                    techo_semana_para_repartir = max(techo_semana_para_repartir, min_servicio_semana)

                # Mercadistas que quedan por llenar EN ESTA ZONA, incluido el
                # actual. Debe ser de la zona porque `total_servicio_restante`
                # también lo es: dividir el trabajo de una zona entre todos los
                # mercadistas del país hacía que cada uno se llevara una
                # fracción mucho menor de la que le tocaba, y el sobrante caía
                # en los últimos del plan, que quedaban casi vacíos.
                mercadistas_restantes = max(1, pendientes_por_zona.get(provincia_asignada, 1))
                objetivo_semana = (
                    int(math.ceil(total_servicio_restante / mercadistas_restantes))
                    if total_servicio_restante > 0
                    else 0
                )
                objetivo_semana = min(
                    max_servicio_semana,
                    max(min_servicio_semana, objetivo_semana)
                    if total_servicio_restante >= min_servicio_semana * mercadistas_restantes
                    else objetivo_semana,
                )
                objetivo_semana = (
                    min(objetivo_semana, hueco_mes, techo_semana_para_repartir)
                    if hueco_mes > 0
                    else min(objetivo_semana, techo_semana_para_repartir)
                )
                if (
                    minutos_merc_mes < min_mes()
                    and semanas_restantes
                    and total_remaining_prov >= objetivo_min_semana
                ):
                    objetivo_semana = max(
                        objetivo_semana,
                        min(int(objetivo_min_semana), techo_semana_para_repartir, hueco_mes, max_servicio_semana),
                    )

            # Armar pool semanal: cada punto (idx o punto_key) solo puede pertenecer a un mercadista
            semana_actual = semana_idx + 1
            available = [
                inst for inst in remaining
                if state.get_punto_key(inst) not in state.punto_mercadista
                or state.punto_mercadista[state.get_punto_key(inst)] == merc_name
            ]
            available_week = [
                inst for inst in available
                if inst.get("semana_num") is None or inst.get("semana_num") == semana_actual
            ]

            week_pool = []
            week_sum = 0.0
            puntos_en_pool = set()

            # 1) Puntos ya asignados a este mercadista: toda su carga de esta semana.
            #
            # Estos NO se limitan con `objetivo_semana`, sino con el techo real
            # de la semana. `objetivo_semana` es un reparto orientativo entre los
            # mercadistas de la zona; aplicarlo aquí dejaba visitas varadas sin
            # salida posible: el punto ya tiene dueño, así que ningún otro
            # mercadista puede tomarlas (regla de un punto = un mercadista), y su
            # dueño las rechazaba por exceder una cuota que ni siquiera es un
            # límite operativo. Peor aún, cuantos más mercadistas tenía la zona
            # menor era la cuota y más visitas quedaban varadas: por eso reforzar
            # la flota no reducía el faltante (149.000 min con 101 mercadistas y
            # 150.000 con 129). Ser dueño de un punto obliga a atenderlo.
            techo_propios = max_servicio_semana
            if hueco_mes > 0:
                techo_propios = min(techo_propios, hueco_mes)

            ids_del_merc = {pk for pk, m in state.punto_mercadista.items() if m == merc_name}
            for inst in available_week:
                pk = state.get_punto_key(inst)
                if pk not in ids_del_merc or pk in puntos_en_pool:
                    continue
                bloque = [x for x in available_week if state.get_punto_key(x) == pk]
                bloque_sum = carga_combinada(bloque)
                if week_sum + bloque_sum <= techo_propios or not week_pool:
                    week_pool.extend(bloque)
                    week_sum += bloque_sum
                    puntos_en_pool.add(pk)

            # 2) Puntos aún no asignados: asignar por punto completo (toda la semana)
            #
            # Adoptar un punto es un compromiso MENSUAL, no semanal: a partir de
            # aquí ningún otro mercadista podrá atenderlo. Por eso se comprueba
            # que quepa la carga del punto en lo que queda de mes, no solo la de
            # esta semana. Sin esa comprobación los primeros mercadistas de la
            # zona se quedaban con todos los puntos mirando únicamente la semana
            # en curso, se llenaban, y las visitas de las semanas siguientes se
            # quedaban sin sitio: ningún otro podía recogerlas. Ese es el motivo
            # de que reforzar la flota no sirviera de nada —los mercadistas
            # añadidos no encontraban ni un punto libre que tomar y el faltante
            # se quedaba clavado en los mismos 148.760 min.
            # Se recorre por LOCAL (afinidad): al adoptar una fila se intentan
            # adoptar también las demás filas de la misma tienda, para que la
            # atienda la misma persona siempre que quepa. Si no cabe, se toma
            # solo lo que entre y el resto queda libre para otro mercadista
            # —exactamente lo que hace el plan real de la empresa con los 48
            # locales que no caben en una sola persona.
            for inst in available_week:
                if week_sum >= objetivo_semana:
                    break
                if state.get_punto_key(inst) in puntos_en_pool:
                    continue
                afinidad = state.afinidad_de(inst)
                hermanas = [inst] + [
                    x
                    for x in available_week
                    if state.afinidad_de(x) == afinidad
                    and state.get_punto_key(x) != state.get_punto_key(inst)
                ]
                vistas = set()
                for cand in hermanas:
                    pk = state.get_punto_key(cand)
                    if pk in puntos_en_pool or pk in vistas:
                        continue
                    vistas.add(pk)
                    bloque = [x for x in available_week if state.get_punto_key(x) == pk]
                    bloque_sum = carga_combinada(bloque)
                    if week_sum + bloque_sum > objetivo_semana and week_pool:
                        continue
                    carga_mes_punto = carga_combinada(
                        [x for x in available if state.get_punto_key(x) == pk]
                    )
                    if (
                        compromiso_mes_merc + carga_mes_punto > cuota_mes()
                        and week_pool
                    ):
                        continue
                    if not state.puede_atender(merc_name, cand):
                        continue
                    state.asignar_punto(pk, merc_name)
                    compromiso_mes_merc += carga_mes_punto
                    week_pool.extend(bloque)
                    week_sum += bloque_sum
                    puntos_en_pool.add(pk)

            picked_ids = set(id(x) for x in week_pool)
            remaining = [x for x in remaining if id(x) not in picked_ids]
            if provincia_asignada:
                state.remaining_by_prov[provincia_asignada] = remaining
            else:
                for prov_k, lst in list(state.remaining_by_prov.items()):
                    state.remaining_by_prov[prov_k] = [x for x in lst if id(x) not in picked_ids]

            assigned_this_week = []
            week_remaining = list(week_pool)

            for day_idx, day in enumerate(dias_merc):
                if not week_remaining:
                    day_assigned = []
                else:
                    # Objetivo diario en minutos COMBINADOS (servicio + viaje):
                    # repartir lo que queda de la semana entre los días que
                    # quedan, sin pasar de la jornada de 480.
                    total_servicio_restante = carga_combinada(week_remaining)
                    dias_restantes = max(1, len(dias_merc) - day_idx)
                    if total_servicio_restante >= min_dia() * dias_restantes:
                        target_service = max(
                            min_dia(),
                            min(
                                int(math.ceil(total_servicio_restante / dias_restantes)),
                                cuota_dia(),
                            ),
                        )
                    else:
                        target_service = min(
                            int(math.ceil(total_servicio_restante / dias_restantes)),
                            cuota_dia(),
                        )

                    obligatorias = [x for x in week_remaining if x.get("required_day") == day]
                    for x in obligatorias:
                        week_remaining.remove(x)

                    flexibles = [x for x in week_remaining if not x.get("required_day")]

                    # Una visita cuyo servicio supera la jornada entera necesita
                    # un día ENTERO para ella sola: es la única excepción al
                    # tope diario. Si el día ya arranca con visitas de día fijo
                    # nunca le queda hueco, y como eso pasa en los cinco días de
                    # las cuatro semanas, el punto acababa siempre en
                    # pendientes. Aquí se le cede el día: las de día fijo
                    # vuelven al pool sin exigencia de día y se recolocan en
                    # otra jornada de la misma semana.
                    larga = next(
                        (c for c in flexibles if float(c.get("tiempo") or 0) > max_dia_flex()),
                        None,
                    )
                    if larga is not None:
                        for x in obligatorias:
                            x["required_day"] = None
                            week_remaining.append(x)
                        obligatorias = []
                        flexibles = [x for x in flexibles if x is not larga]
                        if larga in week_remaining:
                            week_remaining.remove(larga)
                        day_assigned = [larga]
                        total_dia = carga_combinada(day_assigned)
                    else:
                        day_assigned = list(obligatorias)
                        total_dia = carga_combinada(day_assigned)

                    ref_lat = (
                        day_assigned[-1]["lat"]
                        if day_assigned
                        else (sum(loc["lat"] for loc in flexibles) / len(flexibles) if flexibles else 0)
                    )
                    ref_lon = (
                        day_assigned[-1]["lon"]
                        if day_assigned
                        else (sum(loc["lon"] for loc in flexibles) / len(flexibles) if flexibles else 0)
                    )
                    ordenados_flex = ordenar_por_localidad_y_cercania(flexibles, ref_lat, ref_lon)

                    # Una visita más larga que la jornada necesita un día para
                    # ella sola, así que se coloca ANTES de empezar a llenar el
                    # día. Ordenados solo por cercanía nunca llegaba a estrenar
                    # ninguno: para cuando le tocaba el turno, el día ya tenía
                    # visitas y no cabía, y así en todos los días de todas las
                    # semanas hasta acabar en 'Pendientes_Sin_Asignar'.
                    if not day_assigned:
                        largas = [
                            c for c in ordenados_flex
                            if float(c.get("tiempo") or 0) > max_dia_flex()
                        ]
                        if largas:
                            primera = largas[0]
                            ordenados_flex = [primera] + [
                                c for c in ordenados_flex if c is not primera
                            ]

                    for cand in ordenados_flex:
                        if total_dia >= target_service:
                            break
                        # Excepción a la cuota diaria: una visita que por sí
                        # sola dura más que la jornada solo puede ir en un día
                        # para ella sola. Si el día está vacío se acepta; si no,
                        # ese punto nunca se podría atender.
                        cabe_sola = (
                            not day_assigned
                            and float(cand.get("tiempo") or 0) > max_dia_flex()
                        )
                        if total_dia + carga_combinada((cand,)) > max_dia_flex() and not cabe_sola:
                            # `continue`, no `break`: los candidatos vienen
                            # ordenados por cercanía, no por duración, así que
                            # tras uno que no cabe puede haber varios que sí.
                            # Cortando aquí el día quedaba a medio llenar y su
                            # carga se empujaba a otro mercadista.
                            continue
                        day_assigned.append(cand)
                        total_dia += carga_combinada((cand,))
                        flexibles.remove(cand)
                        if cand in week_remaining:
                            week_remaining.remove(cand)

                    if not day_assigned and flexibles:
                        day_assigned, flex_rest = construir_dia_por_cercania(flexibles, target_service)
                        week_remaining = flex_rest + [x for x in week_remaining if x.get("required_day")]

                    total_servicio_dia = sum(it["tiempo"] for it in day_assigned)
                    dia_de_una_visita_larga = (
                        len(day_assigned) == 1
                        and float(day_assigned[0].get("tiempo") or 0) > max_dia_flex()
                    )
                    if total_servicio_dia > max_dia_flex() and not dia_de_una_visita_larga:
                        recortar = []
                        acum = 0.0
                        for it in day_assigned:
                            if acum + it["tiempo"] <= max_dia_flex():
                                acum += it["tiempo"]
                                recortar.append(it)
                            else:
                                break
                        excedentes = [x for x in day_assigned if x not in recortar]
                        day_assigned = recortar
                        for ex in excedentes:
                            if ex not in week_remaining:
                                week_remaining.append(ex)

                # Deduplicar por punto fisico (distinguiendo instancias por tiempo / idx)
                seen_punto_dia = set()
                day_assigned_uniq = []
                for inst in day_assigned:
                    clave = (*clave_punto(inst), inst.get("tiempo"), inst.get("idx"))
                    if clave in seen_punto_dia:
                        if inst not in week_remaining:
                            week_remaining.append(inst)
                        continue
                    seen_punto_dia.add(clave)
                    day_assigned_uniq.append(inst)
                day_assigned = day_assigned_uniq

                for inst in day_assigned:
                    if day not in dias_por_idx[inst["idx"]]:
                        dias_por_idx[inst["idx"]].append(day)
                    if inst not in assigned_this_week:
                        assigned_this_week.append(inst)

                # Ruta optimizada por ciudad
                ordered_locations = compute_route_por_ciudad(day_assigned)

                fecha_base = _lunes_siguiente() + timedelta(days=offset_calendario(day))

                total_servicio_dia = sum(it["tiempo"] for it in ordered_locations)
                aplicar_almuerzo = total_servicio_dia != 480

                # Usar VisitaConfirmador para proceso limpio
                confirmador = VisitaConfirmador(
                    merc_name, day, fecha_base, aplicar_almuerzo,
                    tope_dia=max_dia_flex(),
                    presupuesto_mes=max(
                        0.0, cuota_mes() - servicio_emitido_merc
                    ),
                )

                visitas_rechazadas = []
                rechazadas_por_distancia = 0

                for orden_idx, it in enumerate(ordered_locations):
                    ok = confirmador.intentar_confirmar(it, travel_limit=MAX_TRAVEL_MINUTES_EXTREME)
                    if not ok:
                        visitas_rechazadas.append(it)
                        rechazadas_por_distancia += 1

                # Completar minimo con cercanas
                # Filas de día fijo que este mercadista ya atiende esta semana.
                # Se indexa por la misma clave que la regla de propiedad (la
                # fila), no por el local: si no, bastaba con atender una fila de
                # HIPERMARKET SUR para que el pool de relleno diera por
                # disponibles las demás filas del local aunque ya tuvieran dueño.
                owned_fixed = set(
                    state.get_punto_key(it)
                    for it in assigned_this_week
                    if it.get("fixed_mercadista")
                )

                def _disponible_para_este_merc(inst):
                    """
                    ¿Puede este mercadista tomar esta visita?

                    Un punto de venta pertenece a un solo mercadista: si ya
                    tiene dueño y no es este, la visita no es candidata. Estos
                    pools de relleno leían `remaining` sin comprobarlo, así que
                    un mercadista podía completar su día con una visita de un
                    punto que ya atendía otro y el punto acababa repartido entre
                    dos personas (52 puntos partidos en la salida real).
                    """
                    dueno = state.punto_mercadista.get(state.get_punto_key(inst))
                    return dueno is None or dueno == merc_name

                pool_completo = list(week_remaining) + [
                    inst
                    for inst in remaining
                    if inst["idx"] not in [v["idx"] for v in confirmador.visitas_confirmadas]
                    and (not inst.get("fixed_mercadista") or state.get_punto_key(inst) in owned_fixed)
                    and _disponible_para_este_merc(inst)
                ]

                travel_limits = [MAX_TRAVEL_MINUTES]
                if MAX_TRAVEL_MINUTES_RELAXED > MAX_TRAVEL_MINUTES:
                    travel_limits.append(MAX_TRAVEL_MINUTES_RELAXED)

                for travel_limit in travel_limits:
                    pool_actual = list(week_remaining) if travel_limit == MAX_TRAVEL_MINUTES else pool_completo
                    intentos_sin_agregar = 0
                    max_intentos = len(pool_actual) * 2

                    while (
                        confirmador.combinado_total < min_dia()
                        and pool_actual
                        and intentos_sin_agregar < max_intentos
                    ):
                        candidatos = [
                            x
                            for x in pool_actual
                            if (not x.get("required_day")) or (x.get("required_day") == day)
                        ]
                        if not candidatos:
                            break
                        if confirmador.prev_lat is not None:
                            candidatos = sorted(
                                candidatos,
                                key=lambda loc: haversine_km(
                                    confirmador.prev_lat, confirmador.prev_lon, loc["lat"], loc["lon"]
                                ),
                            )
                        agregado = False
                        for it_extra in candidatos:
                            if it_extra["idx"] in [v["idx"] for v in confirmador.visitas_confirmadas]:
                                continue
                            if it_extra.get("required_day") and it_extra.get("required_day") != day:
                                continue

                            ok = confirmador.intentar_confirmar(it_extra, travel_limit=travel_limit)
                            if not ok:
                                continue

                            # El punto queda ligado a este mercadista para el
                            # resto del proceso: si otro lo tomara después, sus
                            # visitas quedarían repartidas entre dos personas.
                            state.punto_mercadista.setdefault(
                                state.get_punto_key(it_extra), merc_name
                            )
                            assigned_this_week.append(it_extra)
                            dias_por_idx[it_extra["idx"]].append(day)

                            if it_extra in week_remaining:
                                week_remaining.remove(it_extra)
                            if it_extra in pool_actual:
                                pool_actual.remove(it_extra)
                            if it_extra in remaining:
                                remaining.remove(it_extra)
                                if provincia_asignada:
                                    state.remaining_by_prov[provincia_asignada] = remaining

                            agregado = True
                            intentos_sin_agregar = 0
                            break

                        if not agregado:
                            intentos_sin_agregar += 1
                            if intentos_sin_agregar >= len(candidatos):
                                break

                    if confirmador.combinado_total >= min_dia():
                        break

                # Ultimo intento relajado
                if confirmador.visitas_confirmadas and confirmador.combinado_total < min_dia():
                    pool_final = list(week_remaining) + [
                        inst
                        for inst in remaining
                        if inst["idx"] not in [v["idx"] for v in confirmador.visitas_confirmadas]
                        and (not inst.get("fixed_mercadista") or state.get_punto_key(inst) in owned_fixed)
                        and _disponible_para_este_merc(inst)
                    ]
                    if pool_final and confirmador.prev_lat is not None:
                        pool_final = sorted(
                            pool_final,
                            key=lambda loc: haversine_km(
                                confirmador.prev_lat, confirmador.prev_lon, loc["lat"], loc["lon"]
                            ),
                        )
                        for it_final in pool_final[:10]:
                            if it_final["idx"] in [v["idx"] for v in confirmador.visitas_confirmadas]:
                                continue
                            if it_final.get("required_day") and it_final.get("required_day") != day:
                                continue

                            ok = confirmador.intentar_confirmar(it_final, travel_limit=20)
                            if not ok:
                                continue

                            state.punto_mercadista.setdefault(
                                state.get_punto_key(it_final), merc_name
                            )
                            assigned_this_week.append(it_final)
                            dias_por_idx[it_final["idx"]].append(day)
                            if it_final in week_remaining:
                                week_remaining.remove(it_final)
                            if it_final in remaining:
                                remaining.remove(it_final)
                                if provincia_asignada:
                                    state.remaining_by_prov[provincia_asignada] = remaining
                            break

                if confirmador.combinado_total < min_dia():
                    print(
                        f"      [!] Bajo minimo en {day} para {merc_name}: "
                        f"{confirmador.combinado_total:.1f} min combinado (< {min_dia()})."
                    )

                # Rechazadas: devolver al pool
                if visitas_rechazadas:
                    if rechazadas_por_distancia > 0:
                        print(
                            f"      [!] Distancia > {MAX_TRAVEL_MINUTES} min en {day} para {merc_name}: "
                            f"{rechazadas_por_distancia} visitas devueltas al pool."
                        )
                    for rej in visitas_rechazadas:
                        if rej in assigned_this_week:
                            assigned_this_week.remove(rej)
                        if day in dias_por_idx[rej["idx"]]:
                            dias_por_idx[rej["idx"]].remove(day)
                        # El día fijo es una preferencia de reparto dentro de la
                        # semana, no un requisito del cliente: sale de
                        # `dias_fijos_por_frecuencia`, que solo busca separar las
                        # visitas. Si ese día concreto ya no tiene hueco, mantener
                        # la exigencia condena la visita a pendientes, porque el
                        # resto de días la descartan por `required_day != day`.
                        # Liberarla conserva la frecuencia semanal, que es lo que
                        # de verdad importa.
                        if rej.get("required_day") == day:
                            rej["required_day"] = None
                        if rej not in week_remaining:
                            week_remaining.append(rej)

                if confirmador.day_summaries:
                    state.all_day_summaries.extend(confirmador.day_summaries)
                    # Se acumula con la MISMA vara con la que se mide el
                    # presupuesto mensual dos líneas más arriba
                    # (`cuota_mes() - servicio_emitido_merc`, que
                    # el confirmador compara contra `carga_jornada`). Sumar solo
                    # el servicio mientras el gasto se mide en combinado hacía
                    # que, con el modelo "con tiempo de desplazamiento", el
                    # contador fuera por detrás del consumo real y el mes se
                    # cerrara por encima de la cuota. Con el modelo "sin
                    # desplazamiento" el viaje aporta 0 y esto equivale a la
                    # suma de servicio de siempre.
                    servicio_emitido_merc += sum(
                        carga_jornada(
                            float(r.get("Tiempo Servicio (min)") or 0),
                            float(r.get("Tiempo entre sucursal (min)") or 0),
                        )
                        for r in confirmador.day_summaries
                    )

            # Asignar al df
            for inst in assigned_this_week:
                idx = inst["idx"]
                if not df.at[idx, "Mercadista"]:
                    df.at[idx, "Mercadista"] = merc_name
                dias_actual = df.at[idx, "Dia"]
                for d in dias_por_idx.get(idx, []):
                    if dias_actual:
                        if d not in dias_actual:
                            dias_actual += ", " + d
                    else:
                        dias_actual = d
                    col = columnas_merc[dias_merc.index(d)] if d in dias_merc else None
                    if col is None:
                        continue
                    # SABADO/DOMINGO no existen en el maestro hasta que hay
                    # cuadrilla de fin de semana: se crean al vuelo para no
                    # arrastrar dos columnas vacías en las demás ejecuciones.
                    if col not in df.columns:
                        df[col] = ""
                    df.at[idx, col] = "X"
                df.at[idx, "Dia"] = dias_actual
                if not df.at[idx, "Fecha"]:
                    primera = dias_por_idx.get(idx, [])
                    if primera:
                        df.at[idx, "Fecha"] = state.fechas_map[primera[0]]

            # Devolver sobrantes al pool
            if week_remaining:
                if provincia_asignada:
                    state.remaining_by_prov[provincia_asignada] = state.remaining_by_prov.get(
                        provincia_asignada, []
                    ) + list(week_remaining)
                else:
                    for inst in week_remaining:
                        # Por ZONA: el diccionario está indexado por zona, y una
                        # clave suelta quedaría fuera del alcance de todo
                        # mercadista (visitas perdidas sin aparecer siquiera en
                        # pendientes).
                        state.remaining_by_prov.setdefault(state.zona_de(inst), []).append(inst)

            # Combinado, para poder compararlo con los topes mensuales
            minutos_merc_mes += carga_combinada(assigned_this_week)

        state.mercadistas_asignados += 1
        pendientes_por_zona[provincia_asignada] -= 1

    # Progreso final
    mostrar_progreso(state.total_visitas, state.total_visitas, "      Progreso")
    print(f"      -> {state.mercadistas_asignados} mercadistas asignados\n")

    return state
