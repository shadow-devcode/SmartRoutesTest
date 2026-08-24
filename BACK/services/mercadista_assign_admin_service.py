"""
Asignación mercadista (Excel) ↔ usuario USER. Renombra la columna Mercadista en Horarios_Detalle.
"""
from __future__ import annotations

import os

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from exceptions.handlers import BadRequestError, ForbiddenError, NotFoundError
from models import User, Role
from route_engine.excel_writer import (
    drop_spurious_total_rows_horarios_df,
    format_horarios_detalle_worksheet,
)
from services.ruta_edit_service import _sanitizar_df_para_excel
from utils.excel_cache import invalidate_excel_cache
from utils.excel_lock import with_excel_file_lock

OUTPUT_HORARIOS = "minoristas_horarios.xlsx"
SHEET = "Horarios_Detalle"


def _norm(s: str | None) -> str:
    return (s or "").strip()


def _write_horarios_detalle(df: pd.DataFrame, path: str) -> None:
    df = drop_spurious_total_rows_horarios_df(df)
    sort_cols = [c for c in ["Mercadista", "Día", "Fecha", "Orden Ruta"] if c in df.columns]
    if sort_cols:
        df = df.sort_values(by=sort_cols)
    with pd.ExcelWriter(path, engine="openpyxl", mode="a", if_sheet_exists="replace") as writer:
        _sanitizar_df_para_excel(df).to_excel(writer, sheet_name=SHEET, index=False)
        try:
            format_horarios_detalle_worksheet(writer.book[SHEET])
        except Exception:
            pass
    invalidate_excel_cache(path)


def list_mercadistas_con_asignacion(
    session: Session,
    excel_path: str = OUTPUT_HORARIOS,
    *,
    dataset_scope_id: int | None = None,
) -> list[dict]:
    if not os.path.exists(excel_path):
        return []
    df = pd.read_excel(excel_path, sheet_name=SHEET)
    if "Mercadista" not in df.columns:
        return []
    df = drop_spurious_total_rows_horarios_df(df)
    mercs = sorted(
        {
            _norm(str(m))
            for m in df["Mercadista"].unique()
            if _norm(str(m)) and _norm(str(m)).upper() != "TOTAL"
        }
    )
    stmt = select(User).options(joinedload(User.role))
    users = list(session.scalars(stmt).unique().all())
    assign_map: dict[str, int] = {}
    for u in users:
        if (
            u.role
            and u.role.name == "USER"
            and u.is_active
            and u.assigned_mercadista
        ):
            if dataset_scope_id is not None and (getattr(u, "assigned_route_dataset_id", None) or 0) != dataset_scope_id:
                continue
            k = _norm(u.assigned_mercadista)
            if k:
                assign_map[k] = u.id
    return [{"mercadista": m, "user_id": assign_map.get(m)} for m in mercs]


@with_excel_file_lock("excel_path")
def assign_mercadista_a_usuario(
    session: Session,
    mercadista_actual: str,
    user_id: int | None,
    excel_path: str = OUTPUT_HORARIOS,
    *,
    dataset_scope_id: int | None = None,
) -> dict:
    ma = _norm(mercadista_actual)
    if not ma:
        raise BadRequestError("mercadista_actual es obligatorio")

    if not os.path.exists(excel_path):
        raise NotFoundError("Archivo Excel no encontrado en el servidor")

    df = pd.read_excel(excel_path, sheet_name=SHEET)
    if "Mercadista" not in df.columns:
        raise BadRequestError("La hoja no tiene columna Mercadista")
    df = drop_spurious_total_rows_horarios_df(df)

    mask_actual = df["Mercadista"].astype(str).map(_norm) == ma
    if not mask_actual.any():
        raise NotFoundError("Ese mercadista no existe en el Excel")

    df_orig = df.copy()

    if user_id is None:
        stmt = select(User).options(joinedload(User.role))
        for u in session.scalars(stmt).unique().all():
            if u.role and u.role.name == "USER" and _norm(u.assigned_mercadista) == ma:
                if dataset_scope_id is not None and (getattr(u, "assigned_route_dataset_id", None) or 0) != dataset_scope_id:
                    continue
                u.assigned_mercadista = None
        session.commit()
        return {"success": True, "mercadista": ma, "user_id": None, "nuevo_nombre_excel": ma}

    user = session.execute(
        select(User).where(User.id == user_id).options(joinedload(User.role))
    ).scalar_one_or_none()
    if not user or not user.role or user.role.name != "USER":
        raise BadRequestError("Solo puedes asignar rutas a usuarios con rol USER")
    if not user.is_active:
        raise BadRequestError("El usuario debe estar activo")
    if dataset_scope_id is not None and (getattr(user, "assigned_route_dataset_id", None) or 0) != dataset_scope_id:
        raise ForbiddenError("Ese usuario no pertenece al Excel de rutas que gestionas")

    new_label = _norm(user.full_name) or _norm(user.email)
    if not new_label:
        raise BadRequestError("El usuario no tiene nombre ni correo usable como etiqueta en el Excel")

    uniques = {_norm(str(x)) for x in df["Mercadista"].unique() if _norm(str(x)) and _norm(str(x)).upper() != "TOTAL"}
    if new_label != ma and new_label in uniques:
        raise BadRequestError(
            f"Ya existe en el Excel otro mercadista llamado «{new_label}». "
            "Elige otro usuario o cambia el nombre completo del usuario."
        )

    df.loc[mask_actual, "Mercadista"] = new_label

    stmt = select(User).options(joinedload(User.role))
    for u in session.scalars(stmt).unique().all():
        if not u.role or u.role.name != "USER":
            continue
        if dataset_scope_id is not None and (getattr(u, "assigned_route_dataset_id", None) or 0) != dataset_scope_id:
            continue
        am = _norm(u.assigned_mercadista)
        if am == ma or (am == new_label and u.id != user.id):
            u.assigned_mercadista = None

    user.assigned_mercadista = new_label

    try:
        session.flush()
        _write_horarios_detalle(df, excel_path)
        session.commit()
    except (PermissionError, OSError) as e:
        session.rollback()
        if isinstance(e, PermissionError) or getattr(e, "errno", None) == 13:
            raise BadRequestError(
                "No se puede escribir el Excel. Cierra «minoristas_horarios.xlsx» si está abierto e inténtalo de nuevo."
            ) from e
        raise
    except Exception:
        session.rollback()
        try:
            _write_horarios_detalle(df_orig, excel_path)
        except Exception:
            pass
        raise

    return {"success": True, "mercadista_anterior": ma, "nuevo_nombre_excel": new_label, "user_id": user.id}
