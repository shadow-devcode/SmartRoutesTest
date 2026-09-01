"""
Tipos de carga: cómo se particiona el trabajo antes de repartirlo.

El planificador arma cada mercaderista metiendo puntos en una "caja" mientras le
quepan en el mes y estén cerca. El tipo de carga añade una barrera INFRANQUEABLE
a esa caja: dos puntos de particiones distintas no pueden acabar en la misma
persona, por muy cerca que estén y por mucho hueco que sobre.

  zona    — sin barrera. Manda solo la geografía (radio de RADIO_ZONA_KM), que es
            como ha funcionado el motor hasta ahora.
  ciudad  — un mercaderista atiende puntos de UNA sola ciudad.
  cadena  — un mercaderista atiende puntos de UNA sola cadena comercial
            (columna CADENA del Excel: CORAL, FAVORITA, ROSADO, SANTA MARIA,
            TIA, TRADICIONAL...). Regla estricta pedida por negocio: si alguien
            empieza con TRADICIONAL, no puede tener puntos de ninguna otra.
  canal   — un mercaderista atiende puntos de UN solo canal comercial (columna
            `canal` del Excel: Moderno, TRADICIONAL...). Es la misma regla que
            la de cadena pero un escalón más arriba: quien lleva el canal
            moderno puede visitar CORAL y TIA, pero no la tienda de barrio.
  multicanal — las cadenas se agrupan a mano y cada grupo va a su propio equipo
            de mercaderistas: "ROSADO + CORAL" a unos, "TIA + FAVORITA" a otros.
            Es el caso general de los dos anteriores; los grupos los define
            quien lanza el procesamiento, no el archivo.

La geografía sigue aplicando SIEMPRE. Agrupar por cadena sin límite de distancia
produciría un mercaderista con puntos de TIA en Quito y en Machala; lo que hace
el tipo de carga es partir primero por el criterio de negocio y dejar que dentro
de cada partición mande la cercanía como siempre.
"""
from __future__ import annotations

TIPO_ZONA = "zona"
TIPO_CIUDAD = "ciudad"
TIPO_CADENA = "cadena"
TIPO_CANAL = "canal"
TIPO_MULTICANAL = "multicanal"

TIPOS_CARGA = (TIPO_ZONA, TIPO_CIUDAD, TIPO_CADENA, TIPO_CANAL, TIPO_MULTICANAL)

ETIQUETAS = {
    TIPO_ZONA: "por zona geográfica",
    TIPO_CIUDAD: "por ciudad",
    TIPO_CADENA: "por cadena",
    TIPO_CANAL: "por canal",
    TIPO_MULTICANAL: "multicanal (grupos de cadenas)",
}

# Grupos de cadenas de la ejecución actual: {CADENA: "nombre del grupo"}.
#
# Es estado de ejecución, como el modelo de jornada o la cuadrilla de fin de
# semana: lo define quien lanza el procesamiento y no viene en el archivo. Se
# limpia al empezar cada procesamiento.
_grupos_multicanal: dict = {}


def set_grupos_multicanal(grupos) -> dict:
    """
    Fija los grupos de cadenas. `grupos` es una lista de listas de cadenas; el
    primer grupo es "Grupo 1", el segundo "Grupo 2"... Devuelve el mapa
    resultante {CADENA: grupo}.

    Una cadena solo puede estar en un grupo: si se repite, manda el primero en
    el que aparece. Dos grupos con la misma cadena no serían dos barreras sino
    una contradicción.
    """
    _grupos_multicanal.clear()
    for indice, cadenas in enumerate(grupos or [], start=1):
        nombre = f"Grupo {indice}"
        for cadena in cadenas or []:
            clave = str(cadena or "").strip().upper()
            if clave and clave not in _grupos_multicanal:
                _grupos_multicanal[clave] = nombre
    return dict(_grupos_multicanal)


def limpiar_grupos_multicanal() -> None:
    _grupos_multicanal.clear()


def grupos_multicanal() -> dict:
    return dict(_grupos_multicanal)


def cadenas_agrupadas() -> list:
    """Todas las cadenas que participan, en el orden en que se agruparon."""
    return list(_grupos_multicanal.keys())

# Valor usado cuando la fila no trae el dato. No se mezcla con las demás: si
# media docena de puntos vienen sin cadena, forman su propio grupo en vez de
# colarse en cualquiera.
SIN_DATO = "SIN DATO"


def normalizar_tipo_carga(valor) -> str:
    """Tipo de carga válido; cualquier cosa desconocida cae en 'zona'."""
    texto = str(valor or "").strip().lower()
    return texto if texto in TIPOS_CARGA else TIPO_ZONA


def clave_grupo(inst, tipo_carga: str) -> str | None:
    """
    Partición a la que pertenece una visita. `None` = sin barrera (tipo 'zona').
    """
    if tipo_carga == TIPO_CIUDAD:
        return str(inst.get("ciudad_punto") or "").strip().upper() or SIN_DATO
    if tipo_carga == TIPO_CADENA:
        return str(inst.get("cadena_punto") or "").strip().upper() or SIN_DATO
    if tipo_carga == TIPO_CANAL:
        return str(inst.get("canal_punto") or "").strip().upper() or SIN_DATO
    if tipo_carga == TIPO_MULTICANAL:
        cadena = str(inst.get("cadena_punto") or "").strip().upper()
        return _grupos_multicanal.get(cadena, SIN_DATO)
    return None


def resumen_grupos(visit_instances, tipo_carga: str) -> dict:
    """Cuántas visitas y minutos hay en cada partición, para el log y la validación."""
    if tipo_carga == TIPO_ZONA:
        return {}
    resumen: dict = {}
    for inst in visit_instances:
        clave = clave_grupo(inst, tipo_carga)
        datos = resumen.setdefault(clave, {"visitas": 0, "minutos": 0.0})
        datos["visitas"] += 1
        datos["minutos"] += float(inst.get("tiempo") or 0)
    return resumen


def validar_datos_suficientes(visit_instances, tipo_carga: str) -> str | None:
    """
    Mensaje de error si el Excel no trae el dato que ese tipo de carga necesita.

    Se comprueba antes de procesar: sin la columna CADENA, un reparto "por
    cadena" metería todos los puntos en un único grupo "SIN DATO" y el usuario
    recibiría un resultado que parece correcto pero no cumple la regla que pidió.
    """
    if tipo_carga == TIPO_ZONA or not visit_instances:
        return None

    campo = {
        TIPO_CADENA: "cadena_punto",
        TIPO_MULTICANAL: "cadena_punto",
        TIPO_CANAL: "canal_punto",
    }.get(tipo_carga, "ciudad_punto")
    # "SIN_PROVINCIA" es el relleno que deja el motor cuando no pudo deducir la
    # ubicación: cuenta como ausencia de dato, no como una ciudad. Sin esto,
    # repartir "por ciudad" sin geocodificación disponible metía los 4.817
    # puntos en un único grupo y el resultado era idéntico al de "por zona"
    # sin avisar de nada.
    vacios = {"", "SIN_PROVINCIA", "SIN PROVINCIA", SIN_DATO}
    con_dato = sum(
        1 for i in visit_instances
        if str(i.get(campo) or "").strip().upper() not in vacios
    )
    if con_dato:
        return None

    if tipo_carga == TIPO_CADENA:
        return (
            "El archivo no tiene la columna CADENA, necesaria para repartir por "
            "cadena. Añádela al final del Excel (valores como CORAL, FAVORITA, "
            "ROSADO, SANTA MARIA, TIA, TRADICIONAL) o elige otro tipo de carga."
        )
    if tipo_carga == TIPO_CANAL:
        return (
            "El archivo no tiene la columna `canal`, necesaria para repartir por "
            "canal. Añádela al Excel (valores como Moderno o TRADICIONAL) o "
            "elige otro tipo de carga."
        )
    if tipo_carga == TIPO_MULTICANAL:
        return (
            "El archivo no tiene la columna CADENA, necesaria para agrupar "
            "cadenas en multicanal. Añádela al final del Excel o elige otro "
            "tipo de carga."
        )
    return (
        "El archivo no tiene ciudad en ninguna fila y tampoco se pudo deducir de "
        "las coordenadas. Añade una columna CIUDAD o elige otro tipo de carga."
    )
