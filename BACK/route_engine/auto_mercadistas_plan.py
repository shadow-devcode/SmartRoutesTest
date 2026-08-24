"""
Plan de mercadistas en modo automático (sin tabla por provincia en Excel).

- Mínimo de mercadistas según minutos COMBINADOS (servicio + colchón de
  desplazamiento estimado) vs capacidad mensual combinada
  (cuota_mes() = 480 × 5 × 4 = 9600) y vs la lógica semanal.
- Con varias provincias: se añaden mercadistas por provincia cuando la carga
  combinada de esa provincia supera lo cubrible por un solo mercadista al mes.

Nota: la función "viva" usada por `processor.py` es
`calcular_plan_mercadistas_por_provincia` en `excel_reader.py`. Este módulo
mantiene la misma política de reserva de viaje para evitar inconsistencias
si otro flujo lo invoca en el futuro.
"""

from __future__ import annotations

import math
from collections import defaultdict

from route_engine.config import (
    cuota_dia,
    cuota_mes,
    cuota_semana,
    min_semana,
    travel_estimado_por_visita_plan_min,
)
from route_engine.mapbox import norm_provincia


# Tope semanal combinado equivalente al tope mensual / 4 semanas. Se usa para
# acotar el plan por carga semanal de forma consistente con la regla de 480
# min/día (combinado).
_MAX_SEMANA_COMBINADO_MIN = cuota_dia() * 5
_MIN_SEMANA_COMBINADO_MIN = int(_MAX_SEMANA_COMBINADO_MIN * (min_semana() / cuota_semana()))


def _carga_combinada(servicio_min: float, n_visitas: int) -> float:
    """Servicio + colchón de desplazamiento estimado (n_visitas × 15 min)."""
    return float(servicio_min) + float(n_visitas) * travel_estimado_por_visita_plan_min()


def _target_por_limites_globales(combinado_total: float) -> int:
    """Cota inferior de mercadistas a partir del total combinado de minutos."""
    if combinado_total <= 0:
        return 1
    n_mes = max(1, math.ceil(combinado_total / cuota_mes()))
    min_m = max(1, int(math.ceil(combinado_total / _MAX_SEMANA_COMBINADO_MIN)))
    max_m = max(1, int(combinado_total // max(1, _MIN_SEMANA_COMBINADO_MIN)))
    n_sem = min_m if min_m > max_m else max_m
    return max(n_mes, n_sem)


def construir_plan_mercadistas_automatico(
    visit_instances: list[dict],
    min_mercadistas: int = 1,
) -> list[tuple[str, str | None]]:
    """
    Lista (nombre, provincia | None). Provincia acota la asignación como en modo Excel.

    min_mercadistas: piso (p. ej. desde columna HC); el total es max(piso, carga/límites).

    La carga de cada provincia se calcula como
        servicio_prov + n_visitas_prov × travel_estimado_por_visita_plan_min()
    y se compara contra cuota_mes() (9600 min/mercadista/mes).

    - Una sola provincia (o solo SIN_PROVINCIA): varios mercadistas con esa misma
      provincia para repartir carga dentro de la región.
    - Varias provincias: al menos ceil(carga_combinada_prov / MAX_COMBINADO_MES)
      mercadistas por provincia, más refuerzos hasta cumplir el objetivo global.
    """
    by_prov_serv: dict[str, float] = defaultdict(float)
    by_prov_visitas: dict[str, int] = defaultdict(int)
    for inst in visit_instances:
        p = norm_provincia(inst.get("provincia_punto", "")) or "SIN_PROVINCIA"
        t = float(inst.get("tiempo") or 0)
        if t <= 0:
            continue
        by_prov_serv[p] += t
        by_prov_visitas[p] += 1

    total_combinado = sum(
        _carga_combinada(by_prov_serv[p], by_prov_visitas[p])
        for p in by_prov_serv
    )
    n_target = max(
        _target_por_limites_globales(total_combinado),
        max(1, int(min_mercadistas)),
    )

    active = {p: by_prov_serv[p] for p in by_prov_serv if by_prov_serv[p] > 0}
    if not active:
        return [(f"Mercadista {i:02d}", None) for i in range(1, n_target + 1)]

    plan: list[tuple[str, str | None]] = []
    idx = 1

    if len(active) == 1:
        only_prov = next(iter(active.keys()))
        for _ in range(max(n_target, 1)):
            plan.append((f"Mercadista {idx:02d}", only_prov))
            idx += 1
        return plan

    # Carga combinada por provincia (servicio + colchón de viaje)
    combinado_por_prov = {
        p: _carga_combinada(active[p], by_prov_visitas[p]) for p in active
    }

    # Varias provincias: si ninguna supera un mes combinado y la suma de
    # "1 merc por provincia" dispara el número respecto al objetivo global,
    # usar pool compartido (varios mercs sin frontera).
    n_min_por_prov = sum(
        max(1, math.ceil(load / cuota_mes()))
        for load in combinado_por_prov.values()
    )
    todas_ligeras = all(load < cuota_mes() for load in combinado_por_prov.values())
    if todas_ligeras and n_min_por_prov > n_target:
        return [(f"Mercadista {i:02d}", None) for i in range(1, n_target + 1)]

    for prov in sorted(active.keys()):
        load = combinado_por_prov[prov]
        n_p = max(1, math.ceil(load / cuota_mes()))
        for _ in range(n_p):
            plan.append((f"Mercadista {idx:02d}", prov))
            idx += 1

    prov_mas_carga = max(active.keys(), key=lambda p: combinado_por_prov[p])
    while len(plan) < n_target:
        plan.append((f"Mercadista {idx:02d}", prov_mas_carga))
        idx += 1

    return plan
