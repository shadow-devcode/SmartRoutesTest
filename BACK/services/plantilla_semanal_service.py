"""
Importación de la plantilla semanal del cliente para la vista comparativa.

El archivo que maneja el negocio no tiene el formato que produce el motor: es
una fila por punto de venta con los días en columnas —ENTRADA, SALIDA y TIEMPO
TRABAJADO de cada día—, más su provincia, ciudad, mercaderista, canal y cadena.

Aquí se convierte a la hoja `Horarios_Detalle` que espera el resto del sistema
(una fila por visita), de modo que ese plan pueda verse y compararse en el mapa
igual que un rutero generado.
"""
from __future__ import annotations

import io
import re
import unicodedata
from datetime import datetime, time, timedelta

import pandas as pd

from route_engine.config import ORDEN_SEMANA
from route_engine.geo import normalizar_coord_geografica
from route_engine.mapbox import calcular_tiempo_entre

# Cuatro semanas: la plantilla describe una semana tipo y el resto del sistema
# razona en meses de cuatro semanas iguales.
SEMANAS = ["semana 1", "semana 2", "semana 3", "semana 4"]

COLUMNAS_SALIDA = [
    "Mercadista", "Día", "Orden Ruta", "Descripción", "Latitud", "Longitud",
    "CANAL", "CADENA", "PROVINCIA", "CIUDAD", "CALLE",
    "Tiempo Servicio (min)", "Duración (hh:mm)", "Tiempo entre sucursal (min)",
    "kilometros entre sucurlas (km)", "Horario", "Fecha",
]


class PlantillaError(Exception):
    """Problema de formato que hay que contarle a quien sube el archivo."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _norm(texto) -> str:
    """Nombre de columna comparable: sin tildes, sin dobles espacios, en mayúsculas."""
    s = str(texto or "").strip().upper()
    s = "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s)


def _buscar(columnas: list, *alternativas: str):
    """Primera columna cuyo nombre normalizado contenga alguna de las alternativas."""
    for alternativa in alternativas:
        objetivo = _norm(alternativa)
        for original in columnas:
            if objetivo in _norm(original):
                return original
    return None


def _a_minutos(valor) -> float | None:
    """Hora de una celda (datetime, time, texto '10:30' o número) en minutos del día."""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    try:
        if pd.isna(valor):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(valor, (datetime, pd.Timestamp)):
        return valor.hour * 60 + valor.minute
    if isinstance(valor, time):
        return valor.hour * 60 + valor.minute
    if isinstance(valor, (int, float)):
        # Excel guarda las horas como fracción de día.
        if 0 <= float(valor) <= 1:
            return round(float(valor) * 24 * 60)
        return None
    texto = str(valor).strip()
    match = re.match(r"^(\d{1,2})[:.](\d{2})", texto)
    if not match:
        return None
    return int(match.group(1)) * 60 + int(match.group(2))


def _hhmm(minutos: float) -> str:
    minutos = int(round(minutos)) % (24 * 60)
    return f"{minutos // 60:02d}:{minutos % 60:02d}"


def _duracion(minutos: float) -> str:
    horas, resto = divmod(int(round(minutos)), 60)
    return f"{horas}:{resto:02d} horas"


def _minutos_trabajados(valor, entrada: float | None, salida: float | None) -> float:
    """
    Minutos de trabajo de un día.

    Manda el par entrada/salida cuando existe: es el dato exacto. La columna de
    tiempo trabajado se usa como respaldo y admite las dos unidades que aparecen
    en los archivos reales —2,5 (horas) y 150 (minutos)—, distinguiéndolas por
    el tamaño: nadie trabaja 2 minutos en un punto ni 150 horas.
    """
    if entrada is not None and salida is not None and salida > entrada:
        return float(salida - entrada)
    numero = pd.to_numeric(valor, errors="coerce")
    if pd.isna(numero) or float(numero) <= 0:
        return 0.0
    numero = float(numero)
    return round(numero * 60, 2) if numero <= 24 else round(numero, 2)


def _mapa_dias(columnas: list) -> dict:
    """
    Para cada día: su columna de tiempo trabajado y sus entrada/salida.

    ENTRADA y SALIDA se repiten una vez por día y pandas las renombra
    (ENTRADA, ENTRADA.1...), así que no se pueden buscar por nombre: se toman
    las dos columnas que preceden a «TIEMPO TRABAJADO <DÍA>», que es como está
    montada la plantilla.
    """
    mapa = {}
    for indice, columna in enumerate(columnas):
        nombre = _norm(columna)
        if not nombre.startswith("TIEMPO TRABAJADO"):
            continue
        dia = next((d for d in ORDEN_SEMANA if _norm(d) in nombre), None)
        if not dia:
            continue
        entrada = salida = None
        if indice >= 2:
            previa_1, previa_2 = _norm(columnas[indice - 2]), _norm(columnas[indice - 1])
            if previa_1.startswith("ENTRADA") and previa_2.startswith("SALIDA"):
                entrada, salida = columnas[indice - 2], columnas[indice - 1]
        mapa[dia] = {"tiempo": columna, "entrada": entrada, "salida": salida}
    return mapa


def convertir_plantilla(contenido: bytes) -> pd.DataFrame:
    """
    Convierte la plantilla semanal en un `Horarios_Detalle` completo.

    Cada día con tiempo trabajado se convierte en una visita, y la semana tipo
    se repite en las cuatro semanas del mes, que es lo que representa la
    frecuencia mensual del propio archivo.
    """
    try:
        df = pd.read_excel(io.BytesIO(contenido))
    except Exception as exc:
        raise PlantillaError(f"No se pudo leer el Excel: {exc}") from exc

    columnas = list(df.columns)
    dias = _mapa_dias(columnas)
    if not dias:
        raise PlantillaError(
            "El archivo no tiene columnas «TIEMPO TRABAJADO <día>». ¿Seguro que "
            "es la plantilla semanal? Debe traer una columna de tiempo trabajado "
            "por cada día de la semana."
        )

    col_merc = _buscar(columnas, "MERCADERISTA", "MERCADISTA")
    col_lat = _buscar(columnas, "LATITUD", "LAT")
    col_lon = _buscar(columnas, "LONGITUD", "LONG")
    col_desc = _buscar(columnas, "CODIGO CLIENTE SAP2", "NOMBRE", "DESCRIPCION", "CLIENTE")
    col_codigo = _buscar(columnas, "CODIGO CLIENTE SAP")
    col_prov = _buscar(columnas, "PROVINCIA")
    col_ciudad = _buscar(columnas, "CIUDAD")
    col_canal = _buscar(columnas, "CANAL")
    col_cadena = _buscar(columnas, "CADENA")

    faltan = [
        etiqueta
        for etiqueta, columna in (
            ("MERCADERISTA", col_merc), ("LATITUD", col_lat), ("LONGITUD", col_lon)
        )
        if columna is None
    ]
    if faltan:
        raise PlantillaError(
            f"Faltan columnas obligatorias: {', '.join(faltan)}. "
            "Sin mercaderista y coordenadas no se puede pintar la ruta."
        )

    visitas = []
    for _, fila in df.iterrows():
        merc = str(fila.get(col_merc, "") or "").strip()
        # Con recorte al rango geográfico: Excel pierde a veces el punto
        # decimal (-78498314 en vez de -78.498314) y cada tramo hacia ese punto
        # medía 22.000 km; dos puntos así inflaban el rutero de 5.000 a 700.000.
        lat = normalizar_coord_geografica(fila.get(col_lat), "lat")
        lon = normalizar_coord_geografica(fila.get(col_lon), "lon")
        if not merc or lat is None or lon is None or (lat == 0 and lon == 0):
            continue

        descripcion = str(fila.get(col_desc, "") if col_desc else "" or "").strip()
        if not descripcion and col_codigo:
            descripcion = str(fila.get(col_codigo, "") or "").strip()
        if not descripcion:
            descripcion = f"{lat:.5f}, {lon:.5f}"

        for dia, cols in dias.items():
            entrada = _a_minutos(fila.get(cols["entrada"])) if cols["entrada"] else None
            salida = _a_minutos(fila.get(cols["salida"])) if cols["salida"] else None
            minutos = _minutos_trabajados(fila.get(cols["tiempo"]), entrada, salida)
            if minutos <= 0:
                continue
            visitas.append({
                "Mercadista": merc,
                "Día": dia,
                "Descripción": descripcion,
                "Latitud": lat,
                "Longitud": lon,
                "CANAL": str(fila.get(col_canal, "") or "").strip() if col_canal else "",
                "CADENA": str(fila.get(col_cadena, "") or "").strip().upper() if col_cadena else "",
                "PROVINCIA": str(fila.get(col_prov, "") or "").strip() if col_prov else "",
                "CIUDAD": str(fila.get(col_ciudad, "") or "").strip() if col_ciudad else "",
                "CALLE": "",
                "Tiempo Servicio (min)": minutos,
                "_entrada": entrada,
                "_salida": salida,
            })

    if not visitas:
        raise PlantillaError(
            "No se encontró ninguna visita con tiempo trabajado. Revisa que las "
            "columnas de los días traigan horas o minutos."
        )

    return _armar_horarios(pd.DataFrame(visitas))


def _armar_horarios(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ordena cada jornada, calcula orden de ruta, horarios, kilómetros y tiempo de
    desplazamiento, y repite la semana tipo en las cuatro semanas del mes.

    El desplazamiento sale de `calcular_tiempo_entre`, el mismo modelo con el
    que el motor calcula sus propias rutas (distancia recta corregida a
    carretera y velocidad por tramo). Usar aquí otro cálculo —o dejarlo en
    cero, como estaba— haría que la comparativa midiera los dos planes con
    reglas distintas, que es justo lo que esta pantalla existe para evitar.
    """
    filas = []
    for (merc, dia), grupo in df.groupby(["Mercadista", "Día"], sort=False):
        # Por hora de entrada cuando la hay; si no, en el orden del archivo.
        grupo = grupo.sort_values(
            "_entrada", kind="stable", na_position="last"
        ).reset_index(drop=True)

        reloj = None
        lat_prev = lon_prev = None
        for orden, (_, visita) in enumerate(grupo.iterrows(), start=1):
            minutos = float(visita["Tiempo Servicio (min)"])
            lat = float(visita["Latitud"])
            lon = float(visita["Longitud"])
            viaje_min, viaje_km = calcular_tiempo_entre(lat_prev, lon_prev, lat, lon)
            lat_prev, lon_prev = lat, lon

            entrada = visita["_entrada"]
            if entrada is not None:
                # La hora la puso el negocio en la plantilla: manda ella.
                inicio = entrada
            elif reloj is not None:
                # Sin hora de entrada la jornada se encadena, y entre una visita
                # y la siguiente hay que llegar hasta allí.
                inicio = reloj + viaje_min
            else:
                inicio = 8 * 60
            fin = inicio + minutos
            reloj = fin

            base = {
                "Mercadista": merc,
                "Día": dia,
                "Orden Ruta": orden,
                "Descripción": visita["Descripción"],
                "Latitud": lat,
                "Longitud": lon,
                "CANAL": visita["CANAL"],
                "CADENA": visita["CADENA"],
                "PROVINCIA": visita["PROVINCIA"],
                "CIUDAD": visita["CIUDAD"],
                "CALLE": "",
                "Tiempo Servicio (min)": minutos,
                "Duración (hh:mm)": _duracion(minutos),
                "Tiempo entre sucursal (min)": round(viaje_min, 2),
                "kilometros entre sucurlas (km)": round(viaje_km, 2),
                "Horario": f"{_hhmm(inicio)} - {_hhmm(fin)}",
            }
            for semana in SEMANAS:
                filas.append({**base, "Fecha": semana})

    salida = pd.DataFrame(filas, columns=COLUMNAS_SALIDA)
    orden_dias = {d: i for i, d in enumerate(ORDEN_SEMANA)}
    salida["_dia"] = salida["Día"].map(orden_dias).fillna(9)
    salida = salida.sort_values(
        ["Mercadista", "Fecha", "_dia", "Orden Ruta"], kind="stable"
    ).drop(columns=["_dia"]).reset_index(drop=True)

    # Misma vara que el rutero del sistema: kilómetros reales de carretera. El
    # plan del cliente se mide, no se toca: ni se retiran visitas ni se mueven
    # sus horas. Si Mapbox no responde, quedan los tramos estimados.
    try:
        from route_engine.carretera import recalcular_tramos_por_carretera

        recalcular_tramos_por_carretera(
            salida, retirar_sobrantes=False, recomponer_horarios=False
        )
    except Exception:
        pass
    return salida


def plantilla_a_excel(df: pd.DataFrame) -> bytes:
    """Empaqueta el DataFrame como un .xlsx con hoja Horarios_Detalle."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Horarios_Detalle", index=False)
    return buffer.getvalue()
