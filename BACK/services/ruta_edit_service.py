"""
Edición interactiva de rutas:
  - Reordenar visitas dentro de un día
  - Mover una visita a otro día/semana del MISMO mercadista
  - Mover visita(s) a la hoja Pendientes_Sin_Asignar
  - Asignar una visita pendiente a una ruta destino

Toda escritura al Excel pasa por _guardar_excel_horarios_pendientes (o
ExcelWriter directo) para conservar formato y la hoja de pendientes.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from route_engine.excel_writer import (
    _recalcular_ruta,
    drop_spurious_total_rows_horarios_df,
    format_horarios_detalle_worksheet,
)
from route_engine.geo import parse_coordenada_a_float
from route_engine.mapbox import provincia_display
from services.pendientes_service import (
    PENDIENTES_COLS,
    PENDIENTES_SHEET,
    index_frecuencia_mes_desde_excel,
    leer_hoja_pendientes,
    resolver_excel_maestro_frecuencia,
)
from utils.excel_atomic import escritura_atomica
from utils.dataset_config import cuota_dia_del_dataset, incluye_viaje_del_dataset
from utils.excel_cache import (
    actualizar_excel_cache,
    invalidate_excel_cache,
    read_excel_cached,
)
from utils.excel_libro import cargar_libro, registrar_libro
from utils.excel_lock import with_excel_file_lock
from utils.route_helpers import a_numero, clave_pendiente, clave_ub


# ---------------------------------------------------------------------------
# Excepciones de negocio específicas de edición de rutas
# ---------------------------------------------------------------------------


class RutaEditError(Exception):
    """Error de edición de ruta. Lleva status_code para el controller."""

    def __init__(self, message: str, status_code: int = 400, payload: dict | None = None):
        self.message = message
        self.status_code = status_code
        self.payload = payload or {}
        super().__init__(message)


# ---------------------------------------------------------------------------
# Helpers privados
# ---------------------------------------------------------------------------


def _mapa_mercadista_por_punto(df_horarios) -> dict:
    m: dict = {}
    if df_horarios is None or df_horarios.empty:
        return m
    for _, row in df_horarios.iterrows():
        k = clave_ub(row.get("Descripción"), row.get("Latitud"), row.get("Longitud"))
        merc = str(row.get("Mercadista", "") if pd.notna(row.get("Mercadista")) else "").strip()
        if merc and k not in m:
            m[k] = merc
    return m


def _validar_mercadista_punto(dest_merc: str, desc, lat, lon, df_horarios):
    """Valida que `dest_merc` sea el mercadista permitido para este punto de venta."""
    obligatorio = _mapa_mercadista_por_punto(df_horarios).get(clave_ub(desc, lat, lon))
    if obligatorio and str(obligatorio).strip() != str(dest_merc or "").strip():
        return (
            False,
            obligatorio,
            (
                f"Este punto de venta ya está asignado a «{obligatorio}». "
                "Todas sus visitas deben permanecer con el mismo mercadista."
            ),
        )
    return True, obligatorio, ""


def _fila_horarios_a_pendiente(row, motivo: str, freq_index: dict | None = None) -> dict:
    """Convierte una fila de Horarios_Detalle al esquema de Pendientes_Sin_Asignar."""
    semana_v = row.get("Fecha", "")
    semana_str = "" if (semana_v is None or pd.isna(semana_v)) else str(semana_v).strip()
    try:
        t_serv = float(row.get("Tiempo Servicio (min)") or 0)
    except (TypeError, ValueError):
        t_serv = 0.0
    prov = row.get("PROVINCIA", "") if "PROVINCIA" in row.index else row.get("Provincia", "")
    prov = provincia_display(prov)
    desc = str(row.get("Descripción", "") or "").strip()
    lat = parse_coordenada_a_float(row.get("Latitud"))
    lon = parse_coordenada_a_float(row.get("Longitud"))
    from route_engine.excel_reader import lookup_frecuencia_mes

    frec_mes = lookup_frecuencia_mes(freq_index or {}, desc, lat, lon)
    ciudad_val = ""
    if "CIUDAD" in row.index:
        v = row.get("CIUDAD")
        ciudad_val = "" if (v is None or pd.isna(v)) else str(v).strip()
    calle_val = ""
    if "CALLE" in row.index:
        v = row.get("CALLE")
        calle_val = "" if (v is None or pd.isna(v)) else str(v).strip()
    fila = {
        "Descripción": desc,
        "Latitud": lat,
        "Longitud": lon,
        "Semana": semana_str,
        "Tiempo Servicio (min)": t_serv,
        "Provincia": prov,
        "Ciudad": ciudad_val,
        "Calle": calle_val,
        "Mercadista origen": str(row.get("Mercadista", "") or "").strip(),
        "Día origen": str(row.get("Día", "") or "").strip(),
        "Motivo": motivo,
    }
    if frec_mes is not None:
        fila["Frecuencia mes"] = frec_mes
    return fila


def _agregar_pendiente(df_pend: pd.DataFrame, pend_dict: dict) -> pd.DataFrame:
    """Añade SIEMPRE una fila a pendientes, sin deduplicar.

    Mover una visita agendada a pendientes es una conversión 1:1: se quita una
    fila de Horarios_Detalle y debe quedar exactamente una fila pendiente que la
    represente. Deduplicar por (punto+semana) haría DESAPARECER la visita cuando
    el punto ya tiene otra pendiente en esa misma semana (p. ej. frecuencia ≥ 2),
    rompiendo la regla «agendadas + pendientes == frecuencia». Cada visita
    pendiente es independiente y se asigna por separado.
    """
    nueva = pd.DataFrame([pend_dict])
    nueva = nueva[[c for c in PENDIENTES_COLS if c in nueva.columns]]
    if df_pend is None or df_pend.empty:
        return nueva
    return pd.concat([df_pend, nueva], ignore_index=True)


def _agregar_pendiente_sin_duplicar(df_pend: pd.DataFrame, pend_dict: dict) -> pd.DataFrame:
    """Añade una fila a pendientes si no existe ya la misma clave (punto+semana)."""
    clave = clave_pendiente(
        pend_dict.get("Descripción"),
        pend_dict.get("Latitud"),
        pend_dict.get("Longitud"),
        pend_dict.get("Semana"),
    )
    if not df_pend.empty:
        for i in df_pend.index:
            row = df_pend.loc[i]
            k = clave_pendiente(
                row.get("Descripción"),
                row.get("Latitud"),
                row.get("Longitud"),
                row.get("Semana"),
            )
            if k == clave:
                return df_pend
    nueva = pd.DataFrame([pend_dict])
    nueva = nueva[[c for c in PENDIENTES_COLS if c in nueva.columns]]
    if df_pend.empty:
        return nueva
    return pd.concat([df_pend, nueva], ignore_index=True)


# Prefijos que Excel/Calc interpretan como inicio de fórmula al abrir el
# archivo (incluye DDE vía "=cmd|..." y fórmulas que arrancan con +/-/@).
_FORMULA_TRIGGER_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _neutralizar_formula(valor):
    """Antepone una comilla simple a strings que Excel podría ejecutar como
    fórmula al abrir el archivo (mitigación estándar de CSV/Excel injection
    para valores que llegan desde el body del request, p.ej. descripción o
    provincia de una visita)."""
    if isinstance(valor, str) and valor.startswith(_FORMULA_TRIGGER_PREFIXES):
        return "'" + valor
    return valor


def _sanitizar_df_para_excel(df: pd.DataFrame) -> pd.DataFrame:
    """Aplica `_neutralizar_formula` a todas las columnas de texto de `df`
    antes de escribirlo a disco, para que ninguna celda quede como fórmula
    ejecutable al abrirse en Excel/LibreOffice."""
    if df is None or df.empty:
        return df
    df = df.copy()
    for col in df.columns:
        if df[col].dtype == object:
            df[col] = df[col].map(_neutralizar_formula)
    return df


def _guardar_horarios_pendientes(hp: str, df_horarios: pd.DataFrame, df_pend: pd.DataFrame) -> None:
    _escribir_hojas(hp, {"Horarios_Detalle": df_horarios, PENDIENTES_SHEET: df_pend})


def _guardar_horarios(hp: str, df_horarios: pd.DataFrame) -> None:
    """Persiste solo Horarios_Detalle, sanitizada contra fórmulas."""
    _escribir_hojas(hp, {"Horarios_Detalle": df_horarios})


def _escribir_hojas(hp: str, hojas: dict) -> None:
    """Vuelca esas hojas en el Excel reutilizando el libro ya cargado.

    Se escribe sobre una copia temporal que sustituye al original de golpe, de
    modo que quien lea mientras tanto nunca vea el .xlsx a medio escribir. El
    libro se reutiliza entre ediciones: abrirlo cuesta 1,67 s en el rutero
    nacional y todas las escrituras pasan por el mismo lock.
    """
    libro = cargar_libro(hp)
    for nombre, df in hojas.items():
        datos = _sanitizar_df_para_excel(df)
        # La hoja se recrea en su MISMA posición: el orden del libro es el que
        # ve quien lo abre, y Horarios_Detalle debe seguir siendo la primera.
        posicion = libro.sheetnames.index(nombre) if nombre in libro.sheetnames else None
        if posicion is not None:
            del libro[nombre]
        hoja = libro.create_sheet(nombre, posicion)
        hoja.append(list(datos.columns))
        for fila in datos.itertuples(index=False, name=None):
            hoja.append(["" if pd.isna(v) else v for v in fila])
        if nombre == "Horarios_Detalle":
            try:
                format_horarios_detalle_worksheet(hoja)
            except Exception:
                pass

    with escritura_atomica(hp) as destino:
        libro.save(destino)
    registrar_libro(hp, libro)
    invalidate_excel_cache(hp)
    for nombre, df in hojas.items():
        actualizar_excel_cache(hp, nombre, df)


def _mask_grupo(df: pd.DataFrame, mercadista: str, dia: str, fecha: str) -> pd.Series:
    """Máscara booleana de las filas del grupo (mercadista, día, semana/fecha)."""
    return (
        (df["Mercadista"].astype(str).str.strip() == mercadista)
        & (df["Día"].astype(str).str.strip() == dia)
        & (df["Fecha"].astype(str).str.strip() == fecha)
    )


def _renumerar_y_recalcular_grupos(
    df: pd.DataFrame, grupos: set, incluye_viaje: bool | None = None
) -> pd.DataFrame:
    """Renumera orden y recalcula tiempos/horarios en los grupos (merc, día, fecha) indicados.

    `incluye_viaje` es el modelo de jornada del Excel que se está editando: si
    se generó "sin tiempo de desplazamiento", el recálculo no puede volver a
    meter el trayecto en el horario.
    """
    if not grupos:
        return df
    for merc, dia, fecha in grupos:
        idxs = df.index[_mask_grupo(df, merc, dia, fecha)].tolist()
        if not idxs:
            continue
        idxs_sorted = sorted(idxs, key=lambda i: df.at[i, "Orden Ruta"])
        for pos, idx in enumerate(idxs_sorted):
            df.at[idx, "Orden Ruta"] = pos + 1
    sort_cols = [c for c in ["Mercadista", "Día", "Fecha", "Orden Ruta"] if c in df.columns]
    if sort_cols:
        df = df.sort_values(by=sort_cols)
    recalc_keys = list(grupos)
    partes = []
    mask_any = pd.Series(False, index=df.index)
    for merc, dia, fecha in recalc_keys:
        m = _mask_grupo(df, merc, dia, fecha)
        if m.any():
            partes.append(_recalcular_ruta(df.loc[m].copy(), incluye_viaje))
            mask_any = mask_any | m
    if partes:
        df_resto = df.loc[~mask_any].copy()
        df = pd.concat([df_resto] + partes, ignore_index=True)
        if sort_cols:
            df = df.sort_values(by=sort_cols).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Casos de uso (cada uno corresponde a un endpoint)
# ---------------------------------------------------------------------------


@with_excel_file_lock("hp")
def actualizar_orden_ruta(
    hp: str,
    *,
    mercadista: str,
    semana: str,
    dia: str,
    ubicaciones: list[dict],
) -> dict:
    """Reordena visitas y recalcula tiempo entre sucursales, km y horarios."""
    df = read_excel_cached(hp, "Horarios_Detalle")
    df = drop_spurious_total_rows_horarios_df(df)
    idx_rows = df.index[_mask_grupo(df, mercadista, dia, semana)].tolist()
    if len(idx_rows) != len(ubicaciones):
        raise RutaEditError(
            f"El número de visitas no coincide "
            f"(Excel: {len(idx_rows)}, enviadas: {len(ubicaciones)})",
            status_code=400,
        )

    orden_por_clave: dict = {}
    for u in ubicaciones:
        k = clave_ub(u.get("descripcion"), u.get("latitud"), u.get("longitud"))
        orden_por_clave[k] = int(u.get("orden", 0))

    for i in idx_rows:
        row = df.loc[i]
        desc = row.get("Descripción")
        lat = row.get("Latitud")
        lon = row.get("Longitud")
        k = clave_ub(desc, lat, lon)
        if k in orden_por_clave:
            df.at[i, "Orden Ruta"] = orden_por_clave[k]

    ordenado_idx = sorted(idx_rows, key=lambda i: df.at[i, "Orden Ruta"])
    grupo = df.loc[ordenado_idx].copy()

    for col in ("Latitud", "Longitud"):
        if col in grupo.columns:
            grupo[col] = grupo[col].apply(a_numero)

    grupo_recalc = _recalcular_ruta(grupo, incluye_viaje_del_dataset(hp))
    col_tiempo = "Tiempo entre sucursal (min)"
    col_km = "kilometros entre sucurlas (km)"
    col_horario = "Horario"
    for pos, idx in enumerate(ordenado_idx):
        df.at[idx, col_tiempo] = grupo_recalc.iloc[pos][col_tiempo]
        df.at[idx, col_km] = grupo_recalc.iloc[pos][col_km]
        df.at[idx, col_horario] = grupo_recalc.iloc[pos][col_horario]

    sort_cols = [c for c in ["Mercadista", "Día", "Fecha", "Orden Ruta"] if c in df.columns]
    if sort_cols:
        df = df.sort_values(by=sort_cols)

    _guardar_horarios(hp, df)

    return {
        "success": True,
        "message": "Orden actualizado; tiempo entre sucursales, km y horarios recalculados.",
    }


@with_excel_file_lock("hp")
def mover_a_pendientes(
    hp: str,
    *,
    semana: str,
    mercadista_origen: str,
    dia_origen: str,
    todas_las_visitas: bool,
    visita: dict,
) -> dict:
    """Quita visita(s) de Horarios_Detalle y las pasa a Pendientes_Sin_Asignar."""
    clave_punto = clave_ub(
        visita.get("descripcion"),
        visita.get("latitud"),
        visita.get("longitud"),
    )

    df = read_excel_cached(hp, "Horarios_Detalle")
    df = drop_spurious_total_rows_horarios_df(df)
    df_pend = leer_hoja_pendientes(hp)

    if todas_las_visitas:
        idx_mover = []
        grupos_afectados: set = set()
        for i in df.index:
            row = df.loc[i]
            if clave_ub(row.get("Descripción"), row.get("Latitud"), row.get("Longitud")) == clave_punto:
                idx_mover.append(i)
                grupos_afectados.add(
                    (
                        str(row.get("Mercadista", "")).strip(),
                        str(row.get("Día", "")).strip(),
                        str(row.get("Fecha", "")).strip(),
                    )
                )
        motivo = "movido a pendientes (todas las visitas del punto para cambiar mercadista)"
    else:
        mask_orig = _mask_grupo(df, mercadista_origen, dia_origen, semana)
        idx_mover = []
        for i in df.index[mask_orig].tolist():
            row = df.loc[i]
            if clave_ub(row.get("Descripción"), row.get("Latitud"), row.get("Longitud")) == clave_punto:
                idx_mover.append(i)
        grupos_afectados = {(mercadista_origen, dia_origen, semana)}
        motivo = "movido a pendientes manualmente"

    if not idx_mover:
        raise RutaEditError(
            "No se encontró la visita en la ruta indicada", status_code=404
        )

    maestro = resolver_excel_maestro_frecuencia()
    freq_index = index_frecuencia_mes_desde_excel(maestro) if maestro else {}

    for i in sorted(idx_mover, reverse=True):
        pend_dict = _fila_horarios_a_pendiente(df.loc[i], motivo, freq_index)
        # Siempre añadir (sin dedup): cada visita movida es independiente y debe
        # quedar registrada, aunque el punto ya tenga otra pendiente esa semana.
        df_pend = _agregar_pendiente(df_pend, pend_dict)
        df = df.drop(i).reset_index(drop=True)

    df = _renumerar_y_recalcular_grupos(
        df, grupos_afectados, incluye_viaje_del_dataset(hp)
    )
    _guardar_horarios_pendientes(hp, df, df_pend)

    n = len(idx_mover)
    return {
        "success": True,
        "message": (
            f"{n} visita(s) del punto movida(s) a pendientes."
            if todas_las_visitas
            else "Visita movida a pendientes."
        ),
        "visitas_movidas": n,
    }


@with_excel_file_lock("hp")
def mover_visita(
    hp: str,
    *,
    semana: str,
    semana_destino: str,
    mercadista_origen: str,
    dia_origen: str,
    mercadista_destino: str,
    dia_destino: str,
    orden_destino: int,
    visita: dict,
) -> dict:
    """Mueve una visita a otro día/semana del MISMO mercadista. Recalcula origen y destino."""
    if str(mercadista_origen).strip() != str(mercadista_destino).strip():
        raise RutaEditError(
            "No se puede mover una visita a otro mercadista directamente. "
            "Mueve la visita (o todas las visitas del punto) a «Pendientes» "
            "y asígnala al nuevo mercadista desde allí.",
            status_code=403,
            payload={"codigo": "CAMBIO_MERCADISTA_PROHIBIDO"},
        )

    df = read_excel_cached(hp, "Horarios_Detalle")
    df = drop_spurious_total_rows_horarios_df(df)
    idx_orig_list = df.index[_mask_grupo(df, mercadista_origen, dia_origen, semana)].tolist()
    clave_visita = clave_ub(visita.get("descripcion"), visita.get("latitud"), visita.get("longitud"))

    idx_move = None
    for i in idx_orig_list:
        row = df.loc[i]
        k = clave_ub(row.get("Descripción"), row.get("Latitud"), row.get("Longitud"))
        if k == clave_visita:
            idx_move = i
            break
    if idx_move is None:
        raise RutaEditError(
            "No se encontró la visita en la ruta de origen", status_code=404
        )

    row_to_move = df.loc[idx_move].copy()
    df = df.drop(idx_move).reset_index(drop=True)

    idx_orig_resto = df.index[_mask_grupo(df, mercadista_origen, dia_origen, semana)].tolist()
    idx_orig_resto_sorted = sorted(idx_orig_resto, key=lambda i: df.at[i, "Orden Ruta"])
    for pos, idx in enumerate(idx_orig_resto_sorted):
        df.at[idx, "Orden Ruta"] = pos + 1

    row_to_move["Mercadista"] = mercadista_destino
    row_to_move["Día"] = dia_destino
    row_to_move["Fecha"] = semana_destino

    dest_indices = df.index[_mask_grupo(df, mercadista_destino, dia_destino, semana_destino)].tolist()
    dest_df = df.loc[dest_indices].sort_values("Orden Ruta").copy() if dest_indices else pd.DataFrame()
    dest_list = dest_df.to_dict("records") if not dest_df.empty else []
    pos_insert = max(0, min(orden_destino - 1, len(dest_list)))
    dest_list.insert(pos_insert, row_to_move.to_dict())
    for i, r in enumerate(dest_list):
        r["Orden Ruta"] = i + 1

    df_resto = df.drop(dest_indices).reset_index(drop=True) if dest_indices else df
    cols = list(df_resto.columns)
    dest_new_df = pd.DataFrame(dest_list)
    dest_new_df = dest_new_df[[c for c in cols if c in dest_new_df.columns]]
    df = pd.concat([df_resto, dest_new_df], ignore_index=True)

    for col in ("Latitud", "Longitud"):
        if col in df.columns:
            df[col] = df[col].apply(a_numero)

    sort_cols = [c for c in ["Mercadista", "Día", "Fecha", "Orden Ruta"] if c in df.columns]

    # Recalcular solo los grupos afectados (compatible con pandas 3.x que ya no
    # incluye las columnas de agrupación en el DataFrame que recibe apply()).
    grupos_afectados = {
        (mercadista_origen, dia_origen, semana),
        (mercadista_destino, dia_destino, semana_destino),
    }
    partes: list = []
    mask_any = pd.Series(False, index=df.index)
    for merc, dia, fecha in grupos_afectados:
        m = _mask_grupo(df, merc, dia, fecha)
        if m.any():
            partes.append(_recalcular_ruta(df.loc[m].copy(), incluye_viaje_del_dataset(hp)))
            mask_any = mask_any | m
    if partes:
        df_resto = df.loc[~mask_any].copy()
        df = pd.concat([df_resto] + partes, ignore_index=True)
    if sort_cols:
        df = df.sort_values(by=sort_cols)

    _guardar_horarios(hp, df)

    return {
        "success": True,
        "message": "Visita movida; rutas de origen y destino actualizadas con nuevos horarios y tiempos.",
    }


@with_excel_file_lock("hp")
def intercambiar_dias(
    hp: str,
    *,
    semana: str,
    mercadista: str,
    dia_a: str,
    dia_b: str,
) -> dict:
    """
    Intercambia las jornadas completas de dos días de un mercaderista.

    Todo lo del lunes pasa al miércoles y todo lo del miércoles al lunes, con su
    orden de ruta intacto. Se hace en una sola escritura y no visita por visita:
    mover treinta puntos de uno en uno serían treinta reescrituras del Excel, y
    entre medias la semana quedaría en estados imposibles —dos días con las
    mismas visitas— que el propio motor rechazaría.

    Los horarios, tiempos entre puntos y kilómetros se recalculan en los dos
    días: la ruta es la misma, pero el día al que pertenece cambia.
    """
    dia_a = str(dia_a or "").strip()
    dia_b = str(dia_b or "").strip()
    if not dia_a or not dia_b or dia_a == dia_b:
        raise RutaEditError("Elige dos días distintos para intercambiarlos", status_code=400)

    df = read_excel_cached(hp, "Horarios_Detalle")
    df = drop_spurious_total_rows_horarios_df(df)

    # Las dos máscaras se calculan ANTES de tocar nada: si se calculara la
    # segunda después de reescribir la primera, arrastraría las filas ya movidas.
    mask_a = _mask_grupo(df, mercadista, dia_a, semana)
    mask_b = _mask_grupo(df, mercadista, dia_b, semana)
    total_a, total_b = int(mask_a.sum()), int(mask_b.sum())
    if total_a == 0 and total_b == 0:
        raise RutaEditError(
            f"{mercadista} no tiene visitas ni el {dia_a.lower()} ni el {dia_b.lower()} "
            f"de la {semana}.",
            status_code=404,
        )

    df.loc[mask_a, "Día"] = dia_b
    df.loc[mask_b, "Día"] = dia_a

    df = _renumerar_y_recalcular_grupos(
        df,
        {(mercadista, dia_a, semana), (mercadista, dia_b, semana)},
        incluye_viaje_del_dataset(hp),
    )
    _guardar_horarios(hp, df)

    return {
        "success": True,
        # Corto a propósito: el calendario ya enseña el resultado del cambio,
        # así que el aviso solo confirma qué se tocó.
        "message": f"{dia_a} ↔ {dia_b} · {semana}",
        "visitas_movidas": total_a + total_b,
    }


def _texto_celda(valor) -> str:
    """Texto de una celda, con NaN tratado como vacío."""
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except (TypeError, ValueError):
        pass
    return str(valor).strip()


def _geografia_del_punto(df: pd.DataFrame, lat, lon, desc: str) -> dict:
    """Provincia, ciudad, calle, canal y cadena de otra visita del mismo punto.

    La hoja de pendientes guarda esas columnas vacías a menudo, y la visita
    recién colocada salía sin provincia aunque el punto ya estuviera agendado
    otras semanas con la suya.
    """
    columnas = [c for c in ("PROVINCIA", "CIUDAD", "CALLE", "CANAL", "CADENA") if c in df.columns]
    if df.empty or not columnas:
        return {}
    if {"Latitud", "Longitud"}.issubset(df.columns) and lat is not None and lon is not None:
        latitudes = pd.to_numeric(df["Latitud"].map(parse_coordenada_a_float), errors="coerce")
        longitudes = pd.to_numeric(df["Longitud"].map(parse_coordenada_a_float), errors="coerce")
        misma = (latitudes.round(6) == round(float(lat), 6)) & (
            longitudes.round(6) == round(float(lon), 6)
        )
    elif "Descripción" in df.columns:
        misma = df["Descripción"].astype(str).str.strip() == str(desc).strip()
    else:
        return {}
    iguales = df[misma]
    if iguales.empty:
        return {}
    datos: dict = {}
    for col in columnas:
        for valor in iguales[col]:
            texto = _texto_celda(valor)
            if texto:
                datos[col] = texto
                break
    return datos


@with_excel_file_lock("hp")
def asignar_pendiente(
    hp: str,
    *,
    mercadista_destino: str,
    dia_destino: str,
    semana_destino: str,
    orden_destino: int,
    forzar: bool,
    desc_v: str,
    lat_v: Optional[float],
    lon_v: Optional[float],
    semana_visita: str,
    tiempo_servicio_fallback: float,
    provincia_fallback: str,
) -> dict:
    """Inserta una visita pendiente en una ruta destino, recalcula y persiste."""
    df = read_excel_cached(hp, "Horarios_Detalle")
    df = drop_spurious_total_rows_horarios_df(df)
    df_pend = leer_hoja_pendientes(hp)

    ok_merc, merc_oblig, msg_merc = _validar_mercadista_punto(
        mercadista_destino, desc_v, lat_v, lon_v, df
    )
    if not ok_merc:
        raise RutaEditError(
            msg_merc,
            status_code=403,
            payload={
                "codigo": "MERCADISTA_PUNTO_BLOQUEADO",
                "mercadista_obligatorio": merc_oblig,
            },
        )

    # Localizar la fila pendiente: la de esa semana si existe y, si no,
    # cualquier otra del mismo punto. La etiqueta de semana es solo una
    # sugerencia; lo que cuadra la frecuencia es el número de filas. Sin este
    # segundo intento, colocar la visita en una semana sin etiqueta creaba la
    # visita y dejaba la pendiente, y el punto quedaba con una de más.
    clave = clave_pendiente(desc_v, lat_v, lon_v, semana_visita or semana_destino)
    clave_del_punto = clave_ub(desc_v, lat_v, lon_v)
    idx_pend = None
    idx_mismo_punto = None
    for i in df_pend.index:
        row = df_pend.loc[i]
        k = clave_pendiente(
            row.get("Descripción"),
            row.get("Latitud"),
            row.get("Longitud"),
            row.get("Semana"),
        )
        if k == clave:
            idx_pend = i
            break
        if idx_mismo_punto is None and k[:-1] == clave_del_punto:
            idx_mismo_punto = i
    if idx_pend is None:
        idx_pend = idx_mismo_punto

    if idx_pend is None:
        # No bloquear si la pendiente no se encuentra: el usuario podría estar
        # asignando una visita pasada por payload manual.
        pendiente_row = {
            "Descripción": desc_v,
            "Latitud": lat_v,
            "Longitud": lon_v,
            "Semana": semana_visita or semana_destino,
            "Tiempo Servicio (min)": tiempo_servicio_fallback,
            "Provincia": provincia_fallback,
            "Mercadista origen": "",
            "Día origen": "",
            "Motivo": "",
        }
    else:
        pendiente_row = df_pend.loc[idx_pend].to_dict()

    cols_horarios = list(df.columns)
    nueva_fila: dict = {c: None for c in cols_horarios}
    if "Mercadista" in nueva_fila:
        nueva_fila["Mercadista"] = mercadista_destino
    if "Día" in nueva_fila:
        nueva_fila["Día"] = dia_destino
    if "Fecha" in nueva_fila:
        nueva_fila["Fecha"] = semana_destino
    if "Descripción" in nueva_fila:
        nueva_fila["Descripción"] = pendiente_row.get("Descripción", desc_v)
    if "Latitud" in nueva_fila:
        nueva_fila["Latitud"] = lat_v
    if "Longitud" in nueva_fila:
        nueva_fila["Longitud"] = lon_v
    if "Tiempo Servicio (min)" in nueva_fila:
        try:
            nueva_fila["Tiempo Servicio (min)"] = float(
                pendiente_row.get("Tiempo Servicio (min)") or 0
            )
        except (TypeError, ValueError):
            nueva_fila["Tiempo Servicio (min)"] = 0.0
    # Primero lo que traiga la pendiente; si viene en blanco, lo que ya tiene
    # el mismo punto en otra semana y, en último caso, lo que mandó la pantalla.
    geo_punto = _geografia_del_punto(df, lat_v, lon_v, desc_v)
    provincia = (
        _texto_celda(pendiente_row.get("Provincia"))
        or geo_punto.get("PROVINCIA", "")
        or _texto_celda(provincia_fallback)
    )
    if "PROVINCIA" in nueva_fila:
        nueva_fila["PROVINCIA"] = provincia_display(provincia) if provincia else ""
    if "CIUDAD" in nueva_fila:
        nueva_fila["CIUDAD"] = (
            _texto_celda(pendiente_row.get("Ciudad")) or geo_punto.get("CIUDAD", "")
        )
    if "CALLE" in nueva_fila:
        nueva_fila["CALLE"] = (
            _texto_celda(pendiente_row.get("Calle")) or geo_punto.get("CALLE", "")
        )
    for col in ("CANAL", "CADENA"):
        if col in nueva_fila and geo_punto.get(col):
            nueva_fila[col] = geo_punto[col]
    if "Duración (hh:mm)" in nueva_fila:
        from route_engine.scheduling import format_duracion
        try:
            t_dur = float(pendiente_row.get("Tiempo Servicio (min)") or 0)
        except (TypeError, ValueError):
            t_dur = 0.0
        nueva_fila["Duración (hh:mm)"] = format_duracion(t_dur)
    if "Tiempo entre sucursal (min)" in nueva_fila:
        nueva_fila["Tiempo entre sucursal (min)"] = 0
    if "kilometros entre sucurlas (km)" in nueva_fila:
        nueva_fila["kilometros entre sucurlas (km)"] = 0
    if "Horario" in nueva_fila:
        nueva_fila["Horario"] = ""
    if "Orden Ruta" in nueva_fila:
        nueva_fila["Orden Ruta"] = 0

    dest_indices = df.index[_mask_grupo(df, mercadista_destino, dia_destino, semana_destino)].tolist()
    dest_df = (
        df.loc[dest_indices].sort_values("Orden Ruta").copy()
        if dest_indices
        else pd.DataFrame(columns=cols_horarios)
    )
    dest_list = dest_df.to_dict("records") if not dest_df.empty else []
    pos_insert = (
        len(dest_list)
        if orden_destino <= 0
        else max(0, min(orden_destino - 1, len(dest_list)))
    )
    dest_list.insert(pos_insert, nueva_fila)
    for i, r in enumerate(dest_list):
        r["Orden Ruta"] = i + 1

    df_resto = df.drop(dest_indices).reset_index(drop=True) if dest_indices else df.copy()
    dest_new_df = pd.DataFrame(dest_list)
    dest_new_df = dest_new_df[[c for c in cols_horarios if c in dest_new_df.columns]]

    for col in ("Latitud", "Longitud"):
        if col in dest_new_df.columns:
            dest_new_df[col] = dest_new_df[col].apply(a_numero)
    dest_recalc = _recalcular_ruta(dest_new_df, incluye_viaje_del_dataset(hp))

    # Validar el tope diario con los tiempos recalculados. La cuota sale del
    # propio Excel (480 o 400 min/día según el preset con el que se generó).
    tope_dia = cuota_dia_del_dataset(hp)
    servicio_total = travel_total = combinado = 0.0
    col_serv = "Tiempo Servicio (min)"
    col_travel = "Tiempo entre sucursal (min)"
    if col_serv in dest_recalc.columns and col_travel in dest_recalc.columns:
        servicio_total = float(pd.to_numeric(dest_recalc[col_serv], errors="coerce").fillna(0).sum())
        travel_total = float(pd.to_numeric(dest_recalc[col_travel], errors="coerce").fillna(0).sum())
        combinado = servicio_total + travel_total
        if combinado > tope_dia and not forzar:
            raise RutaEditError(
                (
                    f"El día {dia_destino} de {mercadista_destino} ({semana_destino}) "
                    f"quedaría con {combinado:.0f} min combinados "
                    f"(servicio {servicio_total:.0f} + viaje {travel_total:.0f}), "
                    f"por encima del tope de {tope_dia} min. "
                    "Reenvía con 'forzar': true para asignar de todas formas."
                ),
                status_code=409,
                payload={
                    "tope_excedido": True,
                    "limite_min": tope_dia,
                    "servicio_total_min": round(servicio_total, 1),
                    "travel_total_min": round(travel_total, 1),
                    "combinado_total_min": round(combinado, 1),
                    "exceso_min": round(combinado - tope_dia, 1),
                },
            )

    df = pd.concat([df_resto, dest_recalc], ignore_index=True)
    sort_cols = [c for c in ["Mercadista", "Día", "Fecha", "Orden Ruta"] if c in df.columns]
    if sort_cols:
        df = df.sort_values(by=sort_cols).reset_index(drop=True)

    if idx_pend is not None:
        df_pend = df_pend.drop(idx_pend).reset_index(drop=True)

    _guardar_horarios_pendientes(hp, df, df_pend)

    resp: dict = {
        "success": True,
        "message": (
            f"Visita asignada a {mercadista_destino} / {dia_destino} / {semana_destino} "
            f"en la posición {pos_insert + 1}."
        ),
    }
    if forzar and combinado:
        resp["advertencia"] = (
            f"Asignada forzando el tope: combinado {combinado:.0f} min "
            f"(servicio {servicio_total:.0f} + viaje {travel_total:.0f}), "
            f"sobrepasa {tope_dia} min."
        )
    return resp


# ---------------------------------------------------------------------------
# Mercaderistas vacíos
# ---------------------------------------------------------------------------


@with_excel_file_lock("hp")
def crear_mercadista_vacio(hp: str, *, fin_de_semana: bool = False) -> dict:
    """Da de alta un mercaderista sin puntos para poder arrastrarle pendientes.

    Como los mercaderistas salen de las filas de Horarios_Detalle, uno vacío no
    existe hasta su primera visita: se anota en la hoja interna
    Mercadistas_Extra, que el calendario mezcla con los que sí tienen filas.
    """
    from services.mercadistas_extra import (
        JORNADA_FIN_SEMANA,
        JORNADA_SEMANA,
        SHEET_MERCADISTAS_EXTRA,
        dias_de_jornada,
        leer_mercadistas_extra,
        siguiente_nombre_mercadista,
    )

    df = drop_spurious_total_rows_horarios_df(read_excel_cached(hp, "Horarios_Detalle"))
    nombres = (
        set(df["Mercadista"].astype(str).str.strip()) if "Mercadista" in df.columns else set()
    )
    extra = leer_mercadistas_extra(hp)
    nombres |= set(extra["Mercadista"].astype(str).str.strip())
    nombre = siguiente_nombre_mercadista(nombres)
    jornada = JORNADA_FIN_SEMANA if fin_de_semana else JORNADA_SEMANA
    extra = pd.concat(
        [extra, pd.DataFrame([{"Mercadista": nombre, "Jornada": jornada}])],
        ignore_index=True,
    )
    _escribir_hojas(hp, {SHEET_MERCADISTAS_EXTRA: extra})
    return {
        "success": True,
        "mercadista": nombre,
        "dias": dias_de_jornada(jornada),
        "message": f"{nombre} creado sin puntos.",
    }
