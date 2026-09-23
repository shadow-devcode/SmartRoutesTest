"""
Metadatos de la ejecución que generó un Excel de rutas.

El modelo de jornada (si el desplazamiento descuenta o no de la cuota) se elige
por ejecución, así que deja de ser una propiedad del servidor y pasa a ser una
propiedad DEL ARCHIVO. Quien lo lea después —el dashboard, sobre todo— tiene
que medir con el mismo criterio con el que se generó, o mostrará porcentajes
que no cuadran con las rutas que hay dentro.

Se guarda en una hoja del propio Excel y no en la BD porque el archivo viaja
solo: se descarga, se vuelve a subir como comparativa y se sirve también cuando
la autenticación no está cargada y no hay tabla `route_dataset` donde mirar.
"""
from __future__ import annotations

import pandas as pd

SHEET_CONFIG_PROCESAMIENTO = "Config_Procesamiento"

CLAVE_INCLUYE_VIAJE = "jornada_incluye_desplazamiento"
CLAVE_TIPO_RUTA = "tipo_ruta"
CLAVE_TIPO_CARGA = "tipo_carga"
CLAVE_MAX_DIA = "max_minutos_dia"
CLAVE_MAX_MES = "max_minutos_mes"
CLAVE_CUADRILLA_FIN_SEMANA = "mercaderistas_fin_de_semana"
CLAVE_CANAL = "canal"
CLAVE_CADENAS = "cadenas"
CLAVE_GRUPOS = "grupos_de_cadenas"

_COL_PARAMETRO = "Parámetro"
_COL_VALOR = "Valor"
_COL_DESCRIPCION = "Descripción"

_VERDADEROS = frozenset({"1", "true", "yes", "si", "sí", "verdadero"})


def _texto_grupos(grupos_cadenas) -> str:
    """«Nombre: cadena, cadena · Nombre: …», con el nombre que puso el usuario."""
    from route_engine.load_grouping import grupos_multicanal

    mapa = grupos_multicanal()
    partes = []
    for indice, cadenas in enumerate(grupos_cadenas or [], start=1):
        if not cadenas:
            continue
        nombre = next(
            (mapa[str(c).strip().upper()] for c in cadenas if str(c).strip().upper() in mapa),
            f"Grupo {indice}",
        )
        partes.append(f"{nombre}: {', '.join(cadenas)}")
    return " · ".join(partes) or "—"


def build_config_procesamiento_df(
    *,
    incluye_viaje: bool,
    tipo_ruta: str,
    tipo_carga: str = "",
    max_dia_minutos: int = 0,
    max_mes_minutos: int,
    hora_fin_minutos: int,
    cuadrilla_fin_semana: int = 0,
    canal: str = "",
    cadenas=None,
    grupos_cadenas=None,
) -> pd.DataFrame:
    """Hoja de metadatos: cómo se calculó este Excel."""
    modelo = (
        "El tiempo de desplazamiento SÍ descuenta de la cuota de jornada."
        if incluye_viaje
        else "Solo el tiempo de servicio en el punto descuenta de la cuota; "
        "el desplazamiento se informa pero no consume jornada."
    )
    filas = [
        (CLAVE_INCLUYE_VIAJE, "true" if incluye_viaje else "false", modelo),
        (CLAVE_TIPO_RUTA, str(tipo_ruta or ""), "Modalidad de mercaderista aplicada."),
        (
            CLAVE_TIPO_CARGA,
            str(tipo_carga or "zona"),
            "Criterio con el que se repartió el trabajo entre mercaderistas "
            "(zona geográfica, ciudad o cadena comercial).",
        ),
        (
            CLAVE_MAX_DIA,
            str(int(max_dia_minutos)),
            "Cuota diaria contra la que se llenó cada jornada.",
        ),
        (
            CLAVE_MAX_MES,
            str(int(max_mes_minutos)),
            "Cuota mensual por mercaderista (100% de ocupación).",
        ),
        (
            CLAVE_CANAL,
            str(canal or "todos"),
            "Canal comercial al que se acotó la ejecución.",
        ),
        (
            CLAVE_CADENAS,
            ", ".join(cadenas) if cadenas else "todas",
            "Cadenas planificadas. Los puntos de otras cadenas quedaron fuera "
            "del alcance de esta ejecución.",
        ),
        (
            CLAVE_GRUPOS,
            _texto_grupos(grupos_cadenas),
            "Grupos de cadenas del reparto multicanal; cada grupo tiene su "
            "propio equipo de mercaderistas.",
        ),
        (
            CLAVE_CUADRILLA_FIN_SEMANA,
            str(int(cuadrilla_fin_semana)),
            "Mercaderistas con la jornada corrida a miércoles-domingo para "
            "recoger las visitas que no cabían de lunes a viernes (0 = ninguno).",
        ),
        (
            "hora_fin_jornada",
            f"{int(hora_fin_minutos) // 60:02d}:{int(hora_fin_minutos) % 60:02d}",
            "Hora límite para cerrar la última visita del día.",
        ),
    ]
    return pd.DataFrame(filas, columns=[_COL_PARAMETRO, _COL_VALOR, _COL_DESCRIPCION])


def _valor_crudo(df_config: pd.DataFrame | None, clave: str) -> str | None:
    """Valor de una clave de la hoja de configuración, o None si no está."""
    if df_config is None or df_config.empty:
        return None
    if _COL_PARAMETRO not in df_config.columns or _COL_VALOR not in df_config.columns:
        return None
    fila = df_config[df_config[_COL_PARAMETRO].astype(str).str.strip() == clave]
    if fila.empty:
        return None
    return str(fila.iloc[0][_COL_VALOR]).strip()


def leer_entero(df_config: pd.DataFrame | None, clave: str, *, por_defecto: int) -> int:
    """Lee una clave numérica de la hoja de configuración."""
    crudo = _valor_crudo(df_config, clave)
    if crudo is None:
        return por_defecto
    try:
        return int(float(crudo))
    except (TypeError, ValueError):
        return por_defecto


def leer_incluye_viaje(df_config: pd.DataFrame | None, *, por_defecto: bool) -> bool:
    """
    Extrae el modelo de jornada de la hoja de configuración.

    Devuelve `por_defecto` si el DataFrame no existe o no trae la clave: los
    Excels generados antes de que existiera esta hoja se siguen midiendo con el
    modelo por defecto del servidor, que es el que se usó para crearlos.
    """
    crudo = _valor_crudo(df_config, CLAVE_INCLUYE_VIAJE)
    if crudo is None:
        return por_defecto
    return crudo.lower() in _VERDADEROS
