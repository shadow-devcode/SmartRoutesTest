"""
Lógica de los endpoints `/api/comparativa/*`.

Lee del archivo comparativa (mismo esquema que Horarios_Detalle, sin filtros
por rol USER y sin la fila TOTAL al contar mercadistas).
"""
from __future__ import annotations

import io
import os
import sys
from typing import Optional

import pandas as pd

from route_engine.mapbox import provincia_display
from utils.access_scope import df_filtrar_mercadista_usuario
from utils.excel_cache import read_excel_cached
from utils.logging import log_endpoint_error, safe_error_message
from utils.horarios_validation import horarios_corruption_message, horarios_missing_columns
from utils.dataset_config import cuota_dia_del_dataset, incluye_viaje_del_dataset
from utils.ubicacion_dto import fila_a_ubicacion, jornada_dto, stats_vacios
from utils.uploads import COMPARATIVA_FILE, UnsafeExcelError, validar_xlsx_no_es_zip_bomb


def _texto(valor) -> str:
    """Texto de una celda; vacío si viene None o NaN."""
    if valor is None or (isinstance(valor, float) and valor != valor):
        return ""
    return str(valor).strip()


def _sin_nan(obj):
    """Quita los NaN de una respuesta: no son JSON válido y el navegador
    descarta la respuesta entera (el desglose del mercaderista no se pintaba).
    Una columna toda vacía, como CALLE en las plantillas, se lee como NaN y
    `df.where(notna, None)` no la limpia porque pandas la tipa como numérica."""
    if isinstance(obj, float) and obj != obj:
        return None
    if isinstance(obj, dict):
        return {k: _sin_nan(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sin_nan(v) for v in obj]
    return obj


# ---------------------------------------------------------------------------
# Upload directo de comparativa (sin reprocesamiento)
# ---------------------------------------------------------------------------

class ComparativaUploadError(Exception):
    """Error de validación del Excel de comparativa subido."""

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def guardar_comparativa(contenido: bytes, *, auth_loaded: bool) -> None:
    """
    Valida que el Excel traiga hoja Horarios_Detalle con columnas mínimas y
    lo guarda como archivo comparativo del dataset activo (o como archivo
    legacy si auth no está cargado).
    """
    try:
        validar_xlsx_no_es_zip_bomb(io.BytesIO(contenido))
    except UnsafeExcelError as exc:
        log_endpoint_error("guardar_comparativa:zip_bomb_check", exc)
        raise ComparativaUploadError(str(exc)) from exc

    try:
        xl = pd.ExcelFile(io.BytesIO(contenido))
        sheet_names = xl.sheet_names
        xl.close()
    except Exception as exc:
        log_endpoint_error("guardar_comparativa:abrir_excel", exc)
        raise ComparativaUploadError(
            f"El archivo no es un Excel válido: {safe_error_message(exc, generic='formato no reconocido')}"
        ) from exc

    if "Horarios_Detalle" not in sheet_names:
        raise ComparativaUploadError(
            f"El archivo no contiene la hoja 'Horarios_Detalle'. "
            f"Hojas encontradas: {', '.join(sheet_names)}. "
            "Asegúrate de subir un Excel ya procesado."
        )

    try:
        df_header = pd.read_excel(
            io.BytesIO(contenido), sheet_name="Horarios_Detalle", nrows=0
        )
    except Exception as exc:
        log_endpoint_error("guardar_comparativa:leer_horarios_detalle", exc)
        raise ComparativaUploadError(
            f"No se pudo leer la hoja 'Horarios_Detalle': "
            f"{safe_error_message(exc, generic='formato de hoja inválido')}"
        ) from exc

    missing_cols = horarios_missing_columns(df_header)
    if missing_cols:
        cols_str = ", ".join(f"'{c}'" for c in missing_cols)
        raise ComparativaUploadError(
            f"El archivo de comparativa no contiene la(s) columna(s) requerida(s): "
            f"{cols_str}. Sube un Excel generado correctamente por el sistema "
            "(debe traer las columnas Mercadista, Día y Fecha en la hoja "
            "'Horarios_Detalle')."
        )

    dest_path = os.path.abspath(COMPARATIVA_FILE)
    if auth_loaded:
        from database.connection import SessionLocal
        from services.route_dataset_service import RouteDatasetService

        s_up = SessionLocal()
        try:
            dest_path = RouteDatasetService(s_up).ensure_comparativa_path_for_active_write()
        finally:
            s_up.close()

    try:
        parent = os.path.dirname(dest_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(dest_path, "wb") as destino:
            destino.write(contenido)
    except OSError as exc:
        log_endpoint_error("guardar_comparativa:escribir_archivo", exc)
        raise ComparativaUploadError(
            f"No se pudo guardar el archivo: {safe_error_message(exc, generic='error de E/S en el servidor')}",
            status_code=500,
        ) from exc

    # Guardar blob de comparativa en BD para persistencia
    if auth_loaded:
        try:
            from database.connection import SessionLocal
            from services.route_dataset_service import RouteDatasetService

            sess_cmp = SessionLocal()
            try:
                svc_cmp = RouteDatasetService(sess_cmp)
                active_cmp = svc_cmp.repo.get_active()
                if active_cmp:
                    active_cmp.comparativa_blob = contenido
                    sess_cmp.commit()
            finally:
                sess_cmp.close()
        except Exception:
            # No interrumpir si falla el blob; el archivo ya está en disco
            pass


# ---------------------------------------------------------------------------
# Consultas (mismo esquema que mercadistas_query_service pero sin filtros USER)
# ---------------------------------------------------------------------------


def listar_mercadistas(cp: str, *, auth_loaded: bool = False) -> dict:
    df_horarios = read_excel_cached(cp, "Horarios_Detalle")
    df_horarios = df_horarios.where(pd.notna(df_horarios), None)

    missing = horarios_missing_columns(df_horarios)
    if missing:
        print(
            f"[WARN] comparativa.listar_mercadistas: Excel '{cp}' sin columnas {missing}; devolviendo lista vacía.",
            file=sys.stderr,
            flush=True,
        )
        return {
            "success": True,
            "mercadistas": [],
            "total": 0,
            "warning": horarios_corruption_message(missing, comparativa=True),
        }

    df_horarios = df_filtrar_mercadista_usuario(df_horarios, auth_loaded=auth_loaded)

    mercadistas = [
        m for m in df_horarios["Mercadista"].unique().tolist()
        if str(m).strip().upper() != "TOTAL"
    ]
    return {"success": True, "mercadistas": mercadistas, "total": len(mercadistas)}


def detalle_mercadista(cp: str, mercadista_name: str, semana: str, *, auth_loaded: bool = False) -> Optional[dict]:
    df_horarios = read_excel_cached(cp, "Horarios_Detalle")
    df_horarios = df_horarios.where(pd.notna(df_horarios), None)
    df_horarios = df_filtrar_mercadista_usuario(df_horarios, auth_loaded=auth_loaded)

    nombre_norm = str(mercadista_name).strip()
    df_merc = df_horarios[df_horarios["Mercadista"].astype(str).str.strip() == nombre_norm]
    if df_merc.empty:
        return None

    if semana and "Fecha" in df_merc.columns:
        df_merc = df_merc[df_merc["Fecha"].astype(str).str.strip() == semana.strip()]

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
                "provincia": provincia_display(row.get("PROVINCIA", "")),
                "ciudad": _texto(row.get("CIUDAD")),
                "calle": _texto(row.get("CALLE")),
                "tiempo_servicio": row["Tiempo Servicio (min)"],
                "duracion": row["Duración (hh:mm)"],
                "tiempo_entre_sucursal": row.get("Tiempo entre sucursal (min)", 0),
                "km_entre_sucursales": row.get("kilometros entre sucurlas (km)", 0),
                "horario": row["Horario"],
                "fecha": str(fecha_val).strip() if fecha_val is not None else "",
            }
        )

    return _sin_nan({
        "success": True,
        "mercadista": mercadista_name,
        "dias": dias,
        "semana_filtro": semana or None,
    })


def todas_ubicaciones(cp: str, semana: str, *, auth_loaded: bool = False) -> dict:
    df_horarios = read_excel_cached(cp, "Horarios_Detalle")
    df_horarios = df_horarios.where(pd.notna(df_horarios), None)

    missing = horarios_missing_columns(df_horarios)
    if missing:
        print(
            f"[WARN] comparativa.todas_ubicaciones: Excel '{cp}' sin columnas {missing}; devolviendo lista vacía.",
            file=sys.stderr,
            flush=True,
        )
        return {
            "success": True,
            "ubicaciones": [],
            "total": 0,
            "semana_filtro": None,
            "warning": horarios_corruption_message(missing, comparativa=True),
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


def ubicaciones_por_dia(cp: str, dia: str, semana: str, *, auth_loaded: bool = False) -> dict:
    df_horarios = read_excel_cached(cp, "Horarios_Detalle")
    df_horarios = df_horarios.where(pd.notna(df_horarios), None)
    df_horarios = df_filtrar_mercadista_usuario(df_horarios, auth_loaded=auth_loaded)

    dia_norm = str(dia).strip()
    df_dia = df_horarios[df_horarios["Día"].astype(str).str.strip() == dia_norm]
    if semana and "Fecha" in df_dia.columns:
        df_dia = df_dia[df_dia["Fecha"].astype(str).str.strip() == semana.strip()]

    ubicaciones: list[dict] = []
    for _, row in df_dia.iterrows():
        item = fila_a_ubicacion(row, incluir_semana=False)
        if item is not None:
            ubicaciones.append(item)

    return {
        "success": True,
        "dia": dia,
        "ubicaciones": ubicaciones,
        "total": len(ubicaciones),
    }


def stats(cp: str, *, auth_loaded: bool = False) -> dict:
    df = read_excel_cached(cp, "Horarios_Detalle")

    missing = horarios_missing_columns(df)
    if missing:
        print(
            f"[WARN] comparativa.stats: Excel '{cp}' sin columnas {missing}; devolviendo stats en cero.",
            file=sys.stderr,
            flush=True,
        )
        return {
            "success": True,
            "stats": stats_vacios(),
            "warning": horarios_corruption_message(missing, comparativa=True),
        }

    df = df_filtrar_mercadista_usuario(df, auth_loaded=auth_loaded)
    if df.empty:
        return {"success": True, "stats": stats_vacios()}

    df["Tiempo Servicio (min)"] = pd.to_numeric(
        df.get("Tiempo Servicio (min)", 0), errors="coerce"
    ).fillna(0)
    df["Tiempo entre sucursal (min)"] = pd.to_numeric(
        df.get("Tiempo entre sucursal (min)", 0), errors="coerce"
    ).fillna(0)
    df["kilometros entre sucurlas (km)"] = pd.to_numeric(
        df.get("kilometros entre sucurlas (km)", 0), errors="coerce"
    ).fillna(0)

    # tiempo_promedio_servicio = desplazamiento promedio (min) entre sucursales.
    return {
        "success": True,
        "stats": {
            "total_mercadistas": int(df["Mercadista"].nunique()),
            "total_ubicaciones": int(len(df)),
            "total_por_dia": df.groupby("Día").size().to_dict(),
            "tiempo_promedio_servicio": round(
                float(df["Tiempo entre sucursal (min)"].mean()), 2
            ),
            "total_tiempo_trabajo_min": int(df["Tiempo Servicio (min)"].sum()),
            "total_tiempo_entre_sucursales_min": float(
                df["Tiempo entre sucursal (min)"].sum()
            ),
            "total_km_entre_sucursales": float(
                round(df["kilometros entre sucurlas (km)"].sum(), 3)
            ),
            "jornada": jornada_dto(
                cuota_dia_del_dataset(cp),
                incluye_desplazamiento=incluye_viaje_del_dataset(cp),
            ),
        },
    }
