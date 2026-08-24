"""
Restricciones de visibilidad para usuarios con rol USER: solo pueden ver datos
del mercadista que tienen asignado (columna `Mercadista` en el Excel).
"""
from __future__ import annotations


def df_filtrar_mercadista_usuario(df, *, auth_loaded: bool):
    """
    Filtra el DataFrame según el rol del request actual.

    - Sin auth cargado: devuelve df sin cambios.
    - ADMIN, EDITOR, VISUALIZADOR: devuelve df sin cambios.
    - USER sin asignación: devuelve DataFrame vacío (mismas columnas).
    - USER con asignación: filtra por columna `Mercadista`.
    """
    if not auth_loaded:
        return df

    from flask import g  # importado lazy para no romper si auth no está cargado

    role = getattr(g, "user_role", None)
    if role not in ("USER",):
        return df
    scope = getattr(g, "assigned_mercadista", None)
    if not scope:
        return df.iloc[0:0].copy()
    if df is None or df.empty or "Mercadista" not in df.columns:
        return df
    mask = df["Mercadista"].astype(str).str.strip() == str(scope).strip()
    return df.loc[mask].copy()
