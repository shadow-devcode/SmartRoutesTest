"""Rutas HTTP del dashboard (frecuencia de puntos y % por provincia)."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from controllers.auth_state import is_auth_loaded
from services import dashboard_service as ds
from services.path_resolution_service import active_horarios_path, excel_por_fuente
from utils.logging import log_endpoint_error, safe_error_message

dashboard_bp = Blueprint("dashboard_routes", __name__)


@dashboard_bp.route("/api/dashboard/frecuencia-puntos", methods=["GET"])
def get_frecuencia_puntos():
    try:
        hp = excel_por_fuente(request.args.get("fuente"), auth_loaded=is_auth_loaded())
        mercadista = request.args.get("mercadista", "").strip()
        return jsonify(ds.frecuencia_puntos(hp, mercadista))
    except Exception as e:
        log_endpoint_error("GET /api/dashboard/frecuencia-puntos", e)
        return jsonify({"success": False, "error": safe_error_message(e)}), 500


@dashboard_bp.route("/api/dashboard/provincias-porcentaje", methods=["GET"])
def get_provincias_porcentaje():
    try:
        hp = excel_por_fuente(request.args.get("fuente"), auth_loaded=is_auth_loaded())
        mercadista = request.args.get("mercadista", "").strip()
        provincia = request.args.get("provincia", "").strip()
        return jsonify(ds.provincias_porcentaje(hp, mercadista, provincia))
    except Exception as e:
        log_endpoint_error("GET /api/dashboard/provincias-porcentaje", e)
        return jsonify({"success": False, "error": safe_error_message(e)}), 500


@dashboard_bp.route("/api/dashboard/unir-mercadistas", methods=["POST"])
def unir_mercadistas():
    try:
        hp = active_horarios_path(auth_loaded=is_auth_loaded())
        data = request.get_json() or {}
        merc_origen = data.get("mercadista_origen", "").strip()
        merc_destino = data.get("mercadista_destino", "").strip()
        forzar = bool(data.get("forzar") or False)

        if not merc_origen or not merc_destino:
            return jsonify({"success": False, "error": "Debe especificar el mercaderista de origen y de destino."}), 400

        res = ds.unir_mercadistas(hp, merc_origen, merc_destino, forzar=forzar)
        if res.get("success"):
            return jsonify(res), 200
        # 409 cuando la unión rompe las reglas de rutas (dispersión o carga
        # mensual) pero el cliente puede reintentar con forzar=true; 400 para
        # errores de entrada que reintentar no arregla.
        status_code = 409 if res.get("union_invalida") else 400
        return jsonify(res), status_code
    except Exception as e:
        log_endpoint_error("POST /api/dashboard/unir-mercadistas", e)
        return jsonify({"success": False, "error": safe_error_message(e)}), 500
