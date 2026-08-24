"""
Reglas de frecuencia de visitas por mes.
Define cuántas veces y en qué semanas/días se visita cada punto.

Frecuencias:
  1  → 1 visita/mes (cualquier semana)
  2  → 2 visitas/mes: (semana 1 y 3) o (2 y 4) o (1 y 4) o (2 y 3)
  4  → 4 visitas/mes (todas las semanas)
  8  → 8 visitas/mes (2/semana): (Lun y Mié) o (Mié y Vie) o (Mar y Jue)
  12 → 12 visitas/mes (3/semana): Lunes, Miércoles y Viernes
  16 → 16 visitas/mes (4/semana): Lunes, Martes, Jueves y Viernes
  20 → 20 visitas/mes (5/semana): los 5 días
"""

import pandas as pd

from route_engine.config import DAY_NAMES


def semanas_por_frecuencia(frecuencia_mes):
    """
    Número de semanas en las que se visita (1-4).
    Para 8, 12, 16, 20 siempre son 4 semanas.
    """
    val = pd.to_numeric(frecuencia_mes, errors="coerce")
    if pd.isna(val):
        return 1
    try:
        val_int = int(val)
    except Exception:
        return 1
    if val_int in (8, 12, 16, 20):
        return 4
    return min(4, max(1, val_int))


def semanas_distribuidas(frecuencia_mes, idx_seed=0):
    """
    Distribuye visitas en semanas (1-4).
    - 1 → cualquier semana: [1], [2], [3] o [4]
    - 2 → (1 y 3), (2 y 4), (1 y 4) o (2 y 3)
    - 4 → [1, 2, 3, 4]
    - 8, 12, 16, 20: no se usa (tienen días fijos por semana).
    """
    val = pd.to_numeric(frecuencia_mes, errors="coerce")
    if pd.isna(val):
        return [1]
    try:
        val_int = int(val)
    except Exception:
        return [1]
    if val_int <= 0:
        return [1]

    if val_int == 1:
        return [1 + (idx_seed % 4)]
    if val_int == 2:
        opciones = [[1, 3], [2, 4], [1, 4], [2, 3]]
        return opciones[idx_seed % len(opciones)]
    if val_int == 4:
        return [1, 2, 3, 4]
    if val_int in (8, 12, 16, 20):
        return [1, 2, 3, 4]

    n = min(4, max(1, val_int))
    return list(range(1, n + 1))


def dias_fijos_por_frecuencia(frecuencia_mes, idx_seed=0):
    """
    Días fijos por semana para frecuencias 8, 12, 16, 20.
    - 8  → 2 visitas/semana, 10 patrones alternativos
    - 12 → 3 visitas/semana, 10 patrones alternativos
    - 16 → 4 visitas/semana, 5 patrones alternativos
    - 20 → 5 visitas/semana: todos los días (sin alternativa posible)

    El patrón se elige rotando por `idx_seed`, de forma estable: el mismo punto
    cae siempre en los mismos días, pero dos puntos distintos no tienen por qué
    coincidir.

    La rotación importa. Antes solo la frecuencia 8 tenía alternativas: TODOS
    los puntos de frecuencia 12 exigían lunes/miércoles/viernes y todos los de
    16 exigían lunes/martes/jueves/viernes. Como estas visitas entran al día
    como obligatorias y se saltan el tope de 480 min, un mercadista con varios
    de esos puntos desbordaba justo esos días; el postproceso recortaba el
    exceso y lo mandaba a pendientes, mientras sus martes y jueves quedaban
    medio vacíos. 782 de las 1067 visitas sin colocar eran de este tipo.

    Retorna lista de días (strings) o [] si no aplica.
    """
    val = pd.to_numeric(frecuencia_mes, errors="coerce")
    if pd.isna(val):
        return []
    try:
        val_int = int(val)
    except Exception:
        return []

    # Patrones con los días repartidos lo más uniformemente posible dentro de la
    # semana: se busca que un punto no acumule sus visitas en días consecutivos.
    opciones_por_frecuencia = {
        8: [
            ["Lunes", "Martes"],
            ["Lunes", "Miércoles"],
            ["Lunes", "Jueves"],
            ["Lunes", "Viernes"],
            ["Martes", "Miércoles"],
            ["Martes", "Jueves"],
            ["Martes", "Viernes"],
            ["Miércoles", "Jueves"],
            ["Miércoles", "Viernes"],
            ["Jueves", "Viernes"]
        ],
        12: [
            ["Lunes", "Martes", "Miércoles"],
            ["Lunes", "Martes", "Jueves"],
            ["Lunes", "Martes", "Viernes"],
            ["Lunes", "Miércoles", "Jueves"],
            ["Lunes", "Miércoles", "Viernes"],
            ["Lunes", "Jueves", "Viernes"],
            ["Martes", "Miércoles", "Jueves"],
            ["Martes", "Miércoles", "Viernes"],
            ["Martes", "Jueves", "Viernes"],
            ["Miércoles", "Jueves", "Viernes"],
        ],
        16: [
            ["Lunes", "Martes", "Jueves", "Viernes"],
            ["Lunes", "Miércoles", "Jueves", "Viernes"],
            ["Lunes", "Martes", "Miércoles", "Viernes"],
            ["Lunes", "Martes", "Miércoles", "Jueves"],
            ["Martes", "Miércoles", "Jueves", "Viernes"],
        ],
    }

    opciones = opciones_por_frecuencia.get(val_int)
    if opciones:
        return opciones[int(idx_seed) % len(opciones)]
    if val_int == 20:
        return list(DAY_NAMES)
    return []
