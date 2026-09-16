"""
Resolución de la ruta absoluta del Excel principal y de comparativa para el
request actual, según el rol del usuario y si auth está cargado.

Encapsula la lógica que antes vivía como `_active_horarios_path` y
`_active_comparativa_path` en api.py.
"""
from __future__ import annotations

import os

from utils.uploads import COMPARATIVA_FILE, OUTPUT_FILE


def active_horarios_path(*, auth_loaded: bool) -> str | None:
    """Excel principal según rol: activo global (admin) o dataset asignado (editor/visualizador/user)."""
    if not auth_loaded:
        p = os.path.abspath(OUTPUT_FILE)
        return p if os.path.isfile(p) else None
    try:
        from flask import g, has_request_context

        from database.connection import SessionLocal
        from services.route_dataset_service import (
            resolve_active_horarios_absolute,
            resolve_effective_horarios_absolute,
        )

        if not has_request_context():
            return resolve_active_horarios_absolute()
        uid = getattr(g, "user_id", None)
        role = getattr(g, "user_role", None)
        s = SessionLocal()
        try:
            return resolve_effective_horarios_absolute(s, user_id=uid, role=role)
        finally:
            s.close()
    except Exception:
        p = os.path.abspath(OUTPUT_FILE)
        return p if os.path.isfile(p) else None


def active_comparativa_path(*, auth_loaded: bool) -> str | None:
    """Excel de comparativa según rol; cae a archivo legacy si auth no está cargado."""
    if not auth_loaded:
        p = os.path.abspath(COMPARATIVA_FILE)
        return p if os.path.isfile(p) else None
    try:
        from flask import g, has_request_context

        from database.connection import SessionLocal
        from services.route_dataset_service import (
            resolve_active_comparativa_absolute,
            resolve_effective_comparativa_absolute,
        )

        if not has_request_context():
            return resolve_active_comparativa_absolute()
        uid = getattr(g, "user_id", None)
        role = getattr(g, "user_role", None)
        s = SessionLocal()
        try:
            return resolve_effective_comparativa_absolute(s, user_id=uid, role=role)
        finally:
            s.close()
    except Exception:
        p = os.path.abspath(COMPARATIVA_FILE)
        return p if os.path.isfile(p) else None


def excel_por_fuente(fuente: str | None, *, auth_loaded: bool) -> str | None:
    """Excel principal, o el de comparativa si `fuente` es 'comparativa'."""
    if (fuente or "").strip().lower() == "comparativa":
        return active_comparativa_path(auth_loaded=auth_loaded)
    return active_horarios_path(auth_loaded=auth_loaded)
