"""
Lectura y procesamiento del Excel de entrada.
"""

import math
import os

import pandas as pd

from route_engine.classification import es_farmacia_hasta_15, obtener_categoria
from route_engine.config import (
    DAY_COLUMNS,
    NUM_SEMANAS_POR_MERCADISTA,
    cuota_mes,
    travel_estimado_por_visita_plan_min,
)
from route_engine.frequency import dias_fijos_por_frecuencia, semanas_distribuidas
from route_engine.geo import (
    corregir_coordenada,
    haversine_km,
    normalizar_coord_geografica,
    parse_coordenada_a_float,
)
from route_engine.mapbox import (
    _geocode_cache,
    norm_provincia,
    obtener_direccion_desde_coordenadas,
)
from route_engine.scheduling import mostrar_progreso


def leer_excel_entrada(input_file):
    """
    Lee el archivo Excel de entrada con fallback de engines.
    Retorna un DataFrame limpio (sin filas vacias).
    """
    file_ext = os.path.splitext(input_file)[1].lower()

    try:
        if file_ext == ".xlsx":
            try:
                df = pd.read_excel(input_file, engine="openpyxl")
            except Exception:
                try:
                    df = pd.read_excel(input_file, engine="calamine")
                except Exception:
                    df = pd.read_excel(input_file)
        elif file_ext == ".xls":
            df = pd.read_excel(input_file, engine="xlrd")
        else:
            df = pd.read_excel(input_file, engine="openpyxl")
    except FileNotFoundError:
        print(f"      [!] ERROR: No se encontro el archivo '{input_file}'")
        raise
    except Exception as e:
        error_msg = str(e)
        if "BadZipFile" in error_msg or "not a zip file" in error_msg:
            print(f"      [!] ERROR: El archivo '{input_file}' esta corrupto o no es un archivo Excel valido")
            print("      [!] Posibles causas:")
            print("          - El archivo esta abierto en Excel. Por favor, cierralo e intenta de nuevo.")
            print("          - El archivo esta corrupto.")
            print("          - El archivo no es un formato Excel valido (.xlsx o .xls)")
        else:
            print(f"      [!] ERROR al leer el archivo: {error_msg}")
        raise

    # Normalizar nombres de columnas (coordenadas son clave para el ordenamiento de rutas)
    df = _normalizar_columnas_clave(df)

    # Quitar solo filas completamente vacias
    df = df.dropna(how="all")

    # Quitar solo filas sin coordenadas: sin LATITUD y LONGITUD no se puede enrutar.
    # No exigir DESCRIPCION: se puede usar vacio; las coordenadas son imprescindibles.
    if "LATITUD" in df.columns and "LONGITUD" in df.columns:
        df = df.dropna(subset=["LATITUD", "LONGITUD"])
    else:
        # Si no existen columnas de coordenadas, no eliminar por datos clave
        pass

    df = df.reset_index(drop=True)
    return df


def _normalizar_columnas_clave(df):
    """
    Unifica nombres de columnas para DESCRIPCION, LATITUD, LONGITUD
    (permite variantes como Latitud, Longitud, Descripción, etc.).
    """
    mapeo = {}
    for col in df.columns:
        if not isinstance(col, str):
            continue
        c = col.strip().upper().replace("Ó", "O").replace("Í", "I")
        if c in ("LATITUD", "LAT"):
            mapeo[col] = "LATITUD"
        elif c in ("LONGITUD", "LONG", "LNG"):
            mapeo[col] = "LONGITUD"
        elif c == "DESCRIPCION":
            mapeo[col] = "DESCRIPCION"
        elif c in ("ID", "CODIGO", "CODIGO_PUNTO", "ID_PUNTO", "ID PUNTO", "PUNTO_ID"):
            mapeo[col] = "ID"
    if mapeo:
        df = df.rename(columns=mapeo)
    return df


def corregir_coordenadas_df(df):
    """
    Corrige coordenadas en el DataFrame. Aplica recorte geográfico para coords
    fuera de rango (e.g. -32702E+14 → -3.2702). Marca filas inválidas.
    Retorna (df, coordenadas_corregidas, ubicaciones_invalidas).
    """
    coordenadas_corregidas = 0
    ubicaciones_invalidas = 0

    if "LATITUD" in df.columns:
        df["LATITUD"] = df["LATITUD"].astype(object)
    if "LONGITUD" in df.columns:
        df["LONGITUD"] = df["LONGITUD"].astype(object)

    for idx, row in df.iterrows():
        lat_original = row.get("LATITUD", 0)
        lon_original = row.get("LONGITUD", 0)

        # Normalizar con recorte de rango; devuelve None si es irrecuperable
        lat_norm = normalizar_coord_geografica(lat_original, tipo="lat")
        lon_norm = normalizar_coord_geografica(lon_original, tipo="lon")

        # Inválido: irrecuperable o ambas cero (valor por defecto / ausente)
        both_zero = (lat_norm == 0.0 and lon_norm == 0.0)
        either_none = (lat_norm is None or lon_norm is None)
        invalido = either_none or both_zero

        if invalido:
            df.at[idx, "_COORDENADAS_INVALIDAS"] = True
            # Distinguir entre cero genuino e irrecuperable
            raw_lat = parse_coordenada_a_float(lat_original)
            raw_lon = parse_coordenada_a_float(lon_original)
            if raw_lat == 0.0 and raw_lon == 0.0:
                df.at[idx, "_MOTIVO_INVALIDO"] = "coordenadas_cero"
            else:
                df.at[idx, "_MOTIVO_INVALIDO"] = "coordenada_invalida"
            ubicaciones_invalidas += 1
            # Para evitar excepciones ValueError en float() mas adelante al leer filas invalidas,
            # guardamos un fallback numerico (0.0) en el dataframe si no es valido/parseable.
            df.at[idx, "LATITUD"] = raw_lat if raw_lat is not None else 0.0
            df.at[idx, "LONGITUD"] = raw_lon if raw_lon is not None else 0.0
        else:
            df.at[idx, "_COORDENADAS_INVALIDAS"] = False
            df.at[idx, "_MOTIVO_INVALIDO"] = ""
            # Guardamos siempre el float normalizado en el DataFrame
            df.at[idx, "LATITUD"] = lat_norm
            df.at[idx, "LONGITUD"] = lon_norm
            
            raw_lat = parse_coordenada_a_float(lat_original)
            raw_lon = parse_coordenada_a_float(lon_original)
            if raw_lat != lat_norm or raw_lon != lon_norm:
                coordenadas_corregidas += 1
                if coordenadas_corregidas <= 3:
                    print(
                        f"         * {row.get('DESCRIPCION', 'N/A')[:30]}: "
                        f"({lat_original}, {lon_original}) -> ({lat_norm}, {lon_norm})"
                    )

    if coordenadas_corregidas > 3:
        print(f"         ... y {coordenadas_corregidas - 3} coordenadas mas corregidas")
    elif coordenadas_corregidas == 0:
        print("         * Todas las coordenadas estan correctas")

    if ubicaciones_invalidas > 0:
        print(f"      -> [!] {ubicaciones_invalidas} ubicaciones con coordenadas invalidas seran ignoradas")

    return df, coordenadas_corregidas, ubicaciones_invalidas


def determinar_provincias(df):
    """
    Determina PROVINCIA y LOCALIDAD por coordenadas usando geocodificacion.
    Agrega columnas PROVINCIA_COORD y LOCALIDAD_COORD al DataFrame.
    """
    print("      -> Determinando provincia por coordenadas (cache)...")
    coordenadas_unicas_df = set()
    for _, row in df.iterrows():
        if row.get("_COORDENADAS_INVALIDAS", False):
            continue
        try:
            lat_val = parse_coordenada_a_float(row.get("LATITUD"))
            lon_val = parse_coordenada_a_float(row.get("LONGITUD"))
            lat_f = lat_val if lat_val is not None else 0.0
            lon_f = lon_val if lon_val is not None else 0.0
        except Exception:
            continue
        if lat_f == 0.0 and lon_f == 0.0:
            continue
        coordenadas_unicas_df.add((round(lat_f, 6), round(lon_f, 6)))

    total_coords = len(coordenadas_unicas_df)
    if total_coords:
        for i, (lat_f, lon_f) in enumerate(coordenadas_unicas_df, 1):
            mostrar_progreso(i, total_coords, "      Progreso")
            obtener_direccion_desde_coordenadas(lat_f, lon_f)
        print()

    provincias_loc = []
    localidades_loc = []
    for _, row in df.iterrows():
        if row.get("_COORDENADAS_INVALIDAS", False):
            provincias_loc.append("")
            localidades_loc.append("")
            continue
        try:
            lat_val = parse_coordenada_a_float(row.get("LATITUD"))
            lon_val = parse_coordenada_a_float(row.get("LONGITUD"))
            lat_f = lat_val if lat_val is not None else 0.0
            lon_f = lon_val if lon_val is not None else 0.0
        except Exception:
            provincias_loc.append("")
            localidades_loc.append("")
            continue
        key_coord = (round(lat_f, 6), round(lon_f, 6))
        prov, ciudad_esp, _ = _geocode_cache.get(key_coord, ("", "", ""))
        provincias_loc.append(norm_provincia(prov))
        localidades_loc.append((ciudad_esp or "").strip())

    df["PROVINCIA_COORD"] = provincias_loc
    df["LOCALIDAD_COORD"] = localidades_loc
    return df


def inicializar_columnas(df):
    """Inicializa columnas de asignacion en el DataFrame."""
    df["Mercadista"] = ""
    df["Dia"] = ""
    df["Fecha"] = ""
    for col in DAY_COLUMNS:
        df[col] = ""
    return df


def crear_instancias_visita(df, tipo_ruta="tiempo_completo"):
    """
    Genera la lista de instancias de visita expandiendo por frecuencia.
    Retorna (visit_instances, ubicaciones_procesadas, ubicaciones_omitidas, visitas_sin_coordenadas).
    """
    visit_instances = []
    visitas_sin_coordenadas = []
    total_rows = len(df)
    ubicaciones_procesadas = 0
    ubicaciones_omitidas = 0

    columnas_lower = {c.lower(): c for c in df.columns}
    provincia_merc_col = None
    for cname_lower, cname_real in columnas_lower.items():
        if "provincia" in cname_lower or "procincia" in cname_lower:
            provincia_merc_col = cname_real
            break

    # Columna CADENA: la usa el tipo de carga "por cadena" para que un
    # mercaderista atienda puntos de una sola cadena comercial. Se busca de
    # forma tolerante porque en los Excel reales aparece como "CADENA",
    # "Cadena" o "CADENA COMERCIAL".
    cadena_col = None
    for cname_lower, cname_real in columnas_lower.items():
        if "cadena" in cname_lower:
            cadena_col = cname_real
            break

    # Ciudad declarada en el Excel (p. ej. "CIUDAD DEL PDV"). Tiene prioridad
    # sobre la ciudad deducida por geocodificación inversa: si el negocio la
    # escribe, es la que manda para agrupar por ciudad.
    ciudad_col = None
    for cname_lower, cname_real in columnas_lower.items():
        if "ciudad" in cname_lower and "provincia" not in cname_lower:
            ciudad_col = cname_real
            break

    for idx, row in df.iterrows():
        if row.get("_COORDENADAS_INVALIDAS", False):
            ubicaciones_omitidas += 1
            mostrar_progreso(idx + 1, total_rows, "      Progreso")
            desc_val = row.get("DESCRIPCION", "")
            tiempo = pd.to_numeric(row.get("TIEMPO DE SERVICIO", 0), errors="coerce")
            tiempo = float(tiempo) if pd.notna(tiempo) else 0.0
            freq = pd.to_numeric(row.get("FRECUENCIA MES", 0), errors="coerce")
            frecuencia_original = int(freq) if pd.notna(freq) else 1
            lat_val = parse_coordenada_a_float(row.get("LATITUD"))
            lon_val = parse_coordenada_a_float(row.get("LONGITUD"))
            lat = lat_val if lat_val is not None else 0.0
            lon = lon_val if lon_val is not None else 0.0
            motivo_val = row.get("_MOTIVO_INVALIDO") or "coordenadas_cero"
            visitas_sin_coordenadas.append({
                "motivo": str(motivo_val),
                "descripcion": str(desc_val),
                "lat": lat,
                "lon": lon,
                "tiempo": tiempo,
                "frecuencia_mes": frecuencia_original,
                "provincia_punto": norm_provincia(row.get("PROVINCIA_COORD", "")) or "",
            })
            continue

        # Solo Tiempo de Servicio cuenta para trabajo (día/semana/mes); no tiempo entre sucursales
        tiempo = pd.to_numeric(row.get("TIEMPO DE SERVICIO", 0), errors="coerce")
        tiempo = float(tiempo) if pd.notna(tiempo) else 0.0
        freq = pd.to_numeric(row.get("FRECUENCIA MES", 0), errors="coerce")

        frecuencia_original = int(freq) if pd.notna(freq) else 1
        dias_fijos = dias_fijos_por_frecuencia(freq, idx_seed=idx)

        lat_val = parse_coordenada_a_float(row.get("LATITUD"))
        lon_val = parse_coordenada_a_float(row.get("LONGITUD"))
        lat = lat_val if lat_val is not None else 0.0
        lon = lon_val if lon_val is not None else 0.0

        desc_val = row.get("DESCRIPCION", "")
        group_key = f"{round(lat, 6)}|{round(lon, 6)}|{str(desc_val).upper().strip()}"

        id_val = row.get("ID") if "ID" in df.columns else None
        id_str = str(id_val).strip() if (id_val is not None and pd.notna(id_val) and str(id_val).strip() != "") else None

        if tipo_ruta == "medio_tiempo":
            # El ID agrupa las filas de una misma tienda, pero en la entrada real
            # hay IDs repetidos entre locales distintos y lejanos (el 101447 lo
            # comparten COMERCIAL BBB y COMERCIAL KOALOS). Agrupar solo por ID
            # los ataba al mismo mercaderista y producía jornadas con saltos de
            # 485 km. Con la coordenada dentro de la clave, las filas de una
            # misma tienda siguen juntas y los locales distintos se separan.
            punto_key = (
                f"ID_{id_str}|{round(lat, 6)}|{round(lon, 6)}" if id_str else group_key
            )
        else:
            punto_key = idx

        # Provincia y HC por mercaderista (si existen en el Excel)
        provincia_merc = None
        hc_disponible = None
        if provincia_merc_col or columnas_lower.get("hc"):
            if provincia_merc_col and provincia_merc_col in df.columns:
                provincia_merc = row.get(provincia_merc_col, None)
            hc_col_row = columnas_lower.get("hc")
            if hc_col_row and hc_col_row in df.columns:
                hc_val = pd.to_numeric(row.get(hc_col_row, 0), errors="coerce")
                if pd.notna(hc_val):
                    hc_disponible = int(hc_val)

        provincia_punto = norm_provincia(row.get("PROVINCIA_COORD", "")) or "SIN_PROVINCIA"
        localidad_punto = (row.get("LOCALIDAD_COORD") or "").strip() or provincia_punto

        cadena_punto = ""
        if cadena_col:
            v = row.get(cadena_col)
            cadena_punto = "" if (v is None or pd.isna(v)) else str(v).strip().upper()

        ciudad_punto = ""
        if ciudad_col:
            v = row.get(ciudad_col)
            ciudad_punto = "" if (v is None or pd.isna(v)) else str(v).strip().upper()
        if not ciudad_punto:
            ciudad_punto = str(localidad_punto or "").strip().upper()

        base_instance = {
            "idx": idx,
            "punto_key": punto_key,
            "punto_id": id_str or group_key,
            "tiempo": tiempo,
            "descripcion": desc_val,
            "farmacia_hasta_15": es_farmacia_hasta_15(desc_val),
            "lat": lat,
            "lon": lon,
            "group_key": group_key,
            "provincia_mercaderista": provincia_merc,
            "hc_disponible": hc_disponible,
            "provincia_punto": provincia_punto,
            "localidad_punto": localidad_punto,
            "cadena_punto": cadena_punto,
            "ciudad_punto": ciudad_punto,
            "frecuencia_mes": frecuencia_original,
        }

        if dias_fijos:
            # 8→2/semana, 12→3/semana, 16→4/semana, 20→5/semana; 4 semanas cada uno
            for semana_num in range(1, NUM_SEMANAS_POR_MERCADISTA + 1):
                for dia_req in dias_fijos:
                    inst = dict(base_instance)
                    inst.update({
                        "required_day": dia_req,
                        "fixed_mercadista": True,
                        "semana_num": semana_num,
                    })
                    visit_instances.append(inst)
        else:
            semanas_asignadas = semanas_distribuidas(frecuencia_original, idx_seed=idx)
            for semana_num in semanas_asignadas:
                inst = dict(base_instance)
                inst.update({
                    "required_day": None,
                    "fixed_mercadista": False,
                    "semana_num": semana_num,
                })
                visit_instances.append(inst)

        ubicaciones_procesadas += 1
        mostrar_progreso(idx + 1, total_rows, "      Progreso")

    return visit_instances, ubicaciones_procesadas, ubicaciones_omitidas, visitas_sin_coordenadas


def detectar_config_mercadistas(df, visit_instances):
    """
    Detecta configuracion de mercadistas desde el Excel:
    - Por provincia (columnas Procincia_mercaderista + Mercaderistas por PROVINCIA)
    - Por HC (columna HC)
    Retorna (mercadistas_plan, target_mercadistas) o (None, None) si no hay config.
    mercadistas_plan: list[(merc_name, provincia)] o None.
    """
    columnas_lower = {c.lower(): c for c in df.columns}
    prov_cfg_col = None
    mercs_por_prov_col = None
    for cname_lower, cname_real in columnas_lower.items():
        if "procincia_mercaderista" in cname_lower or "provincia_mercaderista" in cname_lower:
            prov_cfg_col = cname_real
        if "mercaderistas por provincia" in cname_lower:
            mercs_por_prov_col = cname_real

    mercadistas_plan = None

    if prov_cfg_col and mercs_por_prov_col:
        try:
            cfg = df[[prov_cfg_col, mercs_por_prov_col]].copy()
            cfg[prov_cfg_col] = cfg[prov_cfg_col].apply(norm_provincia)
            cfg[mercs_por_prov_col] = pd.to_numeric(cfg[mercs_por_prov_col], errors="coerce").fillna(0).astype(int)
            cfg = cfg[cfg[prov_cfg_col] != ""]
            if not cfg.empty:
                mercs_por_prov = cfg.groupby(prov_cfg_col)[mercs_por_prov_col].max().to_dict()
                provs_puntos = set(
                    norm_provincia(x.get("provincia_punto", ""))
                    for x in visit_instances
                    if x.get("provincia_punto")
                )
                provs_cfg = set(mercs_por_prov.keys())
                faltantes = sorted([p for p in provs_puntos if p and p not in provs_cfg])
                if faltantes:
                    print(
                        f"      [!] Advertencia: Hay provincias en puntos sin configuracion "
                        f"de mercaderistas: {', '.join(faltantes)}"
                    )
                    print("      [!] Se asignara 1 mercadista por cada provincia faltante.")
                    for p in faltantes:
                        mercs_por_prov[p] = max(1, int(mercs_por_prov.get(p, 0)))

                mercadistas_plan = []
                merc_num = 1
                for prov, cant in sorted(mercs_por_prov.items(), key=lambda x: x[0]):
                    cant = int(cant) if cant is not None else 0
                    if cant <= 0:
                        continue
                    for _ in range(cant):
                        mercadistas_plan.append((f"Mercadista {merc_num:02d}", prov))
                        merc_num += 1
                if mercadistas_plan:
                    print(f"      -> Mercadistas definidos por provincia en Excel: {len(mercadistas_plan)}")
        except Exception:
            mercadistas_plan = None

    if mercadistas_plan is not None:
        return mercadistas_plan

    # Fallback: HC
    hc_col = columnas_lower.get("hc")
    provincia_merc_col = None
    for cname_lower, cname_real in columnas_lower.items():
        if "provincia" in cname_lower or "procincia" in cname_lower:
            provincia_merc_col = cname_real
            break

    total_hc = None
    if hc_col:
        try:
            if provincia_merc_col:
                hc_por_prov = df[[provincia_merc_col, hc_col]].dropna(subset=[hc_col]).copy()
                if not hc_por_prov.empty:
                    hc_por_prov[hc_col] = pd.to_numeric(hc_por_prov[hc_col], errors="coerce").fillna(0)
                    hc_por_prov = hc_por_prov.groupby(provincia_merc_col)[hc_col].max()
                    total_hc = int(hc_por_prov.sum())
            else:
                total_hc = int(pd.to_numeric(df[hc_col], errors="coerce").fillna(0).max())
        except Exception:
            total_hc = None

    if total_hc is not None and total_hc > 0:
        print(f"      -> Mercadistas definidos por HC en Excel: {total_hc}")
        return [(f"Mercadista {i:02d}", None) for i in range(1, total_hc + 1)]

    return None


def _centroide(puntos):
    """Centro geográfico aproximado de una lista de (lat, lon)."""
    if not puntos:
        return (0.0, 0.0)
    return (
        sum(p[0] for p in puntos) / len(puntos),
        sum(p[1] for p in puntos) / len(puntos),
    )


# Márgenes de la reserva de viaje por visita al dimensionar la flota. Acotan el
# efecto de datos raros (un único punto aislado, o cientos de puntos en la misma
# manzana) sobre el número de mercadistas.
_TRAVEL_RESERVA_MIN = 4.0
_TRAVEL_RESERVA_MAX = 30.0


def _minutos_viaje_por_visita(puntos):
    """
    Reserva de desplazamiento por visita, estimada desde la densidad real de
    los puntos: distancia media al vecino más cercano, convertida a minutos
    con el modelo de viaje del motor.

    Una reserva plana por visita ignora que recorrer 90 puntos dentro de
    Guayaquil no cuesta lo mismo que recorrer 7 repartidos por una provincia
    rural. Como el número de mercadistas sale de un `ceil`, sobreestimar el
    viaje aunque sea un poco puede cruzar el redondeo y añadir un mercadista
    entero que después queda medio vacío.
    """
    from route_engine.config import (
        jornada_incluye_viaje,
        travel_estimado_por_visita_plan_min,
    )
    from route_engine.geo import minutos_viaje_desde_km

    # Si el desplazamiento no descuenta de la cuota, no se reserva nada para él
    # al dimensionar: la flota sale de los minutos de servicio y punto.
    if not jornada_incluye_viaje():
        return 0.0

    if not puntos or len(puntos) < 2:
        return float(travel_estimado_por_visita_plan_min())

    # Con muchos puntos el cálculo exacto es O(n²); una muestra basta para
    # estimar la densidad.
    muestra = puntos if len(puntos) <= 150 else puntos[:: max(1, len(puntos) // 150)]

    distancias = []
    for i, a in enumerate(muestra):
        mejor = None
        for j, b in enumerate(muestra):
            if i == j:
                continue
            d = haversine_km(a[0], a[1], b[0], b[1])
            if mejor is None or d < mejor:
                mejor = d
        if mejor is not None:
            distancias.append(mejor)

    if not distancias:
        return float(travel_estimado_por_visita_plan_min())

    from route_engine.config import DISTANCE_FACTOR_CARRETERA

    km_medio = (sum(distancias) / len(distancias)) * (DISTANCE_FACTOR_CARRETERA or 1.0)
    return max(_TRAVEL_RESERVA_MIN, min(_TRAVEL_RESERVA_MAX, minutos_viaje_desde_km(km_medio)))


def _absorber_zonas_pequenas(zonas):
    """
    Funde una zona con poca carga en la zona vecina más cercana, siempre que la
    zona resultante siga cabiendo en el diámetro máximo (2 × RADIO_ZONA_KM).

    Una localidad aislada con una sola tienda formaba su propia zona y con ella
    se llevaba un mercadista completo para un 5% de jornada. Fundirla con la
    zona de al lado aprovecha esa capacidad sin romper la compacidad: la
    comprobación es sobre la distancia real entre los puntos más lejanos de la
    zona fusionada, no sobre centroides, así que el límite se cumple siempre.
    """
    from route_engine.config import RADIO_ZONA_KM, cuota_mes

    diametro_max = 2 * RADIO_ZONA_KM
    # Umbral: por debajo de un tercio de mercadista no compensa una zona propia.
    umbral = cuota_mes() / 3.0

    def carga(z):
        return sum(p["servicio"] for p in z)

    def diametro_union(a, b):
        return max(
            haversine_km(p["lat"], p["lon"], q["lat"], q["lon"]) for p in a for q in b
        )

    cambiado = True
    while cambiado:
        cambiado = False
        pequenas = sorted(
            (z for z in zonas if carga(z) < umbral), key=carga
        )
        for chica in pequenas:
            mejor, mejor_d = None, None
            for otra in zonas:
                if otra is chica:
                    continue
                d = diametro_union(chica, otra)
                # El diámetro de la propia `otra` ya cumple; basta comprobar la unión.
                if d > diametro_max:
                    continue
                if mejor_d is None or d < mejor_d:
                    mejor, mejor_d = otra, d
            if mejor is not None:
                mejor.extend(chica)
                zonas.remove(chica)
                cambiado = True
                break

    return zonas


def _clusterizar_visitas(visit_instances):
    """
    Agrupa las visitas en ZONAS de trabajo geográficamente compactas y anota
    cada instancia con su zona (`inst["zona"]`).

    Algoritmo: se elige como ancla el punto con más carga acumulada a su
    alrededor, se lleva a su zona todo lo que esté dentro de RADIO_ZONA_KM, y
    se repite con lo que queda. Por construcción, dos puntos de la misma zona
    nunca distan más de 2 × RADIO_ZONA_KM.

    Sustituye a la agrupación por provincias, que no servía como control
    geográfico: encadenaba zonas A→B→C hasta cubrir 214 km (un mercadista con
    puntos en Guayaquil y Cuenca a la vez, o repartido entre Bolívar, Napo y
    Pastaza). La frontera provincial no dice nada sobre si dos puntos son
    atendibles por la misma persona; la distancia entre ellos, sí.

    Returns: list[(zona_key, carga_combinada, n_visitas)]
    """
    puntos = {}
    for inst in visit_instances:
        try:
            lat, lon = float(inst.get("lat") or 0), float(inst.get("lon") or 0)
            tiempo = float(inst.get("tiempo") or 0)
        except (TypeError, ValueError):
            continue
        if tiempo <= 0 or (lat == 0 and lon == 0):
            continue
        clave = (round(lat, 6), round(lon, 6))
        p = puntos.setdefault(clave, {"lat": lat, "lon": lon, "servicio": 0.0,
                                      "visitas": 0, "provincias": {}, "insts": []})
        p["servicio"] += tiempo
        p["visitas"] += 1
        p["insts"].append(inst)
        prov = norm_provincia(inst.get("provincia_punto", "")) or "SIN_PROVINCIA"
        p["provincias"][prov] = p["provincias"].get(prov, 0) + 1

    if not puntos:
        return []

    from route_engine.config import RADIO_ZONA_KM

    libres = list(puntos.values())
    zonas = []
    while libres:
        # Ancla: el punto con más carga de servicio a su alrededor. Empezar por
        # las concentraciones evita que un punto aislado se lleve como ancla a
        # media ciudad y descentre la zona.
        def carga_vecina(p):
            return sum(
                q["servicio"] for q in libres
                if haversine_km(p["lat"], p["lon"], q["lat"], q["lon"]) <= RADIO_ZONA_KM
            )

        ancla = max(libres, key=carga_vecina)
        grupo = [
            q for q in libres
            if haversine_km(ancla["lat"], ancla["lon"], q["lat"], q["lon"]) <= RADIO_ZONA_KM
        ]
        libres = [q for q in libres if q not in grupo]
        zonas.append(grupo)

    zonas = _absorber_zonas_pequenas(zonas)

    desglose = []
    for idx, grupo in enumerate(zonas, 1):
        # Nombre legible: provincia dominante + número, para poder distinguir
        # varias zonas dentro de una misma provincia grande.
        conteo = {}
        for p in grupo:
            for prov, n in p["provincias"].items():
                conteo[prov] = conteo.get(prov, 0) + n
        dominante = max(conteo, key=conteo.get) if conteo else "SIN_PROVINCIA"
        zona_key = f"{dominante} Z{idx:02d}"

        coords = [(p["lat"], p["lon"]) for p in grupo]
        reserva = _minutos_viaje_por_visita(coords)
        servicio = sum(p["servicio"] for p in grupo)
        n_visitas = sum(p["visitas"] for p in grupo)

        for p in grupo:
            for inst in p["insts"]:
                inst["zona"] = zona_key

        desglose.append((zona_key, servicio + n_visitas * reserva, n_visitas))

    return desglose


def calcular_plan_mercadistas_por_provincia(visit_instances):
    """
    Dimensiona la flota a partir de la carga real de trabajo por zona.

    Anota cada visita con su zona (ver `_clusterizar_visitas`) y asigna a cada
    zona ceil(carga / cuota_mes()) mercadistas. Toda la aritmética
    va en minutos COMBINADOS (servicio + desplazamiento), que es la unidad en la
    que se mide la jornada de 8 h.

    Returns:
        (plan, desglose)
          plan: list[(merc_name, zona_key)]
          desglose: list[(zona_key, carga_combinada_minutos, n_mercadistas)]
    """
    zonas = _clusterizar_visitas(visit_instances)
    if not zonas:
        return [], []

    desglose = []
    plan = []
    merc_num = 1
    for zona_key, carga, _n_visitas in sorted(zonas, key=lambda z: -z[1]):
        n_mercs = max(1, int(math.ceil(carga / cuota_mes())))
        desglose.append((zona_key, carga, n_mercs))
        for _ in range(n_mercs):
            plan.append((f"Mercadista {merc_num:02d}", zona_key))
            merc_num += 1

    return plan, desglose


def columna_frecuencia_mes(df: pd.DataFrame) -> str | None:
    """Nombre de columna de frecuencia mensual en el Excel maestro, si existe."""
    for col in df.columns:
        if not isinstance(col, str):
            continue
        c = col.strip().upper().replace("Ó", "O").replace("Í", "I")
        if c in ("FRECUENCIA MES", "FRECUENCIA_MES", "FRECUENCIA"):
            return col
    return None


def clave_punto_frecuencia(descripcion, lat, lon) -> tuple:
    """Clave (descripción, lat6, lon6) para cruzar puntos entre hojas."""
    desc = str(descripcion or "").strip().upper()
    la = parse_coordenada_a_float(lat) if not isinstance(lat, (int, float)) else lat
    lo = parse_coordenada_a_float(lon) if not isinstance(lon, (int, float)) else lon
    if la is None or lo is None:
        return (desc, None, None)
    return (desc, round(float(la), 6), round(float(lo), 6))


def index_frecuencia_mes_por_punto(df: pd.DataFrame) -> dict:
    """
    Índice {(DESCRIPCION, lat6, lon6): frecuencia_mes int} desde el Excel maestro.
    """
    idx: dict = {}
    if df is None or df.empty:
        return idx
    col_freq = columna_frecuencia_mes(df)
    if not col_freq:
        return idx
    work = _normalizar_columnas_clave(df.copy())
    if "LATITUD" not in work.columns or "LONGITUD" not in work.columns:
        return idx
    for _, row in work.iterrows():
        if row.get("_COORDENADAS_INVALIDAS", False):
            continue
        desc = str(row.get("DESCRIPCION", "") or "").strip()
        if not desc:
            continue
        try:
            lat_val = parse_coordenada_a_float(row.get("LATITUD"))
            lon_val = parse_coordenada_a_float(row.get("LONGITUD"))
            lat_f = lat_val if lat_val is not None else 0.0
            lon_f = lon_val if lon_val is not None else 0.0
        except (TypeError, ValueError):
            continue
        if lat_f == 0.0 and lon_f == 0.0:
            continue
        freq = pd.to_numeric(row.get(col_freq), errors="coerce")
        if pd.isna(freq) or float(freq) <= 0:
            continue
        key = clave_punto_frecuencia(desc, lat_f, lon_f)
        if key[1] is None:
            continue
        idx[key] = idx.get(key, 0) + int(freq)
    return idx


def lookup_frecuencia_mes(
    freq_index: dict,
    descripcion,
    lat,
    lon,
    *,
    explicit=None,
) -> int | None:
    """Resuelve frecuencia: valor explícito > índice maestro."""
    if explicit is not None and not (isinstance(explicit, float) and pd.isna(explicit)):
        try:
            v = int(float(explicit))
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    if not freq_index:
        return None
    key = clave_punto_frecuencia(descripcion, lat, lon)
    if key[1] is None:
        return None
    return freq_index.get(key)
