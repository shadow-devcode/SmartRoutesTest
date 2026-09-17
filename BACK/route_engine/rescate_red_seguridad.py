"""
Red de seguridad y reinyección de frecuencias.

Último intento sobre las visitas que ningún pase anterior pudo colocar: se
buscan huecos en las jornadas ya escritas respetando el tope combinado y el
salto máximo, y se reinyectan las visitas que le faltan a un punto para
completar su frecuencia mensual. Lo que sigue sin caber queda en pendientes.
"""
from __future__ import annotations

from collections import defaultdict

from route_engine.config import (
    DAY_NAMES,
    NUM_SEMANAS_POR_MERCADISTA,
    RADIO_RESCATE_AMPLIADO_KM,
    carga_jornada,
    cuota_mes,
    dias_de_mercadista,
    tope_dia_combinado,
)
from route_engine.excel_reader import clave_punto_frecuencia, columna_frecuencia_mes
from route_engine.geo import estimar_minutos_viaje, haversine_km
from route_engine.mapbox import _geocode_cache, norm_provincia
from route_engine.rescate_utiles import (
    _cabe_en_la_zona,
    _coords_por_mercadista,
    _estado_dias_existente,
    _salto_admisible,
    _semana_de,
    _travel_desde,
)
from route_engine.scheduling import clave_punto, format_duracion


def ejecutar_red_seguridad(state):
    """
    Red de seguridad: que la suma de visitas (FRECUENCIA MES)
    coincida con filas generadas. Anade faltantes.
    """
    filas_ya_emitidas = set()
    for s in state.all_day_summaries:
        try:
            lat_f = float(s.get("Latitud", 0))
            lon_f = float(s.get("Longitud", 0))
        except (TypeError, ValueError):
            continue
        desc = str(s.get("Descripción", "")).strip()
        fecha = str(s.get("Fecha", "")).strip()
        filas_ya_emitidas.add((round(lat_f, 6), round(lon_f, 6), desc, fecha))

    faltantes = []
    added_clave_semana = set()
    for inst in state.visit_instances:
        try:
            lat_f = float(inst.get("lat", 0))
            lon_f = float(inst.get("lon", 0))
        except (TypeError, ValueError):
            continue
        desc = str(inst.get("descripcion", "")).strip()
        cp = (round(lat_f, 6), round(lon_f, 6), desc)
        sn = inst.get("semana_num")
        if sn is None:
            for semana in range(1, 5):
                key = (*cp, f"semana {semana}")
                if key in added_clave_semana or key in filas_ya_emitidas:
                    continue
                faltantes.append((inst, semana))
                filas_ya_emitidas.add(key)
                added_clave_semana.add(key)
        else:
            key = (*cp, f"semana {sn}")
            if key in added_clave_semana or key in filas_ya_emitidas:
                continue
            faltantes.append((inst, sn))
            filas_ya_emitidas.add(key)
            added_clave_semana.add(key)

    total_esperado_visitas = len(state.visit_instances)
    if not faltantes or len(state.all_day_summaries) >= total_esperado_visitas:
        return

    max_a_anadir = total_esperado_visitas - len(state.all_day_summaries)
    faltantes = faltantes[:max_a_anadir]
    print(f"      -> Anadiendo {len(faltantes)} visitas faltantes (red de seguridad, tope {total_esperado_visitas})...")

    # Servicio acumulado, desplazamiento acumulado y última posición conocida
    # por grupo (Mercadista, Día, Fecha). Última posición = la del Orden Ruta
    # mayor en cada grupo, que es donde se "engancharía" una visita añadida.
    tot_por_grupo = defaultdict(float)
    travel_por_grupo = defaultdict(float)
    last_pos_por_grupo = {}  # k -> (orden_ruta, lat, lon)
    for s in state.all_day_summaries:
        k = (s.get("Mercadista"), s.get("Día"), s.get("Fecha"))
        try:
            tot_por_grupo[k] += float(s.get("Tiempo Servicio (min)", 0) or 0)
        except (TypeError, ValueError):
            pass
        try:
            travel_por_grupo[k] += float(s.get("Tiempo entre sucursal (min)", 0) or 0)
        except (TypeError, ValueError):
            pass
        try:
            orden_actual = int(s.get("Orden Ruta", 0) or 0)
            lat_s = float(s.get("Latitud", 0))
            lon_s = float(s.get("Longitud", 0))
            prev = last_pos_por_grupo.get(k)
            if prev is None or orden_actual > prev[0]:
                last_pos_por_grupo[k] = (orden_actual, lat_s, lon_s)
        except (TypeError, ValueError):
            pass

    # Carga mensual por mercadista, para repartir los faltantes en el que tenga
    # más hueco dentro de su zona en vez de en el primero que aparezca.
    carga_por_merc = defaultdict(float)
    for s in state.all_day_summaries:
        carga_por_merc[s.get("Mercadista", "")] += carga_jornada(
            float(s.get("Tiempo Servicio (min)", 0) or 0),
            float(s.get("Tiempo entre sucursal (min)", 0) or 0),
        )
    coords_merc = _coords_por_mercadista(state)

    for inst, semana in faltantes:
        idx_punto = state.get_punto_key(inst)
        # Si el punto ya pertenece a un mercaderista, solo puede ir a ese —pero
        # únicamente si de verdad le pilla cerca. Tener dueño no bastaba: la
        # propiedad se acepta sin mirar la distancia y las pasadas de
        # replanificación pueden haber dejado al punto con un dueño que quedó a
        # 200 km. En ese caso es mejor dejarlo pendiente y que se vea.
        if idx_punto in state.punto_mercadista:
            merc = state.punto_mercadista[idx_punto]
            if not _cabe_en_la_zona(
                coords_merc.get(merc, []), inst, radio_km=RADIO_RESCATE_AMPLIADO_KM
            ):
                state.puntos_pendientes.append({
                    "motivo": "red de seguridad: su mercaderista queda demasiado lejos del punto",
                    "descripcion": inst.get("descripcion", ""),
                    "lat": inst.get("lat"),
                    "lon": inst.get("lon"),
                    "semana": semana,
                    "tiempo": inst.get("tiempo", 60),
                    "frecuencia_mes": inst.get("frecuencia_mes"),
                    "provincia_punto": inst.get("provincia_punto", ""),
                })
                continue
        else:
            # Por ZONA, no por provincia. El plan guarda claves de zona
            # ("GUAYAS Z01"), así que compararlas con el nombre de la provincia
            # del punto ("GUAYAS") no casaba nunca: el `merc` caía siempre al
            # fallback y todos los faltantes sin dueño se apilaban en un mismo
            # mercadista, sin ninguna relación geográfica entre ellos (así
            # aparecía un mercadista con puntos a 371 km unos de otros).
            zona = state.zona_de(inst)
            mercs_zona = [
                mn for mn, pa in state.mercadistas_plan
                if pa and norm_provincia(pa) == zona
            ]
            mercs_zona = [
                m for m in mercs_zona
                # Barrera del tipo de carga, igual que en los demás pases.
                if state.puede_atender(m, inst)
                and _cabe_en_la_zona(coords_merc.get(m, []), inst)
            ]
            merc = min(mercs_zona, key=lambda m: carga_por_merc[m]) if mercs_zona else None
            if merc is None:
                # Sin mercadistas en la zona del punto: dejarlo pendiente es
                # correcto. Colocarlo en cualquier otro lo pondría a cientos de
                # km de su ruta, que es peor que no colocarlo.
                state.puntos_pendientes.append({
                    "motivo": "red de seguridad: sin mercadista en la zona del punto",
                    "descripcion": inst.get("descripcion", ""),
                    "lat": inst.get("lat"),
                    "lon": inst.get("lon"),
                    "semana": semana,
                    "tiempo": inst.get("tiempo", 60),
                    "frecuencia_mes": inst.get("frecuencia_mes"),
                    "provincia_punto": inst.get("provincia_punto", ""),
                })
                continue

        tiempo_min = inst.get("tiempo", 60)
        fecha_s = f"semana {semana}"
        # Igual que en el resto de controles mensuales: se mide con lo que
        # consume jornada según el modelo activo, porque el tope contra el que
        # se compara (cuota_mes()) es la cuota combinada.
        tot_mes_merc = sum(
            carga_jornada(tot_por_grupo[k], travel_por_grupo.get(k, 0.0))
            for k in tot_por_grupo
            if k[0] == merc
        )

        def _fits_combinado_safety(k):
            """¿Caben los `tiempo_min` de servicio + el viaje estimado a ese grupo?"""
            serv_actual = tot_por_grupo[k]
            travel_actual = travel_por_grupo.get(k, 0.0)
            prev = last_pos_por_grupo.get(k)
            travel_nuevo = 0.0
            if prev is not None:
                try:
                    travel_nuevo = float(estimar_minutos_viaje(
                        prev[1], prev[2], float(inst.get("lat", 0)), float(inst.get("lon", 0))
                    ))
                except Exception:
                    travel_nuevo = 0.0
            if serv_actual + tiempo_min > tope_dia_combinado():
                return False, travel_nuevo
            # Tope por SALTO, igual que en el pase de rescate. Sin él este pase
            # encadenaba visitas a cualquier distancia mientras el total del día
            # cuadrara: puntos de Sucumbíos sin provincia geocodificada acababan
            # en una jornada de Quito, a 201 km de la visita anterior.
            if prev is not None and not _salto_admisible(prev[1], prev[2], inst):
                return False, travel_nuevo
            combinado = serv_actual + tiempo_min + travel_actual + travel_nuevo
            return combinado <= tope_dia_combinado(), travel_nuevo

        con_hueco = []
        for k in tot_por_grupo:
            if k[0] != merc or k[2] != fecha_s:
                continue
            if tot_mes_merc + tiempo_min > cuota_mes():
                continue
            ok, travel_nuevo = _fits_combinado_safety(k)
            if ok:
                con_hueco.append((k, tot_por_grupo[k], travel_nuevo))
        if con_hueco:
            mejor = min(con_hueco, key=lambda x: x[1])
            mejor_k = mejor[0]
            mejor_travel_nuevo = mejor[2]
            dia_asig = mejor_k[1]
        else:
            # No hay hueco en mercadistas existentes; no se crean adicionales.
            # Registrar como pendiente para visibilidad en reporte y Excel.
            state.puntos_pendientes.append({
                "motivo": f"red de seguridad: sin hueco respetando {tope_dia_combinado()} min/dia combinado",
                "descripcion": inst.get("descripcion", ""),
                "lat": inst.get("lat"),
                "lon": inst.get("lon"),
                "semana": semana,
                "tiempo": tiempo_min,
                "frecuencia_mes": inst.get("frecuencia_mes"),
                "provincia_punto": inst.get("provincia_punto", ""),
            })
            continue

        tot_por_grupo[mejor_k] = tot_por_grupo.get(mejor_k, 0) + tiempo_min
        travel_por_grupo[mejor_k] = travel_por_grupo.get(mejor_k, 0.0) + mejor_travel_nuevo
        try:
            lat_n = float(inst.get("lat", 0))
            lon_n = float(inst.get("lon", 0))
            prev = last_pos_por_grupo.get(mejor_k)
            nuevo_orden = (prev[0] if prev else 0) + 1
            last_pos_por_grupo[mejor_k] = (nuevo_orden, lat_n, lon_n)
        except (TypeError, ValueError):
            pass
        if not state.asignar_punto(idx_punto, merc, inst):
            # La regla del tipo de carga manda sobre el relleno: antes que
            # mezclar cadenas en un mercaderista, la visita queda pendiente.
            state.puntos_pendientes.append({
                "motivo": "tipo de carga: no hay mercaderista de esa cadena/ciudad con hueco",
                "descripcion": inst.get("descripcion", ""),
                "lat": inst.get("lat"),
                "lon": inst.get("lon"),
                "semana": semana,
                "tiempo": tiempo_min,
                "frecuencia_mes": inst.get("frecuencia_mes"),
                "provincia_punto": inst.get("provincia_punto", ""),
            })
            continue
        carga_por_merc[merc] += carga_jornada(tiempo_min, mejor_travel_nuevo)
        # Provincia, ciudad y calle salen del cache de geocodificación, igual
        # que en el resto de inserciones. Escribirlas vacías dejaba visitas sin
        # provincia en el dashboard aunque el punto la tuviera en sus otras
        # visitas.
        try:
            clave_coord = (
                round(float(inst.get("lat") or 0), 6),
                round(float(inst.get("lon") or 0), 6),
            )
        except (TypeError, ValueError):
            clave_coord = None
        provincia, ciudad, calle = _geocode_cache.get(clave_coord, ("", "", ""))
        state.all_day_summaries.append({
            "Mercadista": merc,
            "Día": dia_asig,
            "Orden Ruta": 1,
            "Descripción": inst.get("descripcion", ""),
            "Latitud": inst.get("lat"),
            "Longitud": inst.get("lon"),
            "PROVINCIA": provincia,
            "CIUDAD": ciudad,
            "CALLE": calle,
            "Tiempo Servicio (min)": tiempo_min,
            "Duración (hh:mm)": format_duracion(tiempo_min),
            "Tiempo entre sucursal (min)": 0.0,
            "kilometros entre sucurlas (km)": 0.0,
            "Horario": "Pendiente",
            "Fecha": fecha_s,
        })


def reinyectar_frecuencias_incompletas(state, df):
    """
    Devuelve al pool las visitas que faltan para completar la frecuencia mensual
    de cada punto, para que el rescate intente colocarlas.

    El motor detectaba este hueco solo al ESCRIBIR el Excel
    (`reconciliar_pendientes_por_frecuencia`), cuando ya no se podía hacer nada:
    ahí las visitas faltantes se limitan a anotarse como pendientes. Y son la
    mayor parte del faltante —336 de 474 visitas en la última medición— pese a
    que la capacidad para colocarlas existe: 376 jornadas tenían 240 min libres
    o más, y las visitas que faltaban miden entre 60 y 240.

    Aquí se hace la misma cuenta antes de escribir y se reinyectan como
    instancias normales, con lo que el pase de rescate las coloca con sus reglas
    de siempre (tope diario, tope mensual, distancia y un punto una vez por día).

    Retorna cuántas visitas se reinyectaron.
    """
    from route_engine.excel_reader import clave_punto_frecuencia
    from route_engine.excel_writer import _info_puntos_desde_df

    info = _info_puntos_desde_df(df)
    if not info:
        return 0

    # 1) Lo que ya está agendado, por (punto, tiempo de servicio). El tiempo
    #    distingue las distintas filas de una misma tienda.
    agendadas = defaultdict(int)
    for s in state.all_day_summaries:
        key = clave_punto_frecuencia(
            s.get("Descripción"), s.get("Latitud"), s.get("Longitud")
        )
        if key[1] is None:
            continue
        try:
            t = float(s.get("Tiempo Servicio (min)", 0) or 0)
        except (TypeError, ValueError):
            continue
        agendadas[(key, t)] += 1

    # 2) Instancias disponibles para clonar, por (punto, tiempo).
    plantillas = {}
    for inst in state.visit_instances:
        key = clave_punto_frecuencia(
            inst.get("descripcion"), inst.get("lat"), inst.get("lon")
        )
        if key[1] is None:
            continue
        plantillas.setdefault((key, float(inst.get("tiempo") or 0)), inst)

    # 3) Reinyectar la diferencia.
    reinyectadas = 0
    for key, configs in info.items():
        for cfg in configs:
            try:
                tiempo = float(cfg.get("tiempo") or 0)
                requeridas = int(cfg.get("frecuencia") or 0)
            except (TypeError, ValueError):
                continue
            if tiempo <= 0 or requeridas <= 0:
                continue
            faltan = requeridas - agendadas.get((key, tiempo), 0)
            if faltan <= 0:
                continue
            base = plantillas.get((key, tiempo))
            if base is None:
                continue

            # Repartidas entre las 4 semanas: concentrarlas en una sola las
            # haría competir por los mismos cinco días y volverían a caerse.
            for i in range(faltan):
                nueva = dict(base)
                nueva["semana_num"] = (i % NUM_SEMANAS_POR_MERCADISTA) + 1
                nueva["required_day"] = None
                zona = state.zona_de(nueva)
                state.remaining_by_prov.setdefault(zona, []).append(nueva)
                reinyectadas += 1

    if reinyectadas:
        print(
            f"      -> Frecuencias incompletas: {reinyectadas} visita(s) devueltas "
            f"al pool para intentar colocarlas antes de darlas por pendientes"
        )
    return reinyectadas
