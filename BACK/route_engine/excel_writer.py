"""
Generacion del archivo Excel de salida.
"""

from __future__ import annotations

import re
from collections import defaultdict
from io import BytesIO
from typing import Optional, Set

import openpyxl
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from route_engine.config import (
    dias_de_mercadista,
    mercadistas_fin_semana,
    carga_jornada,
    cuota_dia,
    cuota_mes,
    hora_fin_jornada,
    tope_jornada_real,
    jornada_incluye_viaje,
    max_dia_flex,
    max_servicio_dia,
    viaje_en_agenda,
)
from route_engine.run_config import (
    SHEET_CONFIG_PROCESAMIENTO,
    build_config_procesamiento_df,
)
from route_engine.excel_reader import (
    _normalizar_columnas_clave,
    clave_punto_frecuencia,
    columna_frecuencia_mes,
    index_frecuencia_mes_por_punto,
    lookup_frecuencia_mes,
    minutos_normalizados,
    parse_coordenada_a_float,
)
from route_engine.geo import (
    haversine_km,
    minutos_viaje_desde_km,
    normalizar_coord_geografica,
)
from route_engine.carretera import recalcular_tramos_por_carretera
from route_engine.excel_formato import (
    drop_spurious_total_rows_horarios_df,
    format_horarios_detalle_worksheet,
)
from route_engine.excel_descarga import (
    HOJAS_SOLO_INTERNAS,
    SHEET_RUTAS_SEMANA,
    _construir_hoja_rutas_semana,
    build_horarios_excel_download_bytes,
)
from route_engine.horarios_postproceso import (  # noqa: F401  (reexport)
    _aplicar_tope_combinado_diario,
    _ordenar_jornada_por_cercania,
    _postprocesar_horarios,
    _recalcular_ruta,
    _reubicar_en_otro_dia,
)
from route_engine.frecuencia import (
    _info_puntos_desde_df,
    _semana_a_int,
    reconciliar_pendientes_por_frecuencia,
)
from route_engine.mapbox import (
    _geocode_cache,
    calcular_tiempo_entre,
    provincia_display,
    ruta_por_carretera,
)
from route_engine.scheduling import (
    calcular_horario_almuerzo,
    mostrar_progreso,
)


def _indice_canal_cadena(df):
    """
    Canal y cadena de cada punto, tal como vienen en el Excel de entrada.

    Se indexa por coordenadas MÁS nombre, y se deja el nombre y las coordenadas
    sueltos como respaldo. Ninguna de las dos claves basta por su cuenta: en el
    archivo real hay tres tiendas distintas con las mismas coordenadas —una de
    otra cadena—, así que indexar solo por coordenadas le colgaba a SUPER AKI LA
    JOYA la cadena de su vecino; y el nombre solo falla si las coordenadas se
    corrigieron por el camino.

    Sin este índice habría que arrastrar los dos campos por todo el motor
    —asignación, rescate, red de seguridad— para acabar escribiéndolos igual al
    final.
    """
    from route_engine.excel_reader import columna_canal, columna_cadena

    col_canal = columna_canal(df)
    col_cadena = columna_cadena(df)
    if col_canal is None and col_cadena is None:
        return {}, {}, {}

    col_desc = None
    for candidata in ("DESCRIPCION", "Descripción", "DESCRIPCIÓN", "Descripcion"):
        if candidata in df.columns:
            col_desc = candidata
            break

    por_local, por_nombre, por_coord = {}, {}, {}
    for _, fila in df.iterrows():
        canal = str(fila.get(col_canal, "") or "").strip() if col_canal else ""
        cadena = str(fila.get(col_cadena, "") or "").strip().upper() if col_cadena else ""
        if not canal and not cadena:
            continue
        nombre = str(fila.get(col_desc, "") or "").strip().upper() if col_desc else ""
        try:
            coord = (
                round(float(fila.get("LATITUD")), 6),
                round(float(fila.get("LONGITUD")), 6),
            )
        except (TypeError, ValueError):
            coord = None
        if coord and nombre:
            por_local.setdefault((coord, nombre), (canal, cadena))
        if nombre:
            por_nombre.setdefault(nombre, (canal, cadena))
        if coord:
            por_coord.setdefault(coord, (canal, cadena))
    return por_local, por_nombre, por_coord


def _agregar_canal_cadena(horarios_df, df):
    """
    Añade CANAL y CADENA a las filas de horarios, buscándolos por coordenadas y,
    si no cuadran, por nombre del punto.

    Si el Excel de entrada no trae esas columnas, el DataFrame sale intacto: los
    archivos antiguos siguen generándose exactamente igual que antes.
    """
    if horarios_df.empty:
        return horarios_df

    por_local, por_nombre, por_coord = _indice_canal_cadena(df)
    if not por_local and not por_nombre and not por_coord:
        return horarios_df

    def _buscar(fila):
        try:
            coord = (
                round(float(fila.get("Latitud")), 6),
                round(float(fila.get("Longitud")), 6),
            )
        except (TypeError, ValueError):
            coord = None
        nombre = str(fila.get("Descripción", "") or "").strip().upper()
        dato = por_local.get((coord, nombre)) if coord and nombre else None
        if dato is None and nombre:
            dato = por_nombre.get(nombre)
        if dato is None and coord:
            dato = por_coord.get(coord)
        return dato or ("", "")

    valores = horarios_df.apply(_buscar, axis=1)
    horarios_df = horarios_df.copy()
    horarios_df["CANAL"] = [v[0] for v in valores]
    horarios_df["CADENA"] = [v[1] for v in valores]
    return horarios_df


def generar_excel_salida(output_file, horarios_df, df, visit_instances, state):
    """
    Genera el archivo Excel de salida con las hojas:
    - Horarios_Detalle
    - Categorias
    """
    import sys as _sys

    _required_cols = ("Mercadista", "Día", "Fecha")
    _missing_required = [c for c in _required_cols if c not in horarios_df.columns]
    if _missing_required:
        # Bandera visible: este archivo será inservible para el frontend (vista
        # de rutas/comparativa rompen sin estas columnas). El motivo suele
        # estar más arriba (asignación vacía, formato de entrada incompatible).
        print(
            "      [!!!] ATENCIÓN: horarios_df no contiene las columnas "
            f"requeridas {_missing_required}. El Excel generado quedará "
            "incompleto y las vistas de rutas/comparativa fallarán al leerlo. "
            "Revisa el Excel de entrada y el log de asignación.",
            file=_sys.stderr,
            flush=True,
        )
        print(
            f"      [!!!] Columnas presentes en horarios_df: {list(horarios_df.columns)}",
            file=_sys.stderr,
            flush=True,
        )
        print(
            f"      [!!!] all_day_summaries tiene {len(state.all_day_summaries)} entradas; "
            f"primera entrada: {state.all_day_summaries[0] if state.all_day_summaries else 'VACÍO'}",
            file=_sys.stderr,
            flush=True,
        )

    # Rellenar PROVINCIA/CIUDAD/CALLE desde cache
    for summary in state.all_day_summaries:
        lat = summary.get("Latitud", "")
        lon = summary.get("Longitud", "")
        if lat == "" or lon == "":
            continue
        try:
            lat_f = float(lat)
            lon_f = float(lon)
        except Exception:
            continue
        if lat_f == 0.0 and lon_f == 0.0:
            continue
        key_coord = (round(lat_f, 6), round(lon_f, 6))
        provincia, ciudad, calle = _geocode_cache.get(key_coord, ("", "", ""))
        summary["PROVINCIA"] = provincia
        summary["CIUDAD"] = ciudad
        summary["CALLE"] = calle

    # Canal y cadena de cada visita, tomados del Excel de entrada. Se añaden
    # aquí, sobre el DataFrame ya montado: llevarlos por todo el motor solo para
    # escribirlos al final no aportaría nada.
    horarios_df = _agregar_canal_cadena(horarios_df, df)

    horarios_df = _postprocesar_horarios(horarios_df, visit_instances, state)
    horarios_df = drop_spurious_total_rows_horarios_df(horarios_df)

    # Kilómetros y minutos REALES de carretera, ya con las rutas cerradas. El
    # motor planifica con una estimación para poder comparar miles de
    # combinaciones sin salir a la red; lo que se entrega debe ser lo que se
    # recorre de verdad.
    (
        jornadas_ok,
        jornadas_total,
        jornadas_tarde,
        filas_retiradas,
    ) = recalcular_tramos_por_carretera(horarios_df, optimizar_orden=True)
    if filas_retiradas:
        # Vuelven a pendientes: la reconciliación de frecuencia, que corre justo
        # después, ya se encarga de dejar el invariante
        # «agendadas + pendientes == frecuencia mes» en su sitio.
        print(
            f"      [!] {len(filas_retiradas)} visita(s) retiradas: con los tiempos "
            f"reales de carretera no cabían en el reloj de su jornada "
            f"(tope {tope_jornada_real()} min). Vuelven a pendientes."
        )
    if jornadas_total:
        if jornadas_ok:
            print(
                f"      -> Desplazamiento por carretera: {jornadas_ok} de "
                f"{jornadas_total} jornada(s) recalculadas con distancias reales"
            )
            if jornadas_tarde:
                limite_txt = f"{hora_fin_jornada() // 60:02d}:{hora_fin_jornada() % 60:02d}"
                print(
                    f"      [!] {jornadas_tarde} jornada(s) terminan después de las "
                    f"{limite_txt} con los tiempos reales de carretera. No se "
                    f"recortan: revisar si esas visitas deben pasar a otro día."
                )
        else:
            print(
                "      [!] No se pudo consultar la distancia por carretera "
                "(¿falta MAPBOX_ACCESS_TOKEN?); se mantiene la estimación en "
                "línea recta corregida."
            )

    # Reconciliación de frecuencia: tras fijar el set agendado definitivo,
    # asegurar que por cada punto (agendadas + pendientes) == FRECUENCIA MES.
    # Cubre visitas que se perdieron río arriba sin quedar en pendientes.
    reconciliar_pendientes_por_frecuencia(horarios_df, df, state)

    # Validacion de conteo
    total_esperado = len(visit_instances)
    total_generado = len(horarios_df) if not horarios_df.empty else 0
    if total_esperado != total_generado:
        print(
            f"      [!] Diferencia: se esperaban {total_esperado} visitas "
            f"y se generaron {total_generado} filas en Horarios_Detalle."
        )

    with pd.ExcelWriter(output_file, engine="openpyxl") as writer:
        print("      -> Escribiendo hoja 'Horarios_Detalle'...")
        horarios_df.to_excel(writer, sheet_name="Horarios_Detalle", index=False)

        try:
            ws = writer.book["Horarios_Detalle"]
            plan_names = set(state.plan_names_base)
            nombres_en_excel = (
                set(horarios_df["Mercadista"].astype(str).unique())
                if "Mercadista" in horarios_df.columns
                else set()
            )
            mercadistas_extra = nombres_en_excel - plan_names
            format_horarios_detalle_worksheet(ws, mercadistas_extra if mercadistas_extra else None)
        except Exception:
            pass

        # Hoja Categorias
        print("      -> Escribiendo hoja 'Categorias'...")
        provincias = []
        ciudades = []
        calles = []
        for _, row in df.iterrows():
            lat = row.get("LATITUD", 0)
            lon = row.get("LONGITUD", 0)
            try:
                lat_f = float(lat)
                lon_f = float(lon)
                key_coord = (round(lat_f, 6), round(lon_f, 6))
                provincia, ciudad, calle = _geocode_cache.get(key_coord, ("", "", ""))
            except Exception:
                provincia, ciudad, calle = ("", "", "")
            provincias.append(provincia)
            ciudades.append(ciudad)
            calles.append(calle)

        categorias_df = pd.DataFrame({
            "ID": df.index + 1,
            "DESCRIPCION": df.get("DESCRIPCION", ""),
            "CATEGORIA": df.get("CATEGORIA", "DESCONOCIDO"),
            "PROVINCIA": provincias,
            "CIUDAD": ciudades,
            "CALLE": calles,
        })
        categorias_df.to_excel(writer, sheet_name="Categorias", index=False)

        # Hoja Frecuencia_Puntos: mercadista, punto único (Descripción), frecuencia de visitas
        print("      -> Escribiendo hoja 'Frecuencia_Puntos'...")
        if not horarios_df.empty and "Mercadista" in horarios_df.columns and "Descripción" in horarios_df.columns:
            freq_df = (
                horarios_df.groupby(["Mercadista", "Descripción"], dropna=False)
                .size()
                .reset_index(name="Frecuencia")
            )
            freq_df = freq_df.sort_values(["Mercadista", "Frecuencia"], ascending=[True, False])
            freq_df.to_excel(writer, sheet_name="Frecuencia_Puntos", index=False)

        # Hoja Provincias_Porcentaje: ocupación de la jornada por mercadista y
        # provincia. El 100% es la capacidad mensual COMBINADA de un mercadista
        # (cuota_mes() = 480 min/día × 5 × 4 = 9600), y los
        # minutos contados son servicio + desplazamiento.
        #
        # Antes se dividía solo el servicio entre 10000: un mercadista con 300
        # min en tienda y 180 en carretera —jornada de 8 h completa— aparecía
        # al 60%, y parecía que sobraba capacidad donde no la había.
        print("      -> Escribiendo hoja 'Provincias_Porcentaje'...")
        if not horarios_df.empty and "Mercadista" in horarios_df.columns and "PROVINCIA" in horarios_df.columns:
            col_serv = "Tiempo Servicio (min)"
            col_viaje = "Tiempo entre sucursal (min)"
            prov_calc = horarios_df.copy()
            prov_calc[col_serv] = pd.to_numeric(prov_calc.get(col_serv, 0), errors="coerce").fillna(0)
            prov_calc[col_viaje] = pd.to_numeric(prov_calc.get(col_viaje, 0), errors="coerce").fillna(0)
            prov_calc["_combinado"] = carga_jornada(prov_calc[col_serv], prov_calc[col_viaje])
            prov_df = (
                prov_calc.groupby(["Mercadista", "PROVINCIA"], dropna=False)[
                    [col_serv, "_combinado"]
                ]
                .sum()
                .reset_index()
            )
            prov_df["PROVINCIA"] = prov_df["PROVINCIA"].fillna("").astype(str).str.strip()
            prov_df = prov_df[prov_df["PROVINCIA"] != ""]
            prov_df["Minutos"] = prov_df["_combinado"].round(0).astype(int)
            prov_df["Minutos Servicio"] = prov_df[col_serv].round(0).astype(int)
            prov_df["Porcentaje"] = (
                (prov_df["Minutos"] / cuota_mes()) * 100
            ).round(1)
            prov_df["Provincia_Porcentaje"] = (
                prov_df["PROVINCIA"].astype(str) + " " + prov_df["Porcentaje"].astype(str) + "%"
            )
            prov_df = prov_df[
                ["Mercadista", "Provincia_Porcentaje", "Minutos", "Minutos Servicio"]
            ]
            prov_df = prov_df.sort_values(["Mercadista", "Minutos"], ascending=[True, False])
            prov_df.to_excel(writer, sheet_name="Provincias_Porcentaje", index=False)

        # Hoja Pendientes_Sin_Asignar: visitas que no se pudieron asignar a
        # ningún mercadista/día por superar el tope de jornada (480 min de
        # servicio+desplazamiento) o por falta de hueco mensual. Una fila por
        # (descripción, semana) deduplicada.
        print("      -> Escribiendo hoja 'Pendientes_Sin_Asignar'...")
        freq_idx = index_frecuencia_mes_por_punto(df) if df is not None else {}
        pendientes_data = []
        seen_key = set()
        for p in state.puntos_pendientes:
            desc = str(p.get("descripcion", "")).strip()
            semana_v = p.get("semana", "")
            semana_str = (
                f"semana {semana_v}"
                if isinstance(semana_v, int)
                else str(semana_v).strip()
            )
            try:
                lat_v = float(p.get("lat") or 0)
                lon_v = float(p.get("lon") or 0)
            except (TypeError, ValueError):
                lat_v, lon_v = 0.0, 0.0
            # _dedup_token único en filas de reconciliación: evita que varias
            # visitas faltantes del mismo punto/semana se colapsen en una sola.
            key = (desc, round(lat_v, 6), round(lon_v, 6), semana_str, p.get("_dedup_token", ""))
            if key in seen_key:
                continue
            seen_key.add(key)
            # Enriquecer provincia/ciudad desde el cache de geocoding (PROVINCIA,
            # CIUDAD, CALLE indexadas por (lat,lon) redondeado). Si el punto ya
            # trae provincia_punto desde el motor, la conservamos.
            prov_cache, ciudad_cache, calle_cache = "", "", ""
            if lat_v and lon_v:
                # Recortar antes del lookup: maneja coords fuera de rango (Excel sin decimal)
                lat_geo = normalizar_coord_geografica(lat_v, tipo="lat") or lat_v
                lon_geo = normalizar_coord_geografica(lon_v, tipo="lon") or lon_v
                key_coord = (round(lat_geo, 6), round(lon_geo, 6))
                try:
                    prov_cache, ciudad_cache, calle_cache = _geocode_cache.get(key_coord, ("", "", ""))
                except Exception:
                    prov_cache, ciudad_cache, calle_cache = "", "", ""
            # `provincia_punto` es la clave interna en mayúsculas. Se pasa por
            # `provincia_display` para que la hoja de pendientes guarde el mismo
            # nombre que Horarios_Detalle: al reinsertar una visita, ese texto
            # viaja a la columna PROVINCIA y, sin esto, la misma provincia salía
            # dos veces en los filtros.
            provincia_final = provincia_display(
                p.get("provincia_punto") or p.get("provincia") or prov_cache or ""
            )
            ciudad_final = p.get("ciudad_punto") or p.get("ciudad") or ciudad_cache or ""
            calle_final = p.get("calle_punto") or p.get("calle") or calle_cache or ""
            frec_mes = lookup_frecuencia_mes(
                freq_idx,
                desc,
                lat_v if lat_v else None,
                lon_v if lon_v else None,
                explicit=p.get("frecuencia_mes"),
            )
            fila_pend = {
                "Descripción": desc,
                "Latitud": lat_v if lat_v != 0 else None,
                "Longitud": lon_v if lon_v != 0 else None,
                "Semana": semana_str,
                "Tiempo Servicio (min)": p.get("tiempo", 0),
                "Provincia": provincia_final,
                "Ciudad": ciudad_final,
                "Calle": calle_final,
                "Cadena": p.get("cadena_punto") or p.get("cadena") or "",
                "Canal": p.get("canal_punto") or p.get("canal") or "",
                "Mercadista origen": p.get("mercadista_origen", ""),
                "Día origen": p.get("dia_origen", ""),
                "Motivo": p.get("motivo", ""),
            }
            if frec_mes is not None:
                fila_pend["Frecuencia mes"] = frec_mes
            pendientes_data.append(fila_pend)
        pendientes_df = pd.DataFrame(pendientes_data) if pendientes_data else pd.DataFrame(
            columns=[
                "Descripción", "Latitud", "Longitud", "Semana",
                "Tiempo Servicio (min)", "Provincia", "Ciudad", "Calle",
                "Frecuencia mes", "Cadena", "Canal",
                "Mercadista origen", "Día origen", "Motivo",
            ]
        )
        # Garantizar orden de columnas para Pendientes_Sin_Asignar
        pend_col_order = [
            "Descripción", "Latitud", "Longitud", "Semana",
            "Tiempo Servicio (min)", "Provincia", "Ciudad", "Calle",
            "Frecuencia mes", "Cadena", "Canal",
            "Mercadista origen", "Día origen", "Motivo",
        ]
        pend_cols_present = [c for c in pend_col_order if c in pendientes_df.columns] + [
            c for c in pendientes_df.columns if c not in pend_col_order
        ]
        pendientes_df = pendientes_df[pend_cols_present]
        pendientes_df.to_excel(writer, sheet_name="Pendientes_Sin_Asignar", index=False)
        if pendientes_data:
            print(
                f"      [!] {len(pendientes_data)} visita(s) quedaron sin asignar "
                "(ver hoja 'Pendientes_Sin_Asignar').",
                file=__import__("sys").stderr,
                flush=True,
            )

        # Hoja Puntos_Sin_Coordenadas: puntos con lat/lon = 0 o coordenadas invalidas
        print("      -> Escribiendo hoja 'Puntos_Sin_Coordenadas'...")
        sin_coord_lista = getattr(state, "puntos_sin_coordenadas", [])
        sin_coord_data = []
        seen_sin_coord: set = set()
        freq_idx_sc = index_frecuencia_mes_por_punto(df) if df is not None else {}
        for p in sin_coord_lista:
            desc_sc = str(p.get("descripcion", "")).strip()
            lat_sc = p.get("lat", 0)
            lon_sc = p.get("lon", 0)
            key_sc = (desc_sc, lat_sc, lon_sc)
            if key_sc in seen_sin_coord:
                continue
            seen_sin_coord.add(key_sc)
            frec_sc = lookup_frecuencia_mes(
                freq_idx_sc, desc_sc, lat_sc, lon_sc,
                explicit=p.get("frecuencia_mes"),
            )
            sin_coord_data.append({
                "Descripción": desc_sc,
                "Latitud": lat_sc,
                "Longitud": lon_sc,
                "Semana": "ninguna",
                "Tiempo Servicio (min)": p.get("tiempo", 0),
                "Provincia": "ninguna",
                "Ciudad": "ninguna",
                "Frecuencia mes": frec_sc if frec_sc is not None else "",
                "Mercadista origen": "ninguno",
                "Día origen": "ninguno",
                "Motivo": p.get("motivo", "coordenadas_cero"),
            })
        sin_coord_df = pd.DataFrame(sin_coord_data) if sin_coord_data else pd.DataFrame(
            columns=[
                "Descripción", "Latitud", "Longitud", "Semana",
                "Tiempo Servicio (min)", "Provincia", "Ciudad",
                "Frecuencia mes", "Mercadista origen", "Día origen", "Motivo",
            ]
        )
        sin_coord_df.to_excel(writer, sheet_name="Puntos_Sin_Coordenadas", index=False)
        if sin_coord_data:
            print(
                f"      [!] {len(sin_coord_data)} punto(s) sin coordenadas válidas "
                "(ver hoja 'Puntos_Sin_Coordenadas').",
                file=__import__("sys").stderr,
                flush=True,
            )

        # Hoja Config_Procesamiento: con qué modelo de jornada se generó este
        # archivo. Sin ella, el dashboard mediría la ocupación con el modelo
        # activo en el servidor, que puede no ser el de este Excel.
        print(f"      -> Escribiendo hoja '{SHEET_CONFIG_PROCESAMIENTO}'...")
        build_config_procesamiento_df(
            incluye_viaje=jornada_incluye_viaje(),
            tipo_ruta=getattr(state, "tipo_ruta", ""),
            tipo_carga=getattr(state, "tipo_carga", "zona"),
            canal=getattr(state, "canal", ""),
            cadenas=getattr(state, "cadenas", None),
            grupos_cadenas=getattr(state, "grupos_cadenas", None),
            max_dia_minutos=cuota_dia(),
            max_mes_minutos=cuota_mes(),
            cuadrilla_fin_semana=len(mercadistas_fin_semana()),
            hora_fin_minutos=hora_fin_jornada(),
        ).to_excel(writer, sheet_name=SHEET_CONFIG_PROCESAMIENTO, index=False)

    return horarios_df
