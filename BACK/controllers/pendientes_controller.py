"""Rutas HTTP de pendientes (principal y comparativa)."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from controllers.auth_state import is_auth_loaded
from services import pendientes_service as ps
from services.path_resolution_service import active_comparativa_path, active_horarios_path
from utils.logging import log_endpoint_error, safe_error_message

pendientes_bp = Blueprint("pendientes", __name__)


def _parse_filtros() -> tuple[str, str, str]:
    return (
        request.args.get("semana", "").strip(),
        request.args.get("provincia", "").strip(),
        request.args.get("ciudad", "").strip(),
    )


@pendientes_bp.route("/api/pendientes", methods=["GET"])
def get_pendientes():
    """Visitas sin asignar del Excel principal. Filtros: semana, provincia, ciudad."""
    try:
        hp = active_horarios_path(auth_loaded=is_auth_loaded())
        if not hp:
            return jsonify({"error": "Archivo no encontrado"}), 404
        semana, provincia, ciudad = _parse_filtros()
        return jsonify(ps.listar_pendientes(hp, semana=semana, provincia=provincia, ciudad=ciudad))
    except Exception as e:
        log_endpoint_error("GET /api/pendientes", e)
        return jsonify({"error": safe_error_message(e)}), 500


@pendientes_bp.route("/api/pendientes/resumen", methods=["GET"])
def get_pendientes_resumen():
    """
    Pendientes agrupados por punto de venta, para la pantalla de gestión.

    Cada fila trae descripción, frecuencia mensual, días en los que el punto ya
    se visita y cuántas visitas quedaron sin colocar.
    """
    try:
        hp = active_horarios_path(auth_loaded=is_auth_loaded())
        if not hp:
            return jsonify({"error": "Archivo no encontrado"}), 404
        _semana, provincia, ciudad = _parse_filtros()
        return jsonify(ps.resumen_pendientes(hp, provincia=provincia, ciudad=ciudad))
    except Exception as e:
        log_endpoint_error("GET /api/pendientes/resumen", e)
        return jsonify({"error": safe_error_message(e)}), 500


@pendientes_bp.route("/api/comparativa/pendientes", methods=["GET"])
def get_pendientes_comparativa():
    """Variante para el Excel comparativa."""
    try:
        cp = active_comparativa_path(auth_loaded=is_auth_loaded())
        if not cp:
            return jsonify({"error": "Archivo comparativa no encontrado"}), 404
        semana, provincia, ciudad = _parse_filtros()
        return jsonify(ps.listar_pendientes(cp, semana=semana, provincia=provincia, ciudad=ciudad))
    except Exception as e:
        log_endpoint_error("GET /api/comparativa/pendientes", e)
        return jsonify({"error": safe_error_message(e)}), 500
