"""
Lógica de consulta de mercadistas, ubicaciones, día y stats.

Cada función devuelve un dict serializable a JSON (con `success: True` y los
datos), o eleva una excepción si los datos están incompletos. Los controllers
se encargan únicamente del HTTP.
"""
from __future__ import annotations

import sys
from typing import Optional

import pandas as pd

from utils.access_scope import df_filtrar_mercadista_usuario
from utils.excel_cache import read_excel_cached
from utils.horarios_validation import horarios_corruption_message, horarios_missing_columns
from utils.dataset_config import cuota_dia_del_dataset, incluye_viaje_del_dataset
from utils.ubicacion_dto import fila_a_ubicacion, jornada_dto, stats_vacios


def listar_mercadistas(hp: str, *, auth_loaded: bool) -> dict:
    df_horarios = read_excel_cached(hp, "Horarios_Detalle")
    df_horarios = df_horarios.where(pd.notna(df_horarios), None)

    missing = horarios_missing_columns(df_horarios)
    if missing:
        print(
            f"[WARN] listar_mercadistas: Excel '{hp}' sin columnas {missing}; devolviendo lista vacía.",
            file=sys.stderr,
            flush=True,
        )
        return {
            "success": True,
            "mercadistas": [],
            "total": 0,
            "provincias": [],
            "mercadista_provincias": {},
            "warning": horarios_corruption_message(missing),
        }

    df_horarios = df_filtrar_mercadista_usuario(df_horarios, auth_loaded=auth_loaded)

    mercadistas = [
        m
        for m in df_horarios["Mercadista"].unique().tolist()
        if str(m).strip().upper() != "TOTAL"
    ]

    mercadista_provincias: dict[str, list[str]] = {}
    provincias: list[str] = []
    if "PROVINCIA" in df_horarios.columns:
        df_p = df_horarios[
            df_horarios["Mercadista"].astype(str).str.strip().str.upper() != "TOTAL"
        ]
        for m in mercadistas:
            m_norm = str(m).strip()
            col = df_p[df_p["Mercadista"].astype(str).str.strip() == m_norm]["PROVINCIA"]
            vals = col.dropna().astype(str).str.strip()
            mercadista_provincias[m] = sorted({v for v in vals if v})
        provincias = sorted({p for lst in mercadista_provincias.values() for p in lst})
    else:
        mercadista_provincias = {m: [] for m in mercadistas}

    return {
        "success": True,
        "mercadistas": mercadistas,
        "total": len(mercadistas),
        "provincias": provincias,
        "mercadista_provincias": mercadista_provincias,
    }


def detalle_mercadista(
    hp: str,
    mercadista_name: str,
    semana: str,
    *,
    auth_loaded: bool,
) -> Optional[dict]:
    """Devuelve detalles del mercadista o None si no existe."""
    df_horarios = read_excel_cached(hp, "Horarios_Detalle")
    df_horarios = df_horarios.where(pd.notna(df_horarios), None)
    df_horarios = df_filtrar_mercadista_usuario(df_horarios, auth_loaded=auth_loaded)

    nombre_norm = str(mercadista_name).strip()
    df_merc = df_horarios[df_horarios["Mercadista"].astype(str).str.strip() == nombre_norm]
    if df_merc.empty:
        return None

    if semana and "Fecha" in df_merc.columns:
        df_merc = df_merc[df_merc["Fecha"].astype(str).str.strip() == semana]

    datos = df_merc.to_dict("records")
    dias: dict[str, list[dict]] = {}
    for row in datos:
        dia = row["Día"]
        if dia not in dias:
            dias[dia] = []
        fecha_val = row.get("Fecha") if "Fecha" in row else None
        dias[dia].append(
            {
                "orden": row["Orden Ruta"],
                "descripcion": row["Descripción"],
                "latitud": row["Latitud"],
                "longitud": row["Longitud"],
                "provincia": row.get("PROVINCIA", ""),
                "ciudad": row.get("CIUDAD", ""),
                "calle": row.get("CALLE", ""),
                "tiempo_servicio": row["Tiempo Servicio (min)"],
                "duracion": row["Duración (hh:mm)"],
                "tiempo_entre_sucursal": row.get("Tiempo entre sucursal (min)", 0),
                "km_entre_sucursales": row.get("kilometros entre sucurlas (km)", 0),
                "horario": row["Horario"],
                "fecha": str(fecha_val).strip() if fecha_val is not None else "",
            }
        )

    return {
        "success": True,
        "mercadista": mercadista_name,
        "dias": dias,
        "semana_filtro": semana or None,
    }


def todas_ubicaciones(hp: str, semana: str, *, auth_loaded: bool) -> dict:
    df_horarios = read_excel_cached(hp, "Horarios_Detalle")
    df_horarios = df_horarios.where(pd.notna(df_horarios), None)

    missing = horarios_missing_columns(df_horarios)
    if missing:
        print(
            f"[WARN] todas_ubicaciones: Excel '{hp}' sin columnas {missing}; devolviendo lista vacía.",
            file=sys.stderr,
            flush=True,
        )
        return {
            "success": True,
            "ubicaciones": [],
            "total": 0,
            "semana_filtro": None,
            "warning": horarios_corruption_message(missing),
        }

    df_horarios = df_filtrar_mercadista_usuario(df_horarios, auth_loaded=auth_loaded)

    if semana and "Fecha" in df_horarios.columns:
        df_horarios = df_horarios[df_horarios["Fecha"].astype(str).str.strip() == semana]

    ubicaciones: list[dict] = []
    for _, row in df_horarios.iterrows():
        item = fila_a_ubicacion(row, incluir_semana=True)
        if item is not None:
            ubicaciones.append(item)

    return {
        "success": True,
        "ubicaciones": ubicaciones,
        "total": len(ubicaciones),
        "semana_filtro": semana or None,
    }


def ubicaciones_por_dia(hp: str, dia: str, semana: str, *, auth_loaded: bool) -> dict:
    df_horarios = read_excel_cached(hp, "Horarios_Detalle")
    df_horarios = df_horarios.where(pd.notna(df_horarios), None)
    df_horarios = df_filtrar_mercadista_usuario(df_horarios, auth_loaded=auth_loaded)

    dia_norm = str(dia).strip()
    df_dia = df_horarios[df_horarios["Día"].astype(str).str.strip() == dia_norm]
    if semana and "Fecha" in df_dia.columns:
        df_dia = df_dia[df_dia["Fecha"].astype(str).str.strip() == semana.strip()]

    ubicaciones: list[dict] = []
    for _, row in df_dia.iterrows():
        item = fila_a_ubicacion(row, incluir_semana=True)
        if item is not None:
            ubicaciones.append(item)

    return {
        "success": True,
        "dia": dia,
        "ubicaciones": ubicaciones,
        "total": len(ubicaciones),
        "semana_filtro": semana or None,
    }


def stats(hp: str, *, auth_loaded: bool) -> dict:
    df_horarios = read_excel_cached(hp, "Horarios_Detalle")

    missing = horarios_missing_columns(df_horarios)
    if missing:
        print(
            f"[WARN] stats: Excel '{hp}' sin columnas {missing}; devolviendo stats en cero.",
            file=sys.stderr,
            flush=True,
        )
        return {
            "success": True,
            "stats": stats_vacios(),
            "warning": horarios_corruption_message(missing),
        }

    df_horarios = df_filtrar_mercadista_usuario(df_horarios, auth_loaded=auth_loaded)
    if df_horarios.empty:
        return {"success": True, "stats": stats_vacios()}

    df_stats = df_horarios[
        df_horarios["Mercadista"].astype(str).str.strip().str.upper() != "TOTAL"
    ]
    if df_stats.empty:
        df_stats = df_horarios

    df_horarios["Tiempo Servicio (min)"] = pd.to_numeric(
        df_horarios.get("Tiempo Servicio (min)", 0), errors="coerce"
    ).fillna(0)
    df_horarios["Tiempo entre sucursal (min)"] = pd.to_numeric(
        df_horarios.get("Tiempo entre sucursal (min)", 0), errors="coerce"
    ).fillna(0)
    df_horarios["kilometros entre sucurlas (km)"] = pd.to_numeric(
        df_horarios.get("kilometros entre sucurlas (km)", 0), errors="coerce"
    ).fillna(0)

    # tiempo_promedio_servicio = DESPLAZAMIENTO PROMEDIO por visita (promedio de
    # "Tiempo entre sucursal (min)"). La tarjeta del dashboard "Desplazamiento
    # Promedio" refleja realmente el desplazamiento, no el servicio.
    return {
        "success": True,
        "stats": {
            "total_mercadistas": int(df_stats["Mercadista"].nunique()),
            "total_ubicaciones": int(len(df_horarios)),
            "total_por_dia": df_horarios.groupby("Día").size().to_dict(),
            "tiempo_promedio_servicio": round(
                float(df_horarios["Tiempo entre sucursal (min)"].mean()), 2
            ),
            "total_tiempo_trabajo_min": int(df_horarios["Tiempo Servicio (min)"].sum()),
            "total_tiempo_entre_sucursales_min": float(
                df_horarios["Tiempo entre sucursal (min)"].sum()
            ),
            "total_km_entre_sucursales": float(
                round(df_horarios["kilometros entre sucurlas (km)"].sum(), 3)
            ),
            "jornada": jornada_dto(
                cuota_dia_del_dataset(hp),
                incluye_desplazamiento=incluye_viaje_del_dataset(hp),
            ),
        },
    }


def categorias(hp: str) -> dict:
    df_categorias = read_excel_cached(hp, "Categorias")
    df_categorias = df_categorias.where(pd.notna(df_categorias), None)

    rows = [
        {
            "id": row.get("ID"),
            "descripcion": row.get("DESCRIPCION", ""),
            "categoria": row.get("CATEGORIA", "DESCONOCIDO"),
            "provincia": row.get("PROVINCIA", ""),
            "ciudad": row.get("CIUDAD", ""),
            "calle": row.get("CALLE", ""),
        }
        for _, row in df_categorias.iterrows()
    ]

    return {"success": True, "categorias": rows, "total": len(rows)}


def resumen_rutas(
    hp: str, *, auth_loaded: bool, mercadista: str = "", provincia: str = "", ciudad: str = ""
) -> dict:
    """
    Rutas asignadas AGRUPADAS POR PUNTO DE VENTA.

    Es la contraparte de `resumen_pendientes`: allí se ven los puntos a los que
    les falta trabajo, aquí los que ya lo tienen colocado. Se agrupa por punto
    porque la unidad de gestión es la tienda —quién la atiende, qué días y
    cuántas de sus visitas del mes están puestas—, no cada visita suelta: un
    punto de frecuencia 20 son veinte filas idénticas en Horarios_Detalle.

    `visitas_pendientes` se cruza con la hoja de pendientes para que se vea de
    un vistazo si al punto le falta algo, sin tener que ir a la otra pantalla.
    """
    df_horarios = read_excel_cached(hp, "Horarios_Detalle")

    missing = horarios_missing_columns(df_horarios)
    if missing:
        return {
            "success": True,
            "filas": [],
            "total_filas": 0,
            "puntos": [],
            "total_puntos": 0,
            "total_visitas": 0,
            "minutos_asignados": 0.0,
            "mercadistas": [],
            "warning": horarios_corruption_message(missing),
        }

    df_horarios = df_filtrar_mercadista_usuario(df_horarios, auth_loaded=auth_loaded)
    df_horarios = df_horarios[
        df_horarios["Mercadista"].astype(str).str.strip().str.upper() != "TOTAL"
    ]

    from route_engine.config import ORDEN_SEMANA
    from utils.route_helpers import clave_ub

    puntos: dict = {}
    for _, row in df_horarios.iterrows():
        desc = str(row.get("Descripción") or "").strip()
        if not desc:
            continue
        clave = clave_ub(desc, row.get("Latitud"), row.get("Longitud"))
        item = puntos.get(clave)
        if item is None:
            try:
                lat_f = float(row.get("Latitud"))
                lon_f = float(row.get("Longitud"))
            except (TypeError, ValueError):
                lat_f, lon_f = None, None
            item = {
                "id": clave,
                "descripcion": desc,
                "latitud": lat_f,
                "longitud": lon_f,
                "provincia": str(row.get("PROVINCIA") or "").strip(),
                "ciudad": str(row.get("CIUDAD") or "").strip(),
                "mercadista": str(row.get("Mercadista") or "").strip(),
                "tiempo_servicio": 0.0,
                "dias_visita": set(),
                "semanas": set(),
                "visitas_agendadas": 0,
                "visitas_pendientes": 0,
                "minutos_mes": 0.0,
            }
            puntos[clave] = item

        item["visitas_agendadas"] += 1
        try:
            t = float(row.get("Tiempo Servicio (min)") or 0)
        except (TypeError, ValueError):
            t = 0.0
        item["minutos_mes"] += t
        item["tiempo_servicio"] = max(item["tiempo_servicio"], t)
        dia = str(row.get("Día") or "").strip()
        if dia:
            item["dias_visita"].add(dia)
        semana = str(row.get("Fecha") or "").strip()
        if semana:
            item["semanas"].add(semana)

    # Pendientes del mismo punto, para ver la cobertura sin cambiar de pantalla.
    try:
        from services.pendientes_service import leer_hoja_pendientes

        df_pend = leer_hoja_pendientes(hp)
        for _, row in df_pend.iterrows():
            clave = clave_ub(row.get("Descripción"), row.get("Latitud"), row.get("Longitud"))
            if clave in puntos:
                puntos[clave]["visitas_pendientes"] += 1
    except Exception:
        pass

    def _ordenar_dias(dias) -> list:
        return [d for d in ORDEN_SEMANA if d in dias] + sorted(
            d for d in dias if d not in ORDEN_SEMANA
        )

    lista = []
    for item in puntos.values():
        item["dias_visita"] = _ordenar_dias(item["dias_visita"])
        item["semanas"] = sorted(item["semanas"])
        item["frecuencia_mes"] = item["visitas_agendadas"] + item["visitas_pendientes"]
        item["minutos_mes"] = round(item["minutos_mes"], 1)
        lista.append(item)

    if mercadista:
        lista = [x for x in lista if x["mercadista"].strip().lower() == mercadista.strip().lower()]
    if provincia:
        lista = [x for x in lista if x["provincia"].strip().lower() == provincia.strip().lower()]
    if ciudad:
        lista = [x for x in lista if x["ciudad"].strip().lower() == ciudad.strip().lower()]

    lista.sort(key=lambda x: (x["mercadista"], x["descripcion"]))
    mercadistas = sorted({x["mercadista"] for x in lista if x["mercadista"]})

    # Filas tal cual salen del Excel (hoja Horarios_Detalle): mismo orden y
    # mismos nombres de columna. La pantalla de gestión muestra esto para que lo
    # que se ve en la app y lo que se descarga sean lo mismo; los `puntos`
    # agrupados de arriba se siguen usando para pintar el mapa (un marcador por
    # punto, no uno por visita).
    claves_visibles = {x["id"] for x in lista}
    filas = []
    for _, row in df_horarios.iterrows():
        desc = str(row.get("Descripción") or "").strip()
        if not desc:
            continue
        if clave_ub(desc, row.get("Latitud"), row.get("Longitud")) not in claves_visibles:
            continue
        filas.append(
            {
                "mercadista": str(row.get("Mercadista") or "").strip(),
                "dia": str(row.get("Día") or "").strip(),
                "orden_ruta": _entero(row.get("Orden Ruta")),
                "descripcion": desc,
                # Coordenadas en cada fila: el mapa de la pantalla agrupa a
                # partir de las filas YA filtradas, de modo que los filtros por
                # columna valen igual para la tabla y para el mapa.
                "latitud": _coord(row.get("Latitud")),
                "longitud": _coord(row.get("Longitud")),
                # Canal y cadena solo existen en los Excels generados a partir
                # de un archivo que los traía; en los antiguos van vacíos.
                "canal": str(row.get("CANAL") or "").strip(),
                "cadena": str(row.get("CADENA") or "").strip(),
                "provincia": str(row.get("PROVINCIA") or "").strip(),
                "ciudad": str(row.get("CIUDAD") or "").strip(),
                "calle": str(row.get("CALLE") or "").strip(),
                "tiempo_servicio": _numero(row.get("Tiempo Servicio (min)")),
                "duracion": str(row.get("Duración (hh:mm)") or "").strip(),
                "tiempo_entre_sucursal": _numero(row.get("Tiempo entre sucursal (min)")),
                "km_entre_sucursales": _numero(row.get("kilometros entre sucurlas (km)")),
                "horario": str(row.get("Horario") or "").strip(),
                "fecha": str(row.get("Fecha") or "").strip(),
            }
        )

    return {
        "success": True,
        "filas": filas,
        "total_filas": len(filas),
        "puntos": lista,
        "total_puntos": len(lista),
        "total_visitas": sum(x["visitas_agendadas"] for x in lista),
        "minutos_asignados": round(sum(x["minutos_mes"] for x in lista), 1),
        "mercadistas": mercadistas,
    }


def _numero(valor) -> float:
    try:
        v = float(valor)
        return 0.0 if pd.isna(v) else round(v, 2)
    except (TypeError, ValueError):
        return 0.0


def _coord(valor):
    """Coordenada sin redondear: `_numero` recorta a 2 decimales y movería el punto."""
    try:
        v = float(valor)
        return None if pd.isna(v) or v == 0 else v
    except (TypeError, ValueError):
        return None


def _entero(valor) -> int:
    try:
        v = float(valor)
        return 0 if pd.isna(v) else int(v)
    except (TypeError, ValueError):
        return 0
