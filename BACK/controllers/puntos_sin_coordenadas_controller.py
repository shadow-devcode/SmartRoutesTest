"""Endpoints para gestionar la hoja Puntos_Sin_Coordenadas."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from controllers.auth_state import is_auth_loaded
from services.path_resolution_service import active_horarios_path
from services.puntos_sin_coordenadas_service import (
    actualizar_y_mover_a_pendientes,
    leer_hoja_sin_coordenadas,
    sin_coord_to_json_list,
)
from utils.excel_lock import ExcelLockTimeout
from utils.logging import log_endpoint_error, safe_error_message

puntos_sin_coord_bp = Blueprint("puntos_sin_coordenadas", __name__)


@puntos_sin_coord_bp.route("/api/puntos-sin-coordenadas", methods=["GET"])
def get_puntos_sin_coordenadas():
    """Lista todos los puntos con coordenadas inválidas."""
    try:
        hp = active_horarios_path(auth_loaded=is_auth_loaded())
        if not hp:
            return jsonify({"error": "Archivo no encontrado"}), 404
        df = leer_hoja_sin_coordenadas(hp)
        puntos = sin_coord_to_json_list(df)
        return jsonify({"success": True, "puntos": puntos, "total": len(puntos)})
    except Exception as e:
        log_endpoint_error("GET /api/puntos-sin-coordenadas", e)
        return jsonify({"error": safe_error_message(e)}), 500


@puntos_sin_coord_bp.route("/api/puntos-sin-coordenadas/actualizar-coordenadas", methods=["PUT"])
def put_actualizar_coordenadas():
    """
    Geocodifica las nuevas coordenadas y mueve el punto a Pendientes_Sin_Asignar.

    Body JSON: { "descripcion": str, "latitud": float, "longitud": float }
    """
    try:
        data = request.get_json(silent=True) or {}

        descripcion = data.get("descripcion", "")
        if not isinstance(descripcion, str) or not descripcion.strip():
            return jsonify({"error": "El campo 'descripcion' es requerido"}), 400

        try:
            lat = float(data["latitud"])
            lon = float(data["longitud"])
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "Los campos 'latitud' y 'longitud' son requeridos y deben ser números"}), 400

        if not (-90.0 <= lat <= 90.0):
            return jsonify({"error": f"Latitud {lat} fuera del rango válido (-90 a 90)"}), 400
        if not (-180.0 <= lon <= 180.0):
            return jsonify({"error": f"Longitud {lon} fuera del rango válido (-180 a 180)"}), 400
        if lat == 0.0 and lon == 0.0:
            return jsonify({"error": "Las coordenadas no pueden ser ambas 0"}), 400

        hp = active_horarios_path(auth_loaded=is_auth_loaded())
        if not hp:
            return jsonify({"error": "Archivo no encontrado"}), 404

        resultado = actualizar_y_mover_a_pendientes(hp, descripcion, lat, lon)

        if not resultado.get("success"):
            return jsonify({"error": resultado.get("error", "Punto no encontrado")}), 404

        return jsonify(resultado)

    except ExcelLockTimeout as err:
        return jsonify({"error": err.message}), err.status_code
    except Exception as e:
        log_endpoint_error("PUT /api/puntos-sin-coordenadas/actualizar-coordenadas", e)
        return jsonify({"error": safe_error_message(e)}), 500
