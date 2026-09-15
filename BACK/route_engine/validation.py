"""
Validaciones post-procesamiento.

La carga de un mercadista se mide en minutos COMBINADOS: "Tiempo Servicio
(min)" + "Tiempo entre sucursal (min)", que es lo que consume su jornada.
Reglas contra cuota_dia(), cuota_semana() y cuota_mes() (498 → 2.490 → 9.960).
"""

import pandas as pd

from route_engine.config import (
    dias_de_mercadista,
    carga_jornada,
    cuota_mes,
    cuota_semana,
    max_dia_flex,
    min_dia,
    min_mes,
    min_semana,
)
from route_engine.mapbox import norm_provincia


def validar_resultado(horarios_df, state):
    """
    Ejecuta validaciones estrictas: día, semana y mes contra cuota_dia(), cuota_semana() y cuota_mes().
    Solo cuenta "Tiempo Servicio (min)". Imprime advertencias si algo no cumple.
    """
    if horarios_df.empty or "Fecha" not in horarios_df.columns or "Mercadista" not in horarios_df.columns:
        return

    errores_validacion = []
    semanas_esperadas = {"semana 1", "semana 2", "semana 3", "semana 4"}

    if "Tiempo Servicio (min)" not in horarios_df.columns:
        return

    col_servicio = "Tiempo Servicio (min)"
    col_travel = "Tiempo entre sucursal (min)"

    # Todas las comprobaciones de carga van en minutos COMBINADOS (servicio +
    # desplazamiento), la misma unidad en la que se define la jornada de 8 h.
    # Medirlas en servicio puro contra topes de jornada hacía que casi ningún
    # día alcanzara el mínimo —el viaje no contaba— y el log se llenaba de
    # errores que no correspondían a ningún problema real.
    horarios_df = horarios_df.copy()
    horarios_df["_combinado"] = carga_jornada(
        pd.to_numeric(horarios_df[col_servicio], errors="coerce").fillna(0),
        pd.to_numeric(horarios_df.get(col_travel, 0), errors="coerce").fillna(0),
    )
    col_carga = "_combinado"

    # Mensual por mercadista
    total_mes_por_merc = horarios_df.groupby("Mercadista")[col_carga].sum()
    for merc, total in total_mes_por_merc.items():
        if total < min_mes():
            errores_validacion.append(
                f"{merc} bajo minimo mensual ({total:.0f} min, minimo {min_mes()})."
            )
        elif total > cuota_mes():
            errores_validacion.append(
                f"{merc} supera maximo mensual ({total:.0f} min, maximo {cuota_mes()})."
            )

    # Semanal por mercadista contra cuota_semana()
    for merc in horarios_df["Mercadista"].unique():
        for semana in semanas_esperadas:
            subset = horarios_df[
                (horarios_df["Mercadista"] == merc) & (horarios_df["Fecha"] == semana)
            ]
            if subset.empty:
                continue
            total_semana = subset[col_carga].sum()
            if total_semana < min_semana():
                errores_validacion.append(
                    f"{merc} {semana} bajo minimo semanal ({total_semana:.0f} min, minimo {min_semana()})."
                )
            elif total_semana > cuota_semana():
                errores_validacion.append(
                    f"{merc} {semana} supera maximo semanal ({total_semana:.0f} min, maximo {cuota_semana()})."
                )

    for merc in horarios_df["Mercadista"].unique():
        semanas_merc = set(horarios_df.loc[horarios_df["Mercadista"] == merc, "Fecha"].unique())
        if not semanas_esperadas.issubset(semanas_merc):
            faltan = sorted(semanas_esperadas - semanas_merc)
            errores_validacion.append(f"{merc} no tiene todas las semanas: faltan {faltan}.")

        for semana in semanas_esperadas:
            subset = horarios_df[(horarios_df["Mercadista"] == merc) & (horarios_df["Fecha"] == semana)]
            if subset.empty:
                continue
            dias_semana = set(subset["Día"].unique())
            # Cada mercaderista tiene SUS cinco días: la cuadrilla de fin de
            # semana no falta al trabajo por no aparecer un lunes.
            dias_esperados = set(dias_de_mercadista(merc))
            if dias_esperados - dias_semana:
                faltan_dias = sorted(dias_esperados - dias_semana)
                errores_validacion.append(
                    f"{merc} semana {semana} no trabaja todos los dias: faltan {faltan_dias}."
                )

    # Diario por mercadista/día/fecha, en minutos combinados
    tot_dia = horarios_df.groupby(["Mercadista", "Día", "Fecha"])[col_carga].sum()
    for (merc, dia, fecha), total in tot_dia.items():
        if total < min_dia():
            errores_validacion.append(
                f"{merc} {dia} {fecha} bajo minimo diario ({total:.0f} min, minimo {min_dia()})."
            )
        elif total > max_dia_flex():
            errores_validacion.append(
                f"{merc} {dia} {fecha} excede maximo diario ({total:.0f} min, maximo {max_dia_flex()})."
            )

    if errores_validacion:
        errores_texto = "\n".join(f"- {e}" for e in errores_validacion)
        print("      [!] Validaciones de mercadistas no cumplen:\n" + errores_texto)


def recolectar_pendientes(state):
    """
    Recopila puntos pendientes que no fueron asignados y los imprime.
    """
    pendientes_keys = set()
    for p in state.puntos_pendientes:
        pendientes_keys.add(
            (p.get("descripcion", ""), p.get("lat"), p.get("lon"), p.get("semana", ""))
        )
    for lst in state.remaining_by_prov.values():
        for inst in lst:
            key = (
                inst.get("descripcion", ""),
                inst.get("lat"),
                inst.get("lon"),
                inst.get("semana_num") or "todas",
            )
            if key in pendientes_keys:
                continue
            pendientes_keys.add(key)
            state.puntos_pendientes.append({
                "motivo": "pendiente sin asignacion",
                "descripcion": inst.get("descripcion", ""),
                "lat": inst.get("lat"),
                "lon": inst.get("lon"),
                "semana": inst.get("semana_num") or "todas",
                "tiempo": inst.get("tiempo", 0),
                "frecuencia_mes": inst.get("frecuencia_mes"),
                "provincia_punto": inst.get("provincia_punto", ""),
            })

    if state.puntos_pendientes:
        print("      [!] Puntos pendientes sin asignar:")
        for p in state.puntos_pendientes:
            desc = p.get("descripcion", "")
            semana = p.get("semana", "")
            lat = p.get("lat", "")
            lon = p.get("lon", "")
            motivo = p.get("motivo", "")
            print(f"         - {desc} | semana {semana} | ({lat}, {lon}) | {motivo}")
