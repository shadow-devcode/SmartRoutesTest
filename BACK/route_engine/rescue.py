"""
Pase de rescate y red de seguridad para visitas no asignadas.
"""

from collections import defaultdict

from route_engine.config import (
    DAY_NAMES,
    dias_de_mercadista,
    mercadistas_fin_semana,
    MAX_TRAVEL_MINUTES_EXTREME,
    NUM_SEMANAS_POR_MERCADISTA,
    RADIO_RESCATE_AMPLIADO_KM,
    carga_jornada,
    cuota_mes,
    max_dia_flex,
    max_servicio_dia,
    travel_estimado_por_visita_plan_min,
)
from route_engine.frequency import semanas_distribuidas, semanas_por_frecuencia
from route_engine.geo import estimar_minutos_viaje, haversine_km
from route_engine.mapbox import _geocode_cache, norm_provincia
from route_engine.rescate_red_seguridad import (
    ejecutar_red_seguridad,
    reinyectar_frecuencias_incompletas,
)
from route_engine.rescate_reubicacion import (
    disolver_mercadistas_infrautilizados,
    reasignar_puntos_varados,
)
from route_engine.rescate_utiles import (
    _cabe_en_la_zona,
    _candidatos_cercanos,
    _coords_por_mercadista,
    _estado_dias_existente,
    _misma_provincia,
    _provincia_por_mercadista,
    _puede_absorber,
    _salto_admisible,
    _semana_de,
    _travel_desde,
    _visitas_por_semana,
)
from route_engine.scheduling import clave_punto, format_duracion


def ejecutar_pase_rescate(state):
    """
    Pases de rescate: asignar visitas pendientes respetando limites.
    Modifica state.all_day_summaries in-place.
    """
    pendientes_total = state.count_remaining()
    if pendientes_total == 0:
        return

    # Cada pase parte de cero: lo que no quepa en ESTE pase es lo que queda.
    state.sin_hueco = []

    # Minutos que ESTE pase añade a cada mercaderista. Vive FUERA del bucle por
    # zona a propósito: desde que el rescate puede ofrecer una visita a un
    # mercaderista de zona vecina, una misma persona recibe trabajo en varias
    # iteraciones del bucle. Reiniciando el contador en cada zona, la segunda
    # empezaba a contar desde cero y el tope mensual se comprobaba contra un
    # total falso: cuatro mercaderistas acababan por encima de sus 9.600 min,
    # hasta 10.920.
    total_merc_rescue = defaultdict(float)
    total_min_por_merc = defaultdict(float)
    for s in state.all_day_summaries:
        # Mismo criterio que el tope contra el que se compara (cuota_mes(),
        # que es la cuota COMBINADA): con el modelo "con tiempo de
        # desplazamiento" contar solo el servicio dejaba al rescate creyendo que
        # quedaba hueco mensual donde ya no lo había. Con el modelo "sin
        # desplazamiento" el viaje aporta 0 y esto es la suma de servicio de
        # siempre.
        total_min_por_merc[s.get("Mercadista", "")] += carga_jornada(
            float(s.get("Tiempo Servicio (min)", 0) or 0),
            float(s.get("Tiempo entre sucursal (min)", 0) or 0),
        )

    print(
        f"      -> Asignando {pendientes_total} visitas pendientes (rescue), "
        f"limite {max_servicio_dia()} min/dia y {cuota_mes()} min/mes por mercadista..."
    )

    for prov_p, lst in list(state.remaining_by_prov.items()):
        if not lst:
            continue
        # Candidatos: TODOS los mercaderistas, no solo los de la zona de la
        # visita. La zona ya no es una región con varias personas dentro —desde
        # que la flota se dimensiona por capacidad mensual hay exactamente una
        # por zona—, así que filtrar por zona dejaba un único candidato: el
        # mismo dueño que ya había dicho que no le cabía. El control geográfico
        # real lo hace `_cabe_en_la_zona` unos renglones más abajo, que exige
        # que el punto entre en el radio operativo del mercaderista.
        mercs_prov = [mn for mn, _pa in state.mercadistas_plan]
        propios = {
            mn for mn, pa in state.mercadistas_plan if pa and norm_provincia(pa) == prov_p
        }
        # Se prueba primero con los de la zona propia: mover trabajo al vecino
        # es la salida, no la preferencia.
        mercs_prov.sort(key=lambda mn: (mn not in propios, mn))
        if not mercs_prov:
            continue

        pares = []
        for inst in lst:
            semana_num = inst.get("semana_num")
            frecuencia_mes = inst.get("frecuencia_mes", 1)
            if semana_num is None:
                num_s = semanas_por_frecuencia(frecuencia_mes)
                semanas_rescue = list(range(1, num_s + 1))
            else:
                semanas_rescue = [semana_num]
            for semana in semanas_rescue:
                pares.append((inst, semana))

        buckets = {}
        # Estado por bucket: {"servicio": float, "travel": float,
        #                      "last_lat": float|None, "last_lon": float|None}
        #
        # Se inicializa con lo que YA tiene agendado cada (mercadista, día,
        # semana). Antes arrancaba vacío, así que `_fits_combinado` daba por
        # libre una jornada que en realidad estaba al tope y le encajaba otros
        # 480 min. Esas visitas se escribían igual, el postproceso las recortaba
        # por pasarse de 480 y acababan en pendientes con el motivo "sin hueco
        # en ningún día" — habiendo desplazado por el camino a otras que sí
        # cabían.
        bucket_state = _estado_dias_existente(state)
        coords_merc = _coords_por_mercadista(state)
        prov_merc = _provincia_por_mercadista(state)


        # Puntos ya presentes en cada bucket (mercadista, día, semana). Un punto
        # no puede visitarse dos veces el mismo día: sin este control el rescate
        # amontonaba las 5 visitas semanales de un punto de frecuencia 20 en el
        # primer día que le cabía, y al escribir el Excel la deduplicación se
        # quedaba con una sola. Las otras cuatro no aparecían ni agendadas ni
        # pendientes: desaparecían del recuento.
        puntos_en_bucket = defaultdict(set)
        for s_ in state.all_day_summaries:
            merc_s = s_.get("Mercadista", "")
            dias_s = dias_de_mercadista(merc_s)
            dia_s = s_.get("Día")
            sem_s = _semana_de(s_.get("Fecha"))
            if dia_s not in dias_s or sem_s is None:
                continue
            try:
                clave_s = (
                    round(float(s_.get("Latitud") or 0), 6),
                    round(float(s_.get("Longitud") or 0), 6),
                    str(s_.get("Descripción", "")).strip(),
                )
            except (TypeError, ValueError):
                continue
            puntos_en_bucket[(merc_s, dias_s.index(dia_s), sem_s)].add(clave_s)

        def _fits_combinado(key, inst_):
            """
            ¿Cabe `inst_` en `buckets[key]`?

            Comprueba, además de la carga: que el punto no se repita ese día y
            que el SALTO desde la última visita de la jornada sea razonable.

            El tope por salto faltaba, y era el agujero por el que salían rutas
            imposibles: la jornada solo miraba el total del día, así que un
            trayecto de 485 km y 515 min entraba mientras el total cuadrara.
            Un mercaderista no cruza medio país entre dos tiendas.
            """
            if clave_punto(inst_) in puntos_en_bucket.get(key, ()):
                return False
            st = bucket_state.get(key, {"servicio": 0.0, "travel": 0.0, "last_lat": None, "last_lon": None})
            nuevo_serv = st["servicio"] + inst_["tiempo"]
            # Excepción del tope diario: una visita más larga que la jornada
            # entera solo cabe en un día vacío, para ella sola. Sin esto el
            # rescate no podía recolocar esos puntos y acababan en pendientes
            # aunque el asignador sí les hubiera hecho sitio.
            dia_vacio = st["servicio"] <= 0
            visita_mayor_que_jornada = float(inst_["tiempo"] or 0) > max_dia_flex()
            if dia_vacio and visita_mayor_que_jornada:
                return _salto_admisible(st["last_lat"], st["last_lon"], inst_)
            if nuevo_serv > max_dia_flex():
                return False
            if not _salto_admisible(st["last_lat"], st["last_lon"], inst_):
                return False
            travel_nuevo = _travel_desde(st["last_lat"], st["last_lon"], inst_)
            combinado = nuevo_serv + st["travel"] + travel_nuevo
            return combinado <= max_dia_flex()

        def _agregar(key, inst_, semana_):
            buckets.setdefault(key, []).append((inst_, semana_))
            puntos_en_bucket[key].add(clave_punto(inst_))
            # Las coordenadas del mercaderista se actualizan AQUÍ, no solo al
            # empezar el pase. Con la foto inicial, cada punto se comparaba
            # contra un conjunto que ya no era el real y la zona se iba
            # estirando visita a visita.
            try:
                coords_merc.setdefault(key[0], []).append(
                    (round(float(inst_["lat"]), 4), round(float(inst_["lon"]), 4))
                )
            except (TypeError, ValueError, KeyError):
                pass
            st = bucket_state.get(key, {"servicio": 0.0, "travel": 0.0, "last_lat": None, "last_lon": None})
            travel_nuevo = _travel_desde(st["last_lat"], st["last_lon"], inst_)
            st["servicio"] += inst_["tiempo"]
            st["travel"] += travel_nuevo
            try:
                st["last_lat"] = float(inst_["lat"])
                st["last_lon"] = float(inst_["lon"])
            except (TypeError, ValueError, KeyError):
                pass
            bucket_state[key] = st
            # Los minutos de viaje que añade esta inserción: el llamador los
            # necesita para descontarlos de la cuota mensual con la misma vara
            # con la que el tope está expresado.
            return travel_nuevo

        # De mayor a menor servicio: las visitas largas son las que menos huecos
        # admiten, así que se colocan primero (first-fit decreasing). Al revés,
        # las cortas se comían los pocos huecos grandes y las largas ya no
        # entraban en ningún sitio.
        pares.sort(
            key=lambda par: (
                -int(par[0].get("frecuencia_mes") or 0),
                -float(par[0].get("tiempo") or 0),
            )
        )

        for inst, semana in pares:
            asignado = False
            idx_punto = state.get_punto_key(inst)
            # Solo mercadistas a los que este punto puede ir: no asignado o ya asignado a ese merc
            mercs_validos = [
                m for m in mercs_prov
                if (idx_punto not in state.punto_mercadista or state.punto_mercadista[idx_punto] == m)
                and state.puede_atender(m, inst)
                and _cabe_en_la_zona(coords_merc.get(m, []), inst)
            ]
            # Primero el dueño / la zona propia y, dentro de eso, el que menos
            # carga lleva: repartir el sobrante al más descargado es lo que
            # aplana la ocupación en vez de abrir plazas nuevas.
            # Orden de preferencia, de más a menos:
            #   1) mercaderistas de la MISMA PROVINCIA que el punto
            #   2) los de su propia zona
            #   3) los menos cargados
            #
            # La provincia manda porque es regla de negocio: un mercaderista
            # trabaja su provincia y solo sale de ella cuando no queda otra.
            # Antes solo se miraba la zona, y como desde el nuevo dimensionado
            # hay una zona por persona, el criterio no distinguía nada: por eso
            # aparecían rutas Rumiñahui→Puyo o Quevedo→Quito.
            mercs_ordenados = sorted(
                mercs_validos,
                key=lambda m: (
                    not _misma_provincia(prov_merc.get(m), inst),
                    m not in propios,
                    total_min_por_merc[m] + total_merc_rescue[m],
                ),
            )
            for merc in mercs_ordenados:
                if asignado:
                    break
                tot_mes = total_min_por_merc[merc] + total_merc_rescue[merc]
                if tot_mes + inst["tiempo"] > cuota_mes():
                    continue
                # Best-fit: el día con MENOS hueco de los que admiten la visita.
                # Tomar el primero que cabe (first-fit por índice de día) gastaba
                # jornadas casi vacías en visitas pequeñas y dejaba sin sitio a
                # las grandes, que ya no encontraban ningún día holgado.
                opciones = []
                for dia_idx in range(len(DAY_NAMES)):
                    key = (merc, dia_idx, semana)
                    if _fits_combinado(key, inst):
                        st = bucket_state.get(key)
                        usado = (st["servicio"] + st["travel"]) if st else 0.0
                        opciones.append((max_dia_flex() - usado, key))
                if opciones:
                    _, key = min(opciones)
                    travel_nuevo = _agregar(key, inst, semana)
                    total_merc_rescue[merc] += carga_jornada(inst["tiempo"], travel_nuevo)
                    state.asignar_punto(idx_punto, merc)
                    asignado = True
            if not asignado:
                candidatos = []
                for merc in mercs_ordenados:
                    if total_min_por_merc[merc] + total_merc_rescue[merc] + inst["tiempo"] > cuota_mes():
                        continue
                    for d in range(len(DAY_NAMES)):
                        key = (merc, d, semana)
                        if _fits_combinado(key, inst):
                            tot_b = bucket_state.get(key, {}).get("servicio", 0.0)
                            candidatos.append(
                                (key, tot_b, total_min_por_merc[merc] + total_merc_rescue[merc])
                            )
                if candidatos:
                    key_menor, _, _ = min(candidatos, key=lambda x: (x[2], x[1]))
                    travel_nuevo = _agregar(key_menor, inst, semana)
                    total_merc_rescue[key_menor[0]] += carga_jornada(
                        inst["tiempo"], travel_nuevo
                    )
                    state.asignar_punto(idx_punto, key_menor[0])
                else:
                    # Sin hueco en ninguna jornada existente. Se guarda la
                    # instancia para que el procesador pueda abrirle plaza; si
                    # tampoco así cabe, `recolectar_pendientes` la reportará al
                    # final. Antes se cerraba aquí como pendiente definitivo y
                    # esas visitas ya no volvían a intentarse nunca.
                    state.sin_hueco.append(inst)

        for (merc, dia_idx, semana), items in buckets.items():
            seen_punto = set()
            items_uniq = []
            for inst, sem in items:
                clave = clave_punto(inst)
                if clave in seen_punto:
                    continue
                seen_punto.add(clave)
                items_uniq.append((inst, sem))

            dia_nombre = dias_de_mercadista(merc)[dia_idx]
            for orden_dia, (inst, semana) in enumerate(items_uniq, 1):
                key_coord = (round(float(inst["lat"]), 6), round(float(inst["lon"]), 6))
                provincia, ciudad, calle = _geocode_cache.get(key_coord, ("", "", ""))
                state.all_day_summaries.append({
                    "Mercadista": merc,
                    "Día": dia_nombre,
                    "Orden Ruta": orden_dia,
                    "Descripción": inst["descripcion"],
                    "Latitud": inst["lat"],
                    "Longitud": inst["lon"],
                    "PROVINCIA": provincia,
                    "CIUDAD": ciudad,
                    "CALLE": calle,
                    "Tiempo Servicio (min)": inst["tiempo"],
                    "Duración (hh:mm)": format_duracion(inst["tiempo"]),
                    "Tiempo entre sucursal (min)": 0.0,
                    "kilometros entre sucurlas (km)": 0.0,
                    "Horario": "Pendiente",
                    "Fecha": f"semana {semana}",
                })

        state.remaining_by_prov[prov_p] = []
