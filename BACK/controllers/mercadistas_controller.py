"""
Rutas HTTP de consulta de mercadistas, ubicaciones, día, stats y categorías.

Cada handler es lo más delgado posible: validación de path/query → servicio →
serialización. Conserva los mismos códigos de estado y forma de respuesta que
el api.py original (incluso `{"error": ...}` en lugar de `success: false`).
"""
from __future__ import annotations

from flask import Blueprint, g, jsonify, request

from controllers.auth_state import is_auth_loaded
from services import mercadistas_query_service as mq
from services.path_resolution_service import active_horarios_path
from utils.logging import log_endpoint_error, safe_error_message

mercadistas_bp = Blueprint("mercadistas", __name__)


@mercadistas_bp.route("/api/mercadistas", methods=["GET"])
def get_mercadistas():
    try:
        hp = active_horarios_path(auth_loaded=is_auth_loaded())
        if not hp:
            return jsonify({"error": "Archivo no encontrado. Ejecuta main.py primero"}), 404
        return jsonify(mq.listar_mercadistas(hp, auth_loaded=is_auth_loaded()))
    except Exception as e:
        log_endpoint_error("GET /api/mercadistas", e)
        return jsonify({"error": safe_error_message(e)}), 500


@mercadistas_bp.route("/api/mercadista/<mercadista_name>", methods=["GET"])
def get_mercadista_details(mercadista_name):
    try:
        hp = active_horarios_path(auth_loaded=is_auth_loaded())
        if not hp:
            return jsonify({"error": "Archivo no encontrado"}), 404

        if is_auth_loaded() and getattr(g, "user_role", None) == "USER":
            scope = getattr(g, "assigned_mercadista", None)
            if not scope or str(mercadista_name).strip() != str(scope).strip():
                return jsonify({"success": False, "error": "Acceso denegado"}), 403

        semana = request.args.get("semana", "").strip()
        detalle = mq.detalle_mercadista(
            hp, mercadista_name, semana, auth_loaded=is_auth_loaded()
        )
        if detalle is None:
            return jsonify({"error": "Mercadista no encontrado"}), 404
        return jsonify(detalle)
    except Exception as e:
        return jsonify({"error": safe_error_message(e)}), 500


@mercadistas_bp.route("/api/rutas-asignadas", methods=["GET"])
def get_rutas_asignadas():
    """
    Rutas ya asignadas, agrupadas por punto de venta.

    Contraparte de /api/pendientes/resumen: mismo formato de fila (descripción,
    frecuencia, días de visita) pero para lo que SÍ está colocado. Respeta el
    filtro por mercaderista del rol USER.
    """
    try:
        hp = active_horarios_path(auth_loaded=is_auth_loaded())
        if not hp:
            return jsonify({"error": "Archivo no encontrado"}), 404
        return jsonify(
            mq.resumen_rutas(
                hp,
                auth_loaded=is_auth_loaded(),
                mercadista=request.args.get("mercadista", "").strip(),
                provincia=request.args.get("provincia", "").strip(),
                ciudad=request.args.get("ciudad", "").strip(),
            )
        )
    except Exception as e:
        log_endpoint_error("GET /api/rutas-asignadas", e)
        return jsonify({"error": safe_error_message(e)}), 500


@mercadistas_bp.route("/api/todas-ubicaciones", methods=["GET"])
def get_todas_ubicaciones():
    try:
        hp = active_horarios_path(auth_loaded=is_auth_loaded())
        if not hp:
            return jsonify({"error": "Archivo no encontrado"}), 404
        semana = request.args.get("semana", "").strip()
        return jsonify(mq.todas_ubicaciones(hp, semana, auth_loaded=is_auth_loaded()))
    except Exception as e:
        log_endpoint_error("GET /api/todas-ubicaciones", e)
        return jsonify({"error": safe_error_message(e)}), 500


@mercadistas_bp.route("/api/dia/<dia>", methods=["GET"])
def get_ubicaciones_por_dia(dia):
    try:
        hp = active_horarios_path(auth_loaded=is_auth_loaded())
        if not hp:
            return jsonify({"error": "Archivo no encontrado"}), 404
        semana = request.args.get("semana", "").strip()
        return jsonify(
            mq.ubicaciones_por_dia(hp, dia, semana, auth_loaded=is_auth_loaded())
        )
    except Exception as e:
        return jsonify({"error": safe_error_message(e)}), 500


@mercadistas_bp.route("/api/stats", methods=["GET"])
def get_stats():
    try:
        hp = active_horarios_path(auth_loaded=is_auth_loaded())
        if not hp:
            return jsonify({"error": "Archivo no encontrado"}), 404
        return jsonify(mq.stats(hp, auth_loaded=is_auth_loaded()))
    except Exception as e:
        log_endpoint_error("GET /api/stats", e)
        return jsonify({"error": safe_error_message(e)}), 500


@mercadistas_bp.route("/api/categorias", methods=["GET"])
def get_categorias():
    try:
        hp = active_horarios_path(auth_loaded=is_auth_loaded())
        if not hp:
            return jsonify({"error": "Archivo no encontrado"}), 404
        return jsonify(mq.categorias(hp))
    except Exception as e:
        return jsonify({"error": safe_error_message(e)}), 500
