"""
Rutas solo para rol ADMIN. Protegidas por middleware (ADMIN_PATH_PREFIXES).
Incluye CRUD de usuarios y endpoint de ejemplo dashboard.
"""
import os

from flask import Blueprint, jsonify, request, send_file
from pydantic import ValidationError

from exceptions.handlers import BadRequestError
from schemas.admin_user_schemas import AdminUserCreateRequest, AdminUserUpdateRequest
from services.user_admin_service import UserAdminService, _UNSET
from services.mercadista_assign_admin_service import (
    assign_mercadista_a_usuario,
    list_mercadistas_con_asignacion,
)
from services.route_dataset_service import RouteDatasetService, get_horarios_excel_path_for_management
from schemas.route_dataset_schemas import RouteDatasetActivateRequest
from models.route_dataset import RouteDataset
from models.user import User
from utils.logging import log_endpoint_error, safe_error_message

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


def _primer_error_validacion(e: ValidationError) -> str:
    """Primer mensaje de un ValidationError de Pydantic, sin el prefijo
    «Value error, » que añade Pydantic v2 a los field_validator."""
    errores = e.errors()
    if not errores:
        return "Datos inválidos"
    msg = str(errores[0].get("msg", "Datos inválidos"))
    return msg.removeprefix("Value error, ")


def _excel_context_mercadistas(session, actor_id, actor_role) -> dict:
    """Nombre e id del dataset cuyo horarios se editan en asignación mercadista (admin: activo; editor: el suyo)."""
    r = (actor_role or "").strip().upper()
    if r == "ADMIN":
        svc = RouteDatasetService(session)
        row = svc.repo.get_active()
        if row:
            return {"dataset_id": int(row.id), "display_name": row.display_name}
        return {"dataset_id": None, "display_name": None}
    if r == "EDITOR" and actor_id is not None:
        u = session.get(User, int(actor_id))
        ds_id = getattr(u, "assigned_route_dataset_id", None) if u else None
        if ds_id is not None:
            rd = session.get(RouteDataset, int(ds_id))
            if rd:
                return {"dataset_id": int(rd.id), "display_name": rd.display_name}
        return {
            "dataset_id": int(ds_id) if ds_id is not None else None,
            "display_name": None,
        }
    return {"dataset_id": None, "display_name": None}


@admin_bp.route("/dashboard", methods=["GET"])
def dashboard():
    """
    Ejemplo de endpoint solo ADMIN.
    Requiere: Authorization: Bearer <access_token> y role ADMIN.
    """
    user_id = getattr(request, "user_id", None)
    user_role = getattr(request, "user_role", None)
    return jsonify(
        success=True,
        message="Acceso permitido al panel de administración",
        data={
            "user_id": user_id,
            "role": user_role,
        },
    )


@admin_bp.route("/users", methods=["GET"])
def list_users():
    actor_role = getattr(request, "user_role", None)
    actor_id = getattr(request, "user_id", None)
    created_by_editor = request.args.get("created_by_editor", type=int)

    from database.connection import SessionLocal

    session = SessionLocal()
    try:
        service = UserAdminService(session)
        users = service.list_users(
            actor_role=actor_role,
            actor_user_id=int(actor_id) if actor_id is not None else None,
            created_by_editor_id=created_by_editor,
        )
        return jsonify(success=True, users=users)
    finally:
        session.close()


@admin_bp.route("/users", methods=["POST"])
def create_user():
    try:
        body = request.get_json() or {}
        data = AdminUserCreateRequest(**body)
    except ValidationError as e:
        raise BadRequestError(_primer_error_validacion(e))

    actor_role = getattr(request, "user_role", None)
    actor_id = getattr(request, "user_id", None)

    from database.connection import SessionLocal

    session = SessionLocal()
    try:
        service = UserAdminService(session)
        user = service.create_user(
            full_name=data.full_name,
            email=str(data.email),
            password=data.password,
            role=data.role,
            actor_role=actor_role,
            actor_user_id=int(actor_id) if actor_id is not None else None,
        )
        return jsonify(success=True, user=user), 201
    finally:
        session.close()


@admin_bp.route("/users/<int:user_id>", methods=["PUT"])
def update_user(user_id: int):
    try:
        raw = request.get_json()
        if raw is None:
            raise BadRequestError("Cuerpo JSON requerido")
        data = AdminUserUpdateRequest.model_validate(raw)
    except ValidationError as e:
        raise BadRequestError(_primer_error_validacion(e))

    updates = data.model_dump(exclude_unset=True, exclude_none=True)
    updates.pop("assigned_mercadista", None)

    actor_role = getattr(request, "user_role", None)
    if actor_role == "EDITOR":
        updates.pop("role", None)

    assigned_kw: object = _UNSET
    if "assigned_mercadista" in raw:
        av = raw.get("assigned_mercadista")
        if av is None:
            assigned_kw = None
        else:
            s = str(av).strip()
            assigned_kw = s or None

    assigned_ds_kw: object = _UNSET
    if actor_role == "ADMIN" and raw is not None and "assigned_route_dataset_id" in raw:
        av = raw.get("assigned_route_dataset_id")
        if av is None or av == "":
            assigned_ds_kw = None
        else:
            try:
                assigned_ds_kw = int(av)
            except (TypeError, ValueError):
                raise BadRequestError("assigned_route_dataset_id debe ser un número entero o null")

    if not updates and assigned_kw is _UNSET and assigned_ds_kw is _UNSET:
        raise BadRequestError("Envía al menos un campo para actualizar")

    actor_id = getattr(request, "user_id", None)
    if actor_id is None:
        raise BadRequestError("Sesión inválida")

    from database.connection import SessionLocal

    session = SessionLocal()
    try:
        service = UserAdminService(session)
        user = service.update_user(
            user_id,
            actor_user_id=int(actor_id),
            actor_role=actor_role,
            full_name=updates.get("full_name"),
            email=str(updates["email"]) if "email" in updates else None,
            password=updates.get("password"),
            role=updates.get("role"),
            is_active=updates.get("is_active"),
            assigned_mercadista=assigned_kw,
            assigned_route_dataset_id=assigned_ds_kw,
        )
        return jsonify(success=True, user=user)
    finally:
        session.close()


@admin_bp.route("/route-datasets", methods=["GET"])
def list_route_datasets():
    from database.connection import SessionLocal

    session = SessionLocal()
    try:
        svc = RouteDatasetService(session)
        return jsonify(success=True, datasets=svc.list_datasets())
    finally:
        session.close()


@admin_bp.route("/route-datasets/activate", methods=["POST"])
def activate_route_dataset():
    try:
        body = request.get_json() or {}
        data = RouteDatasetActivateRequest(**body)
    except ValidationError as e:
        raise BadRequestError(_primer_error_validacion(e))

    from database.connection import SessionLocal

    session = SessionLocal()
    try:
        svc = RouteDatasetService(session)
        row = svc.set_active(data.dataset_id)
        return jsonify(success=True, dataset=svc.to_public_dict(row))
    finally:
        session.close()


@admin_bp.route("/route-datasets/<int:dataset_id>", methods=["DELETE"])
def delete_route_dataset(dataset_id: int):
    if dataset_id < 1:
        raise BadRequestError("ID de dataset inválido")

    from database.connection import SessionLocal

    session = SessionLocal()
    try:
        svc = RouteDatasetService(session)
        svc.delete_dataset(dataset_id)
        return jsonify(success=True, message="Dataset eliminado")
    finally:
        session.close()


@admin_bp.route("/datasets/<int:dataset_id>/sync-blob", methods=["POST"])
def sync_blob_dataset(dataset_id: int):
    """
    Lee el Excel de disco del dataset indicado y almacena/actualiza su blob en BD.
    Útil para sincronizar datasets registrados antes de que existiera el blob.
    """
    from database.connection import SessionLocal

    session = SessionLocal()
    try:
        svc = RouteDatasetService(session)
        svc.sync_blob_for_dataset(dataset_id)
        return jsonify(
            success=True,
            message=f"Blob del dataset {dataset_id} sincronizado correctamente.",
        )
    finally:
        session.close()


@admin_bp.route("/horarios-excel", methods=["GET"])
def download_horarios_excel():
    """
    Descarga el Excel de rutas del dataset activo,
    con la columna Mercadista tal como quedó tras las asignaciones a usuarios.
    El nombre del archivo usa el display_name con el que se guardó el dataset,
    con sufijo "_actualizado" para distinguirlo de la descarga original.
    """
    from database.connection import SessionLocal
    from services.route_dataset_service import build_excel_download_name

    actor_role = getattr(request, "user_role", None)
    actor_id = getattr(request, "user_id", None)

    session = SessionLocal()
    try:
        abs_path = get_horarios_excel_path_for_management(
            session, actor_user_id=actor_id, actor_role=actor_role
        )
        download_name = build_excel_download_name(
            session,
            suffix="actualizado",
            fallback="minoristas_horarios_actualizado",
        )
    finally:
        session.close()
    if not os.path.isfile(abs_path):
        return jsonify(
            success=False,
            error="No hay archivo de rutas en el servidor. Procesa o sube un Excel primero.",
        ), 404

    from route_engine.excel_writer import build_horarios_excel_download_bytes

    try:
        bio = build_horarios_excel_download_bytes(abs_path)
    except Exception as exc:
        log_endpoint_error("GET /api/admin/.../descargar-resultado", exc)
        return jsonify(
            success=False,
            error=safe_error_message(exc, generic="No se pudo preparar el Excel"),
        ), 500

    return send_file(
        bio,
        as_attachment=True,
        download_name=download_name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@admin_bp.route("/mercadistas-asignacion", methods=["GET"])
def list_mercadistas_asignacion():
    actor_role = getattr(request, "user_role", None)
    actor_id = getattr(request, "user_id", None)

    from database.connection import SessionLocal

    session = SessionLocal()
    try:
        excel_path = get_horarios_excel_path_for_management(
            session, actor_user_id=actor_id, actor_role=actor_role
        )
        ds_scope = None
        if actor_role == "EDITOR" and actor_id is not None:
            eu = session.get(User, int(actor_id))
            ds_scope = eu.assigned_route_dataset_id if eu else None
        rows = list_mercadistas_con_asignacion(
            session, excel_path=excel_path, dataset_scope_id=ds_scope
        )
        excel_ctx = _excel_context_mercadistas(session, actor_id, actor_role)
        return jsonify(success=True, rows=rows, excel_context=excel_ctx)
    finally:
        session.close()


@admin_bp.route("/mercadistas-asignacion", methods=["PUT"])
def put_mercadista_asignacion():
    raw = request.get_json()
    if not raw:
        raise BadRequestError("Cuerpo JSON requerido")
    merc = raw.get("mercadista_actual")
    uid = raw.get("user_id")
    if merc is None or (isinstance(merc, str) and not str(merc).strip()):
        raise BadRequestError("mercadista_actual es obligatorio")
    user_id: int | None
    if uid is None or uid == "":
        user_id = None
    else:
        try:
            user_id = int(uid)
        except (TypeError, ValueError):
            raise BadRequestError("user_id debe ser un número o null")

    actor_role = getattr(request, "user_role", None)
    actor_id = getattr(request, "user_id", None)

    from database.connection import SessionLocal
    from models.user import User

    session = SessionLocal()
    try:
        excel_path = get_horarios_excel_path_for_management(
            session, actor_user_id=actor_id, actor_role=actor_role
        )
        ds_scope = None
        if actor_role == "EDITOR" and actor_id is not None:
            eu = session.get(User, int(actor_id))
            ds_scope = eu.assigned_route_dataset_id if eu else None
        out = assign_mercadista_a_usuario(
            session,
            str(merc).strip(),
            user_id,
            excel_path=excel_path,
            dataset_scope_id=ds_scope,
        )
        return jsonify(**out)
    finally:
        session.close()


@admin_bp.route("/users/<int:user_id>", methods=["DELETE"])
def delete_user(user_id: int):
    actor_id = getattr(request, "user_id", None)
    actor_role = getattr(request, "user_role", None)
    if actor_id is None:
        raise BadRequestError("Sesión inválida")

    from database.connection import SessionLocal

    session = SessionLocal()
    try:
        service = UserAdminService(session)
        service.delete_user(
            user_id, actor_user_id=int(actor_id), actor_role=actor_role
        )
        return jsonify(success=True, message="Usuario eliminado")
    finally:
        session.close()
