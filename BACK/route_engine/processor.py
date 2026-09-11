"""
Orquestador principal: procesar_minoristas.
Coordina todos los modulos para producir el Excel de salida.
"""

import math
from typing import Callable, Optional

import pandas as pd

from route_engine.assignment import carga_combinada, ejecutar_asignacion
from route_engine.classification import obtener_categoria
from route_engine.excel_reader import (
    corregir_coordenadas_df,
    crear_instancias_visita,
    determinar_provincias,
    filtrar_por_canal_y_cadenas,
    inicializar_columnas,
    leer_excel_entrada,
)
from route_engine.config import (
    CONVERTIR_A_FIN_DE_SEMANA,
    CUADRILLA_FIN_SEMANA_ACTIVA,
    HOLGURA_FLOTA,
    RADIO_CENTRO_ZONA_KM,
    desregistrar_mercadistas_fin_semana,
    carga_jornada,
    cuota_dia,
    cuota_dia_temporal,
    cuota_mes,
    dia_equivalente_fin_semana,
    jornada_incluye_viaje,
    jornada_incluye_viaje_temporal,
    limpiar_mercadistas_fin_semana,
    mercadistas_fin_semana,
    registrar_mercadistas_fin_semana,
)
from route_engine.excel_writer import generar_excel_salida
from route_engine.geo import haversine_km
from route_engine.fleet_packing import planificar_flota_por_capacidad, resumen_plan
from route_engine.load_grouping import (
    ETIQUETAS,
    TIPO_CADENA,
    TIPO_CANAL,
    TIPO_MULTICANAL,
    TIPO_ZONA,
    cadenas_agrupadas,
    limpiar_grupos_multicanal,
    normalizar_tipo_carga,
    resumen_grupos,
    set_grupos_multicanal,
    validar_datos_suficientes,
)
from route_engine.mapbox import obtener_direccion_desde_coordenadas
from route_engine.rescue import (
    ejecutar_pase_rescate,
    disolver_mercadistas_infrautilizados,
    ejecutar_red_seguridad,
    reasignar_puntos_varados,
    reinyectar_frecuencias_incompletas,
)
from route_engine.scheduling import mostrar_progreso
from route_engine.validation import recolectar_pendientes, validar_resultado

ProgressCallback = Callable[[int, str], None]


def _asignar_con_refuerzo_de_flota(
    visit_instances, mercadistas_plan, df, tipo_ruta, notify, propiedad_puntos=None,
    tipo_carga=None,
):
    """
    Asigna y, si quedan visitas sin colocar, añade mercadistas en las zonas
    afectadas y vuelve a asignar.

    El plan inicial se calcula antes de conocer las rutas: estima el
    desplazamiento con una reserva media por visita. Cuando la ruta real sale
    más cara que la reserva, la zona se queda sin capacidad y el excedente cae
    en 'Pendientes_Sin_Asignar' aunque el resto de la flota tenga hueco (no
    puede tomarlo: está en otra zona, a otra distancia). Aquí se mide el
    faltante real y se refuerza solo la zona que lo necesita.
    """
    from route_engine.config import MAX_RONDAS_REFUERZO_FLOTA, cuota_mes

    plan = list(mercadistas_plan)
    mejor_plan, mejor_state, mejor_faltante = plan, None, None

    for ronda in range(1, MAX_RONDAS_REFUERZO_FLOTA + 1):
        state = ejecutar_asignacion(
            visit_instances, plan, df, tipo_ruta=tipo_ruta,
            propiedad_inicial=propiedad_puntos, tipo_carga=tipo_carga,
        )

        # Solo se refuerzan ZONAS REALES, es decir las que el planificador creó
        # como grupo geográfico compacto. `remaining_by_prov` tiene además una
        # clave de descarte para las visitas que la clusterización no pudo
        # ubicar (servicio 0 o coordenadas inválidas): esas no forman un grupo
        # compacto, están repartidas por todo el país. Crear un mercadista para
        # ellas producía una ruta con puntos a 297 km entre sí —Quito, Guayaquil
        # y Los Ríos en la misma jornada—, justo lo que la zonificación evita.
        zonas_reales = {z for _, z in plan if z}
        faltante_por_zona = {
            zona: carga_combinada(insts)
            for zona, insts in state.remaining_by_prov.items()
            if insts and zona in zonas_reales
        }
        total_faltante = sum(faltante_por_zona.values())

        # Se conserva el mejor plan visto, no el último: una ronda puede
        # empeorar y no queremos quedarnos con ella.
        if mejor_faltante is None or total_faltante < mejor_faltante:
            mejor_plan, mejor_state, mejor_faltante = list(plan), state, total_faltante
        elif ronda > 1:
            # Añadir mercadistas dejó de reducir el faltante: lo que queda no
            # es un problema de capacidad de flota (sus dueños tienen mes
            # libre) sino de que esas visitas no caben en ningún día suelto.
            # Seguir reforzando solo añadiría mercadistas vacíos.
            print(
                f"      -> Ronda {ronda}: el faltante no baja ({total_faltante:.0f} min); "
                f"se detiene el refuerzo y se conserva el plan de "
                f"{len(mejor_plan)} mercadista(s)"
            )
            break

        if not faltante_por_zona:
            break
        if ronda == MAX_RONDAS_REFUERZO_FLOTA:
            break

        refuerzos = []
        siguiente = len(plan) + 1
        for zona, carga in sorted(faltante_por_zona.items(), key=lambda kv: -kv[1]):
            n_extra = max(1, int(math.ceil(carga / cuota_mes())))
            for _ in range(n_extra):
                refuerzos.append((f"Mercadista {siguiente:02d}", zona))
                siguiente += 1

        print(
            f"      -> Ronda {ronda}: quedan {total_faltante:.0f} min sin colocar en "
            f"{len(faltante_por_zona)} zona(s); se añaden {len(refuerzos)} mercadista(s)"
        )
        notify(
            55,
            f"Ajustando la flota: {len(refuerzos)} mercadista(s) más para cubrir "
            f"las visitas que no cabían...",
        )
        plan.extend(refuerzos)

    # Reasignar con el mejor plan si la última ronda no fue la mejor, para que
    # el estado devuelto se corresponda con el plan que se reporta.
    if mejor_state is None:
        mejor_state = ejecutar_asignacion(
            visit_instances, mejor_plan, df, tipo_ruta=tipo_ruta,
            propiedad_inicial=propiedad_puntos, tipo_carga=tipo_carga,
        )
    mejor_state.mercadistas_plan = mejor_plan
    return mejor_state


def _control_mes(state, etapa):
    """
    Comprueba que ningún mercaderista supere el mes contratado de 9.600 min.

    Es el límite duro del modelo: la jornada es flexible (un día puede llegar a
    600 min) precisamente porque el mes no lo es. Se reporta en el log para que
    un incumplimiento no pase inadvertido.
    """
    from collections import defaultdict
    tot = defaultdict(float)
    for r in state.all_day_summaries:
        # Se mide con el modelo activo: contra la cuota COMBINADA hay que sumar
        # lo que de verdad consume jornada, o el control da por bueno un mes que
        # con "tiempo de desplazamiento" está por encima de las 9.600.
        tot[r.get("Mercadista", "")] += carga_jornada(
            float(r.get("Tiempo Servicio (min)") or 0),
            float(r.get("Tiempo entre sucursal (min)") or 0),
        )
    malos = {m: v for m, v in tot.items() if v > cuota_mes() + 0.5}
    print(f"      [mes] {etapa:34s} infractores={len(malos):2d} max={max(tot.values(), default=0):.0f}")
    return malos


def _replanificar_puntos_completos(state, restantes):
    """
    Devuelve los puntos con visitas sueltas a replanificar, ENTEROS.

    Un punto de venta lo atiende siempre el mismo mercaderista. Si se
    replanificaran solo las visitas sobrantes, esas irían a una plaza nueva y el
    punto quedaría partido entre dos personas, que es exactamente lo que la
    regla prohíbe. Así que se retira del calendario todo lo que el dueño ya tenía
    agendado de ese punto y se replanifica el punto completo.

    Se retira por (mercaderista, local): las filas de una misma tienda que
    atiende la misma persona vuelven juntas y tienden a quedarse juntas.
    """
    from route_engine.scheduling import clave_punto

    # Se indexa por LOCAL, sin el dueño.
    #
    # Con la clave (dueño, local) el pase se rompía en cuanto un punto perdía
    # dueño —cosa que hace este mismo pase al terminar—: en la vuelta siguiente
    # su dueño era None, sus visitas ya agendadas quedaban bajo otro
    # mercaderista, no coincidían con la clave y no se retiraban. El punto se
    # replanificaba igualmente y acababa atendido por dos personas a la vez: 12
    # filas partidas en la salida real, justo lo que la regla de negocio
    # prohíbe. Sin el dueño en la clave, retirar y recomponer miran siempre el
    # mismo conjunto y el punto se mueve entero o no se mueve.
    afectados = {clave_punto(inst) for inst in restantes}

    # 1) Sacar del calendario lo ya agendado de esos puntos.
    def _clave_resumen(s):
        try:
            return (
                round(float(s.get("Latitud") or 0), 6),
                round(float(s.get("Longitud") or 0), 6),
                str(s.get("Descripción", "")).strip(),
            )
        except (TypeError, ValueError):
            return None

    retirados = 0
    conservados = []
    for s in state.all_day_summaries:
        if _clave_resumen(s) in afectados:
            retirados += 1
            continue
        conservados.append(s)
    state.all_day_summaries = conservados

    # 2) Recomponer el conjunto completo de visitas de esos puntos.
    completos = []
    claves_pk = set()
    for inst in state.visit_instances:
        if clave_punto(inst) in afectados:
            completos.append(inst)
            claves_pk.add(state.get_punto_key(inst))

    # Las instancias sueltas que no estaban en el maestro (rescate) se añaden
    # solo si su punto no vino ya por la vía anterior, para no duplicar visitas.
    for inst in restantes:
        if state.get_punto_key(inst) not in claves_pk:
            completos.append(inst)
            claves_pk.add(state.get_punto_key(inst))

    # 3) Liberar la propiedad: el reparto nuevo decide dueño para el punto entero.
    for pk in claves_pk:
        state.punto_mercadista.pop(pk, None)
    state.sin_hueco = []
    for zona in list(state.remaining_by_prov):
        state.remaining_by_prov[zona] = []

    if retirados:
        print(
            f"      -> Se retiran {retirados} visita(s) ya agendadas para "
            f"replanificar {len(claves_pk)} punto(s) enteros"
        )
    return completos


def _absorber_pendientes_abriendo_plazas(state, df, tipo_ruta, notify, max_rondas=8):
    """
    Abre mercaderistas nuevos para las visitas que quedaron sin colocar.

    Reutiliza el mismo empaquetado por capacidad mensual sobre lo que sobra: las
    visitas pendientes se agrupan en cajas de 9.600 min con el mismo límite
    geográfico, y cada caja se programa con el asignador normal. Iterar es
    necesario porque programar el día puede volver a dejar restos, aunque cada
    vuelta sean muchos menos.
    """
    from route_engine.assignment import ejecutar_asignacion

    anterior = None
    for ronda in range(1, max_rondas + 1):
        # Dos orígenes: lo que nunca llegó a adoptar nadie (`remaining_by_prov`)
        # y lo que sí tenía dueño pero no cupo en ninguna de sus jornadas
        # (`sin_hueco`). El segundo grupo es el mayoritario y era justo el que
        # se daba por perdido.
        restantes = [
            inst
            for lst in state.remaining_by_prov.values()
            for inst in lst
            if float(inst.get("tiempo") or 0) > 0
        ]
        vistos = {id(i) for i in restantes}
        restantes += [
            inst
            for inst in state.sin_hueco
            if float(inst.get("tiempo") or 0) > 0 and id(inst) not in vistos
        ]
        if not restantes:
            return

        # Sin avance respecto a la vuelta anterior no tiene sentido seguir:
        # cada vuelta desmonta agenda ya hecha para rehacerla, y repetirlo sin
        # ganancia solo desordena lo que ya estaba bien colocado.
        if anterior is not None and len(restantes) >= anterior:
            return
        anterior = len(restantes)

        restantes = _replanificar_puntos_completos(state, restantes)
        if not restantes:
            return

        siguiente = len(state.mercadistas_plan) + 1
        plan_extra, propiedad_extra, _desglose = planificar_flota_por_capacidad(
            restantes,
            numero_inicial=siguiente,
            cajas_previas=_hueco_libre_de_la_plantilla(state),
            tipo_carga=getattr(state, "tipo_carga", None),
        )

        nuevos = {m for m, _z in plan_extra}
        para_nuevos, para_existentes = [], []
        for inst in restantes:
            dueno = propiedad_extra.get(state.get_punto_key(inst))
            (para_nuevos if dueno in nuevos else para_existentes).append(inst)

        minutos = sum(float(i.get("tiempo") or 0) for i in restantes)
        print(
            f"      -> Absorción {ronda}: {len(restantes)} visita(s) sin colocar "
            f"({minutos:.0f} min); {len(para_existentes)} caben en la plantilla "
            f"actual, se abren {len(plan_extra)} plaza(s) para el resto"
        )
        if plan_extra:
            notify(
                62,
                f"Abriendo {len(plan_extra)} mercadista(s) más para cubrir las visitas "
                f"que quedaban sin asignar...",
            )

        # 1) Lo que cabe en la plantilla actual lo coloca el rescate, que sí
        #    conoce la carga real de cada jornada ya agendada.
        zona_de_merc = dict(state.mercadistas_plan)
        state.punto_mercadista.update(
            {pk: m for pk, m in propiedad_extra.items() if m not in nuevos}
        )
        state.sincronizar_grupos()
        for inst in para_existentes:
            dueno = propiedad_extra.get(state.get_punto_key(inst))
            zona = zona_de_merc.get(dueno)
            if zona:
                inst["zona"] = zona
                state.remaining_by_prov.setdefault(zona, []).append(inst)

        # 2) Lo que no cabía abre plazas y se programa desde cero.
        if plan_extra and para_nuevos:
            state_extra = ejecutar_asignacion(
                para_nuevos, plan_extra, df, tipo_ruta=tipo_ruta,
                tipo_carga=getattr(state, "tipo_carga", None),
                propiedad_inicial={
                    pk: m for pk, m in propiedad_extra.items() if m in nuevos
                },
            )
            state.all_day_summaries.extend(state_extra.all_day_summaries)
            state.mercadistas_plan = list(state.mercadistas_plan) + list(plan_extra)
            state.punto_mercadista.update(state_extra.punto_mercadista)
            state.grupo_de_punto.update(getattr(state_extra, "grupo_de_punto", {}))
            state.sincronizar_grupos()
            state.mercadistas_asignados += state_extra.mercadistas_asignados
            for zona, lst in state_extra.remaining_by_prov.items():
                if lst:
                    state.remaining_by_prov.setdefault(zona, []).extend(lst)

        ejecutar_pase_rescate(state)


def _candidato_a_fin_de_semana(state, pendientes, descartados):
    """
    Mercaderista de lunes a viernes al que compensa pasar a miércoles-domingo.

    Se busca al que más mes libre tenga entre los que trabajan CERCA de alguna
    visita atascada: convertir a alguien lejano no serviría de nada —no podría
    atenderla— y le cambiaría la jornada para nada.

    Devuelve (nombre, visitas que podría recoger) o None.
    """
    ya_en_cuadrilla = mercadistas_fin_semana()
    coords_pendientes = [
        (i, float(i.get("lat") or 0), float(i.get("lon") or 0))
        for i in pendientes
        if float(i.get("lat") or 0) or float(i.get("lon") or 0)
    ]
    if not coords_pendientes:
        return None

    mejor = None
    for caja in _hueco_libre_de_la_plantilla(state):
        merc = caja["merc"]
        if merc in ya_en_cuadrilla or merc in descartados:
            continue
        libre = cuota_mes() - float(caja.get("carga") or 0)
        if libre < cuota_dia():  # menos de una jornada libre: no aporta
            continue
        coords = caja.get("coords") or []
        if not coords:
            continue
        cercanas = [
            inst
            for inst, lat, lon in coords_pendientes
            if min(haversine_km(lat, lon, c[0], c[1]) for c in coords)
            <= RADIO_CENTRO_ZONA_KM
        ]
        if not cercanas:
            continue
        # Entre los que pueden ayudar, el que más hueco tiene: cada conversión
        # cambia la jornada de una persona real, así que cuantas menos, mejor.
        clave = (-libre, merc)
        if mejor is None or clave < mejor[0]:
            mejor = (clave, merc, cercanas)

    return None if mejor is None else (mejor[1], mejor[2])


def _convertir_plantilla_a_fin_de_semana(state, df, tipo_ruta, pendientes, notify,
                                         max_conversiones=12):
    """
    Pasa a jornada de miércoles-domingo a gente YA CONTRATADA, de una en una.

    Lo que manda visitas al fin de semana no es falta de capacidad sino choque
    de días. Medido sobre el rutero nacional, la cuadrilla abría 16 plazas para
    78.245 min de trabajo mientras la plantilla de lunes a viernes tenía 119.330
    min de mes libre: el trabajo cabía de sobra, los días no.

    Un mercaderista al 40% que corre su jornada a miércoles-domingo mantiene sus
    cinco días y su cuota, pero ahora sus huecos caen en días que nadie más usa,
    y ahí sí entran las visitas atascadas.

    Cada conversión se hace y se COMPRUEBA: reprogramar la cartera entera de
    alguien puede salir peor que dejarla como estaba. Si al reasignar pierde
    visitas o no recoge ninguna de las atascadas, se deshace por completo y se
    prueba con otro. Un intento fallido no deja rastro.

    Devuelve las visitas que siguen sin colocarse.
    """
    from route_engine.assignment import ejecutar_asignacion

    restantes = list(pendientes)
    descartados: set = set()
    convertidos: list = []

    while restantes and len(convertidos) < max_conversiones:
        candidato = _candidato_a_fin_de_semana(state, restantes, descartados)
        if candidato is None:
            break
        merc, cercanas = candidato

        suyas = [
            inst
            for inst in state.visit_instances
            if state.punto_mercadista.get(inst.get("punto_key", inst.get("idx"))) == merc
        ]
        # Foto de todo lo que se va a tocar, para poder deshacerlo.
        dias_previos = [(inst, inst.get("required_day")) for inst in suyas + cercanas]
        zonas_previas = [(inst, inst.get("zona")) for inst in suyas + cercanas]
        filas_previas = [
            fila for fila in state.all_day_summaries
            if str(fila.get("Mercadista") or "").strip() == merc
        ]
        agendadas_antes = len(filas_previas)

        state.all_day_summaries[:] = [
            fila for fila in state.all_day_summaries
            if str(fila.get("Mercadista") or "").strip() != merc
        ]
        registrar_mercadistas_fin_semana([merc])
        # Los días fijos venían escritos en lunes-viernes: se corren al mismo
        # hueco de la nueva jornada (lunes -> miércoles, martes -> jueves...),
        # conservando la separación entre visitas que da sentido a la frecuencia.
        # El asignador normaliza la clave de zona a mayúsculas y sin tildes
        # (`norm_provincia`), así que la clave tiene que estar ya en esa forma o
        # no encontraría las visitas de esta persona.
        zona_fs = f"FS-{merc.upper().replace(' ', '-')}"
        for inst in suyas + cercanas:
            dia = inst.get("required_day")
            if dia:
                inst["required_day"] = dia_equivalente_fin_semana(dia)
            # El asignador reparte por zona: sin esto las visitas quedarían en
            # la zona de su plan anterior y esta persona no vería trabajo.
            inst["zona"] = zona_fs

        propiedad = {
            inst.get("punto_key", inst.get("idx")): merc for inst in suyas
        }
        estado_fs = ejecutar_asignacion(
            suyas + cercanas, [(merc, zona_fs)], df, tipo_ruta=tipo_ruta,
            tipo_carga="zona", propiedad_inicial=propiedad,
        )

        sobran = list(estado_fs.sin_hueco)
        for lst in estado_fs.remaining_by_prov.values():
            sobran.extend(lst)
        ids_sobran = {id(i) for i in sobran}
        recogidas = [i for i in cercanas if id(i) not in ids_sobran]
        agendadas_ahora = len(estado_fs.all_day_summaries)

        # Se acepta solo si la persona no pierde visitas propias y además
        # recoge la MAYORÍA de las atascadas que tenía cerca. Recoger cuatro de
        # sesenta no arregla el atasco y sí retira cinco días de lunes a viernes
        # del reparto, que es capacidad que otros necesitaban.
        suficientes = len(recogidas) >= max(1, len(cercanas) // 2)
        if agendadas_ahora < agendadas_antes or not suficientes:
            for inst, dia in dias_previos:
                inst["required_day"] = dia
            for inst, zona in zonas_previas:
                inst["zona"] = zona
            desregistrar_mercadistas_fin_semana([merc])
            state.all_day_summaries.extend(filas_previas)
            descartados.add(merc)
            continue

        state.all_day_summaries.extend(estado_fs.all_day_summaries)
        state.punto_mercadista.update(estado_fs.punto_mercadista)
        state.grupo_de_punto.update(getattr(estado_fs, "grupo_de_punto", {}))
        state.sincronizar_grupos()
        convertidos.append(merc)

        ids_recogidas = {id(i) for i in recogidas}
        # Las que este mercaderista NO pudo recoger vuelven a su estado de
        # partida: siguen buscando dueño, y si se quedaran con el día ya corrido
        # y la zona de esta persona, la siguiente fase se lo correría otra vez
        # (lunes -> miércoles -> viernes) y acabarían sin poder colocarse.
        for inst, dia in dias_previos:
            if id(inst) not in ids_recogidas:
                inst["required_day"] = dia
        for inst, zona in zonas_previas:
            if id(inst) not in ids_recogidas:
                inst["zona"] = zona

        restantes = [i for i in restantes if id(i) not in ids_recogidas]

    if convertidos:
        print(
            f"      -> Fin de semana: {len(convertidos)} mercaderista(s) ya contratados "
            f"pasan a miércoles-domingo en vez de abrir plazas nuevas"
        )
        notify(
            64,
            f"Pasando {len(convertidos)} mercadista(s) a jornada de fin de semana para "
            f"cubrir las visitas que no cabían de lunes a viernes...",
        )
    return restantes


def _absorber_pendientes_fin_de_semana(state, df, tipo_ruta, notify, max_rondas=3):
    """
    Cuadrilla de fin de semana para lo que no cupo de lunes a viernes.

    Cada mercaderista sigue trabajando CINCO días; los de esta cuadrilla los
    tienen corridos a miércoles-domingo. Por eso no da más minutos: da más
    ranuras de día. Lo que manda visitas a pendientes no es la falta de
    capacidad —sobra— sino el choque de días fijos: a una misma persona le
    coinciden varios puntos que exigen el martes, ese día revienta los 480 y el
    resto de su semana queda a medias. Con sábado y domingo sobre la mesa esos
    puntos encuentran hueco sin partirse.

    Solo se usa con reparto POR ZONA, y solo si después de todo lo demás siguen
    quedando visitas sin colocar: si el archivo cabe de lunes a viernes, esta
    fase no existe y nada cambia respecto a antes.

    El punto se mueve ENTERO (`_replanificar_puntos_completos`): la regla de un
    punto = un mercaderista no se relaja por trabajar en fin de semana.
    """
    from route_engine.assignment import ejecutar_asignacion

    anterior = None
    abiertos = 0
    for ronda in range(1, max_rondas + 1):
        restantes = [
            inst
            for lst in state.remaining_by_prov.values()
            for inst in lst
            if float(inst.get("tiempo") or 0) > 0
        ]
        vistos = {id(i) for i in restantes}
        restantes += [
            inst
            for inst in state.sin_hueco
            if float(inst.get("tiempo") or 0) > 0 and id(inst) not in vistos
        ]
        if not restantes:
            break

        if anterior is not None and len(restantes) >= anterior:
            break
        anterior = len(restantes)

        restantes = _replanificar_puntos_completos(state, restantes)
        if not restantes:
            break

        # Antes de contratar: pasar a jornada de fin de semana a gente que ya
        # está y tiene mes libre. Estas visitas necesitan DÍAS distintos, no
        # manos nuevas. Cada conversión se comprueba y se deshace si empeora.
        #
        # Va apagado por defecto porque es un canje de negocio, no una mejora
        # gratuita. Medido sobre el rutero nacional:
        #     apagado -> 65 mercaderistas, 96,0% de cobertura
        #     encendido -> 59 mercaderistas, 91,0% de cobertura
        # Seis personas menos a cambio de 191 visitas que habría que colocar a
        # mano desde el calendario. Se enciende con CONVERTIR_A_FIN_DE_SEMANA=1.
        if CONVERTIR_A_FIN_DE_SEMANA:
            restantes = _convertir_plantilla_a_fin_de_semana(
                state, df, tipo_ruta, restantes, notify
            )
        if not restantes:
            ejecutar_pase_rescate(state)
            break

        siguiente = len(state.mercadistas_plan) + 1
        # `cajas_previas` son solo los de la cuadrilla ya abierta: primero se
        # llena a quien ya trabaja el fin de semana y solo después se abren
        # plazas nuevas. La plantilla de lunes a viernes queda fuera a
        # propósito: ya tuvo su oportunidad en las fases anteriores y es donde
        # estas visitas no caben.
        ya_en_cuadrilla = mercadistas_fin_semana()
        plan_fs, propiedad_fs, _desglose = planificar_flota_por_capacidad(
            restantes,
            numero_inicial=siguiente,
            cajas_previas=(
                _hueco_libre_de_la_plantilla(state, solo=ya_en_cuadrilla)
                if ya_en_cuadrilla
                else None
            ),
            tipo_carga="zona",
        )
        if not plan_fs:
            break

        nombres_fs = [m for m, _z in plan_fs]
        registrar_mercadistas_fin_semana(nombres_fs)

        # Los días fijos venían escritos en lunes-viernes: se corren al mismo
        # hueco de la jornada de fin de semana (lunes -> miércoles, martes ->
        # jueves...), conservando la separación entre visitas del patrón.
        for inst in restantes:
            dia = inst.get("required_day")
            if dia:
                inst["required_day"] = dia_equivalente_fin_semana(dia)

        minutos = sum(float(i.get("tiempo") or 0) for i in restantes)
        print(
            f"      -> Fin de semana {ronda}: {len(restantes)} visita(s) sin colocar "
            f"({minutos:.0f} min) -> {len(plan_fs)} mercaderista(s) de "
            f"miércoles a domingo"
        )
        notify(
            64,
            f"Abriendo {len(plan_fs)} mercadista(s) de fin de semana para las visitas "
            f"que no cupieron de lunes a viernes...",
        )

        state_fs = ejecutar_asignacion(
            restantes, plan_fs, df, tipo_ruta=tipo_ruta, tipo_carga="zona",
            propiedad_inicial=dict(propiedad_fs),
        )
        state.all_day_summaries.extend(state_fs.all_day_summaries)
        state.mercadistas_plan = list(state.mercadistas_plan) + list(plan_fs)
        state.punto_mercadista.update(state_fs.punto_mercadista)
        state.grupo_de_punto.update(getattr(state_fs, "grupo_de_punto", {}))
        state.sincronizar_grupos()
        state.mercadistas_asignados += state_fs.mercadistas_asignados
        for zona, lst in state_fs.remaining_by_prov.items():
            if lst:
                state.remaining_by_prov.setdefault(zona, []).extend(lst)
        state.sin_hueco.extend(state_fs.sin_hueco)
        abiertos += len(plan_fs)

        ejecutar_pase_rescate(state)

    return abiertos


def _hueco_libre_de_la_plantilla(state, solo=None):
    """
    Capacidad mensual que le queda a cada mercaderista ya contratado.

    `solo` acota el cálculo a un subconjunto de nombres. Lo usa la cuadrilla de
    fin de semana para llenar primero a los que ya tiene antes de abrir plazas
    nuevas: sin ese filtro, el empaquetado ofrecería el hueco de la plantilla de
    lunes a viernes, que es justo donde esas visitas no caben.

    Se mide sobre lo realmente agendado (`all_day_summaries`), no sobre el plan:
    lo que importa para saber si alguien puede asumir más trabajo es lo que
    tiene puesto en el calendario.
    """
    usado = {}
    coords = {}
    for s in state.all_day_summaries:
        merc = s.get("Mercadista", "")
        if not merc:
            continue
        usado[merc] = usado.get(merc, 0.0) + carga_jornada(
            float(s.get("Tiempo Servicio (min)", 0) or 0),
            float(s.get("Tiempo entre sucursal (min)", 0) or 0),
        )
        try:
            punto = (round(float(s.get("Latitud") or 0), 6), round(float(s.get("Longitud") or 0), 6))
        except (TypeError, ValueError):
            continue
        if punto != (0.0, 0.0):
            coords.setdefault(merc, set()).add(punto)

    zona_de_merc = dict(state.mercadistas_plan)
    grupo_de_merc = getattr(state, "grupo_de_merc", {})
    return [
        {
            "merc": merc,
            "zona": zona_de_merc.get(merc),
            "carga": carga,
            "coords": list(coords.get(merc, ())),
            # La partición que ya tiene: sin esto, el empaquetado le metería
            # puntos de otra cadena en su hueco libre.
            "grupo": grupo_de_merc.get(merc),
        }
        for merc, carga in usado.items()
        if zona_de_merc.get(merc) and (solo is None or merc in solo)
    ]


def procesar_minoristas(
    input_file: str,
    output_file: str,
    progress_callback: Optional[ProgressCallback] = None,
    tipo_ruta: str = "tiempo_completo",
    incluir_tiempo_desplazamiento: Optional[bool] = None,
    minutos_jornada_dia: Optional[int] = None,
    tipo_carga: Optional[str] = None,
    canal: Optional[str] = None,
    cadenas: Optional[list] = None,
    grupos_cadenas: Optional[list] = None,
) -> None:
    """Funcion principal de procesamiento de rutas.

    Args:
        input_file: Ruta al Excel de entrada con ubicaciones.
        output_file: Ruta donde se guardará el Excel de resultados.
        progress_callback: Función opcional (progress: int, message: str) -> None
            llamada en cada etapa para reportar avance (0-100).
        tipo_ruta: 'tiempo_completo' o 'medio_tiempo'.
        incluir_tiempo_desplazamiento: modelo de jornada de ESTA ejecución.
            True  -> el desplazamiento entre puntos descuenta de la cuota
                     (480 min/día, 9600 min/mes), así que caben menos puntos
                     por jornada y hacen falta más mercaderistas.
            False -> la cuota se llena solo con el tiempo de servicio en los
                     puntos; el desplazamiento se sigue calculando y escribiendo
                     en el Excel, pero no consume jornada.
            None  -> el valor por defecto del servidor (JORNADA_INCLUYE_VIAJE).
        minutos_jornada_dia: cuota de jornada diaria de ESTA ejecución. La
            semana son 5 días y el mes 20, así que 480 -> 2.400/semana y
            9.600/mes, y 400 -> 2.000 y 8.000. `None` usa el valor por defecto.
        tipo_carga: cómo se parte el trabajo entre mercaderistas.
            'zona'   -> solo manda la geografía (comportamiento histórico)
            'ciudad' -> cada mercaderista atiende una sola ciudad
            'cadena' -> cada mercaderista atiende una sola cadena comercial
                        (columna CADENA del Excel)
        canal: canal comercial al que se acota la ejecución (columna `canal`
            del Excel: "Moderno", "TRADICIONAL"...). Vacío = todos.
        cadenas: cadenas concretas dentro de ese canal. Vacío = todas las del
            canal. Es un recorte del ALCANCE: los puntos de otras cadenas no se
            planifican ni se reportan como pendientes, porque no se pidieron.
            Solo se aplica con tipo_carga='cadena'.
        grupos_cadenas: lista de listas de cadenas para tipo_carga='multicanal'.
            Cada lista es un grupo con su propio equipo de mercaderistas
            (["ROSADO", "CORAL"], ["TIA"]...). Las cadenas que no estén en
            ningún grupo quedan fuera de la ejecución.

    Ambos ajustes se aplican durante toda la ejecución y se restauran al
    terminar, también si el procesamiento falla o se cancela.
    """
    with jornada_incluye_viaje_temporal(incluir_tiempo_desplazamiento), cuota_dia_temporal(
        minutos_jornada_dia
    ):
        _procesar_minoristas(
            input_file,
            output_file,
            progress_callback=progress_callback,
            tipo_ruta=tipo_ruta,
            tipo_carga=tipo_carga,
            canal=canal,
            cadenas=cadenas,
            grupos_cadenas=grupos_cadenas,
        )


def _procesar_minoristas(
    input_file: str,
    output_file: str,
    progress_callback: Optional[ProgressCallback] = None,
    tipo_ruta: str = "tiempo_completo",
    tipo_carga: Optional[str] = None,
    canal: Optional[str] = None,
    cadenas: Optional[list] = None,
    grupos_cadenas: Optional[list] = None,
) -> None:
    """Cuerpo del procesamiento. Asume el modelo de jornada ya aplicado."""

    def _notify(progress: int, message: str) -> None:
        if progress_callback:
            progress_callback(progress, message)  # Las excepciones (ej. cancelación) deben propagarse

    modelo_jornada = (
        "con tiempo de desplazamiento"
        if jornada_incluye_viaje()
        else "sin tiempo de desplazamiento"
    )
    print("\n" + "=" * 60)
    print(f"  SMART ROUTES - Procesamiento de Rutas ({tipo_ruta.upper()})")
    print(f"  Jornada: {modelo_jornada}")
    print(f"  Cuota: {cuota_dia()} min/día · {cuota_dia() * 5} min/semana · {cuota_mes()} min/mes")
    print(f"  Reparto: {ETIQUETAS[normalizar_tipo_carga(tipo_carga)]}")
    print("=" * 60 + "\n")

    # La cuadrilla de fin de semana es estado de ejecución: se decide dentro de
    # este procesamiento y no debe filtrarse al siguiente.
    limpiar_mercadistas_fin_semana()
    limpiar_grupos_multicanal()

    # Grupos de cadenas de multicanal: los define quien lanza el procesamiento.
    # Solo se planifican las cadenas agrupadas; lo que no entró en ningún grupo
    # queda fuera del alcance, igual que las cadenas no marcadas en "por cadena".
    if normalizar_tipo_carga(tipo_carga) == TIPO_MULTICANAL and grupos_cadenas:
        mapa = set_grupos_multicanal(grupos_cadenas)
        cadenas = cadenas_agrupadas()
        print(f"      -> Grupos de cadenas: {mapa}")

    _notify(3, "Preparando el archivo de datos...")

    # Paso 1: leer archivo
    print("[1/6] Leyendo archivo de entrada...")
    df = leer_excel_entrada(input_file)
    print(f"      -> {len(df)} ubicaciones encontradas")

    # Recorte por canal y cadenas: se hace aquí, antes de expandir frecuencias
    # y de dimensionar la flota, para que todo lo que viene después (plantilla,
    # rutas, pendientes, porcentajes) hable solo de lo que se pidió planificar.
    if normalizar_tipo_carga(tipo_carga) in (
        TIPO_CADENA, TIPO_CANAL, TIPO_MULTICANAL
    ) and (canal or cadenas):
        df, descartadas = filtrar_por_canal_y_cadenas(df, canal or "", cadenas or [])
        canal_txt = ", ".join(canal) if isinstance(canal, (list, tuple)) else (canal or "todos")
        etiqueta = ", ".join(cadenas) if cadenas else "todas las cadenas"
        print(
            f"      -> Alcance: canal '{canal_txt}' · {etiqueta} "
            f"-> {len(df)} ubicación(es); {descartadas} fuera del alcance"
        )
        if df.empty:
            raise ValueError(
                "Ninguna fila del archivo pertenece al canal y las cadenas "
                "seleccionadas. Revisa la selección o elige otro alcance."
            )
        df = df.reset_index(drop=True)
    _notify(10, f"Se encontraron {len(df)} puntos de venta. Clasificando categorías...")

    # Asignar categoria
    if "DESCRIPCION" in df.columns:
        df["CATEGORIA"] = df["DESCRIPCION"].apply(obtener_categoria)
    else:
        df["CATEGORIA"] = "DESCONOCIDO"

    # Corregir coordenadas
    print("      -> Verificando y corrigiendo coordenadas...")
    df, _, _ = corregir_coordenadas_df(df)
    print()

    # Determinar provincias
    df = determinar_provincias(df)

    # Inicializar columnas
    df = inicializar_columnas(df)
    _notify(18, "Verificando coordenadas y determinando provincias...")

    # Paso 2: instancias por frecuencia
    print("[2/6] Calculando visitas por frecuencia...")
    visit_instances, ubicaciones_procesadas, ubicaciones_omitidas, visitas_sin_coordenadas = crear_instancias_visita(df, tipo_ruta=tipo_ruta)
    print(f"      -> {ubicaciones_procesadas} ubicaciones validas procesadas")
    if ubicaciones_omitidas > 0:
        print(f"      -> {ubicaciones_omitidas} ubicaciones omitidas (coordenadas invalidas) -> pendientes")
    print(f"      -> {len(visit_instances)} visitas totales generadas\n")
    _notify(30, f"Se calcularon {len(visit_instances)} visitas según la frecuencia de cada punto...")

    # Paso 3: Ordenar
    print("[3/6] Ordenando visitas...")
    # De MAYOR a menor duración (first-fit decreasing). Empaquetar en jornadas de
    # 480 min es un problema de bin packing, y colocar primero las visitas largas
    # es la heurística estándar: los huecos que quedan son pequeños y se rellenan
    # con visitas cortas. Al revés —como estaba— las jornadas se llenaban de
    # visitas cortas y las de 240-480 min ya no cabían en ningún día, así que
    # abrían jornada propia y dejaban el resto del día vacío.
    visit_instances.sort(key=lambda x: -float(x.get("tiempo") or 0))
    print("      -> Ordenamiento completado\n")
    _notify(38, "Organizando y priorizando las visitas...")

    # Plan automático de mercadistas por zona de trabajo. Una zona es un grupo
    # de puntos de venta geográficamente compacto (radio acotado), no una
    # provincia: lo que determina si dos puntos los puede atender la misma
    # persona es la distancia entre ellos, no la frontera administrativa.
    tipo_carga_norm = normalizar_tipo_carga(tipo_carga)
    error_datos = validar_datos_suficientes(visit_instances, tipo_carga_norm)
    if error_datos:
        raise ValueError(error_datos)

    grupos = resumen_grupos(visit_instances, tipo_carga_norm)
    if grupos:
        print(f"      -> Reparto {ETIQUETAS[tipo_carga_norm]}: {len(grupos)} grupo(s)")
        for clave, datos in sorted(grupos.items(), key=lambda kv: -kv[1]["minutos"]):
            print(
                f"         * {clave:<28s} {datos['visitas']:5d} visitas "
                f"{datos['minutos']:9.0f} min/mes"
            )

    mercadistas_plan, propiedad_puntos, desglose_zonas = planificar_flota_por_capacidad(
        visit_instances, tipo_carga=tipo_carga_norm
    )
    print(f"      -> Plan por capacidad mensual: {resumen_plan(desglose_zonas)}")

    if not mercadistas_plan:
        propiedad_puntos = {}
        # Salvavidas: si no hay visitas con tiempo > 0 (caso extremo), usamos
        # al menos un mercadista para no romper el flujo posterior.
        mercadistas_plan = [("Mercadista 01", None)]
        desglose_zonas = []

    if desglose_zonas:
        print("      -> Plan automático por zona (min/mes = servicio + viaje estimado):")
        for zona, total_min, n_mercs in desglose_zonas:
            print(
                f"         * {zona:<40s} {total_min:8.0f} min/mes combinado -> "
                f"{n_mercs} mercadista(s)"
            )

    # Los días fijos de las frecuencias 8/12/16/20 se eligen aquí en función de
    # la carga de cada mercaderista, no del número de fila del Excel
    # (ver route_engine/day_balance.py). Sin esto, a una misma persona le
    # coincidían varios puntos exigiendo el mismo día: 58 jornadas quedaban por
    # encima de la cuota solo con visitas obligatorias mientras otras quedaban
    # vacías, y ese exceso terminaba en 'Pendientes_Sin_Asignar'.
    from route_engine.day_balance import equilibrar_dias_fijos

    balance = equilibrar_dias_fijos(visit_instances, propiedad_puntos, cuota_dia())
    print(
        f"      -> Días fijos equilibrados por mercadista: "
        f"{balance['puntos_movidos']} punto(s) recolocados en "
        f"{balance['mercaderistas']} mercadista(s)"
    )

    _notify(
        45,
        f"Configurando {len(mercadistas_plan)} mercadistas en "
        f"{len(desglose_zonas) or 1} zona(s) para cubrir todas las rutas...",
    )

    # Paso 4: asignacion
    print("[4/6] Asignando mercadistas y calculando rutas...")
    state = _asignar_con_refuerzo_de_flota(
        visit_instances, mercadistas_plan, df, tipo_ruta, _notify,
        propiedad_puntos=propiedad_puntos, tipo_carga=tipo_carga_norm,
    )
    state.tipo_carga = tipo_carga_norm
    # Alcance de la ejecución, para que quede escrito en el Excel resultante.
    state.canal = (", ".join(canal) if isinstance(canal, (list, tuple)) else canal) or ""
    state.cadenas = list(cadenas or [])
    state.grupos_cadenas = [list(g) for g in (grupos_cadenas or []) if g]
    mercadistas_plan = state.mercadistas_plan

    # Inyectar puntos con coordenadas (0,0) en la lista separada Puntos_Sin_Coordenadas
    if visitas_sin_coordenadas:
        for v in visitas_sin_coordenadas:
            state.puntos_sin_coordenadas.append({
                "motivo": v["motivo"],
                "descripcion": v["descripcion"],
                "lat": v["lat"],
                "lon": v["lon"],
                "tiempo": v["tiempo"],
                "frecuencia_mes": v.get("frecuencia_mes"),
            })
        print(f"      -> {len(visitas_sin_coordenadas)} punto(s) con coordenadas (0,0) enviados a 'Puntos_Sin_Coordenadas'")

    _notify(58, "Distribuyendo puntos de venta entre los mercadistas...")

    # Traslado de puntos que su dueño no puede atender completos. Va ANTES del
    # rescate para que sea este quien los recoloque, con el estado real de las
    # jornadas del nuevo dueño.
    # Mínimo de plantilla que admite la demanda: por debajo de esto no existe
    # ningún reparto que coloque todo el trabajo, por bueno que sea.
    demanda_total = sum(float(i.get("tiempo") or 0) for i in visit_instances)
    minimo_teorico = int(math.ceil(demanda_total / cuota_mes()))
    piso_flota = int(math.ceil(minimo_teorico * HOLGURA_FLOTA))
    print(
        f"      -> Plantilla: mínimo teórico {minimo_teorico}, "
        f"suelo con holgura {piso_flota} mercadista(s) "
        f"({demanda_total:.0f} min de demanda)"
    )


    reasignar_puntos_varados(state)


    # Pase de rescate
    ejecutar_pase_rescate(state)

    # Completar frecuencias: lo que falte para llegar a la frecuencia mensual de
    # cada punto vuelve al pool ANTES de compactar y absorber.
    #
    # El orden es lo que decide si sirve de algo. Si se reinyecta al final, esas
    # visitas solo pueden ofrecerse a su dueño actual —un punto lo atiende una
    # sola persona— y su dueño está lleno, que es justamente por lo que faltaban:
    # de 512 devueltas entraban 59. Haciéndolo aquí, la fase de absorción puede
    # mover el punto ENTERO a otro mercaderista, que es la única jugada legal que
    # las coloca sin partir el punto.
    if reinyectar_frecuencias_incompletas(state, df):
        ejecutar_pase_rescate(state)

    # Compactar ANTES de contratar, y repetir.
    #
    # El orden importa y estaba al revés: se abrían plazas para lo que no cabía
    # y solo después se compactaba, así que el trabajo sobrante estrenaba
    # mercaderista mientras la plantilla tenía plazas al 5-20% que nadie había
    # fundido todavía. Compactando primero, ese mismo trabajo cae en gente que
    # ya está contratada y no hace falta abrir la plaza.
    #
    # Se alternan las dos operaciones porque se alimentan mutuamente: disolver
    # libera puntos que el rescate recoloca, y colocarlos deja nuevas plazas por
    # debajo del umbral que la siguiente vuelta puede fundir.
    for vuelta in range(3):
        if disolver_mercadistas_infrautilizados(
            state, umbral_ocupacion=0.75, minimo_activos=piso_flota
        ):
            # Los puntos cedidos vuelven al pool: hay que recolocarlos.
            ejecutar_pase_rescate(state)
        elif vuelta > 0:
            break
        _absorber_pendientes_abriendo_plazas(
            state, df, tipo_ruta, _notify, max_rondas=4 if vuelta == 0 else 2
        )

    # Última compactación: la absorción deja plazas recién abiertas a medio
    # llenar que ya pueden fundirse contra la plantilla definitiva.
    if disolver_mercadistas_infrautilizados(
            state, umbral_ocupacion=0.75, minimo_activos=piso_flota
        ):
        ejecutar_pase_rescate(state)

    _notify(65, "Verificando que todos los puntos estén cubiertos...")

    # Red de seguridad
    ejecutar_red_seguridad(state)

    # Sábados y domingos para lo que siga sin colocar (solo reparto por zona).
    #
    # Va DESPUÉS de la red de seguridad y con una reinyección previa porque el
    # faltante real no se conoce hasta entonces: la mayor parte de las visitas
    # que faltan no están en el pool, sino que se descubren al cuadrar
    # (agendadas + pendientes = frecuencia mensual). Antes de reinyectar aquí se
    # veían 40 visitas sin colocar cuando en realidad eran más de mil.
    if CUADRILLA_FIN_SEMANA_ACTIVA and normalizar_tipo_carga(tipo_carga) == TIPO_ZONA:
        if reinyectar_frecuencias_incompletas(state, df):
            # Primero se intenta de lunes a viernes: el fin de semana es el
            # último recurso, no el atajo.
            ejecutar_pase_rescate(state)
        abiertos_fs = _absorber_pendientes_fin_de_semana(state, df, tipo_ruta, _notify)
        if abiertos_fs:
            print(
                f"      -> Cuadrilla de fin de semana: {abiertos_fs} mercadista(s) "
                f"de miércoles a domingo"
            )
            # Aquí NO se compacta. Medido: una vuelta de compactación después
            # de la cuadrilla devolvía la cobertura del 91,9% al 77,9%, porque
            # vaciar plazas mueve puntos enteros a vecinos donde no caben y
            # acaban otra vez en pendientes.
            ejecutar_red_seguridad(state)

    _control_mes(state, "reparto final")
    _notify(70, "Optimizando la cobertura total de rutas...")

    # Paso 5: obtener direcciones
    print("[5/6] Obteniendo direcciones desde coordenadas...")
    coordenadas_unicas = set()
    for summary in state.all_day_summaries:
        lat = summary.get("Latitud", "")
        lon = summary.get("Longitud", "")
        if lat and lon and lat != "" and lon != "":
            try:
                lat_f = float(lat)
                lon_f = float(lon)
                if lat_f != 0.0 and lon_f != 0.0:
                    coordenadas_unicas.add((round(lat_f, 6), round(lon_f, 6)))
            except Exception:
                pass
    for _, row in df.iterrows():
        lat = row.get("LATITUD", 0)
        lon = row.get("LONGITUD", 0)
        try:
            lat_f = float(lat)
            lon_f = float(lon)
            if lat_f != 0.0 and lon_f != 0.0:
                coordenadas_unicas.add((round(lat_f, 6), round(lon_f, 6)))
        except Exception:
            pass

    total_unicas = len(coordenadas_unicas)
    # Intervalo de notificación: cada 5% del total o mínimo cada 10 items
    notif_intervalo = max(1, total_unicas // 20)
    if total_unicas > 0:
        for i, (lat, lon) in enumerate(coordenadas_unicas, 1):
            mostrar_progreso(i, total_unicas, "      Progreso")
            obtener_direccion_desde_coordenadas(lat, lon)
            if i % notif_intervalo == 0 or i == total_unicas:
                pct = 70 + int((i / total_unicas) * 20)
                _notify(pct, f"Obteniendo información geográfica... ({i} de {total_unicas} ubicaciones)")
        print()
    print(f"      -> {total_unicas} coordenadas unicas procesadas\n")

    # Paso 6: generar Excel
    print("[6/6] Generando archivo Excel...")
    _notify(92, "Generando el archivo de resultados con todas las rutas...")
    horarios_df = pd.DataFrame(state.all_day_summaries)

    # Validaciones
    validar_resultado(horarios_df, state)

    # Generar archivo (esto puebla state.puntos_pendientes con visitas
    # descartadas por el tope diario combinado en el postproceso)
    generar_excel_salida(output_file, horarios_df, df, visit_instances, state)

    # Reporte final: visitas que no quedaron asignadas a ningún mercadista/día.
    # Las acumula `recolectar_pendientes` desde rescue + red_seguridad + tope
    # combinado y las imprime al terminal.
    recolectar_pendientes(state)

    print(f"\n{'=' * 60}")
    print(f"  EXITO: Archivo generado -> {output_file}")
    print("=" * 60 + "\n")
    _notify(100, "¡Proceso completado! Las rutas han sido generadas exitosamente.")
