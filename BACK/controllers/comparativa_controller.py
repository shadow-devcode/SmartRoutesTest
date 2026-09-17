"""Rutas HTTP de comparativa: upload, mercadistas/ubicaciones/dia/stats."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from controllers.auth_state import is_auth_loaded
from services import comparativa_service as cs
from services import plantilla_semanal_service as pss
from services.path_resolution_service import active_comparativa_path, active_horarios_path
from utils.uploads import allowed_file
from utils.logging import log_endpoint_error, safe_error_message

comparativa_bp = Blueprint("comparativa", __name__, url_prefix="/api/comparativa")


@comparativa_bp.route("/upload-excel", methods=["POST"])
def upload_excel_comparativa():
    """
    Recibe un Excel ya procesado (con hoja Horarios_Detalle) y lo guarda
    como archivo comparativo del dataset activo, sin reprocesarlo.
    """
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No se encontró el archivo en la petición"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"success": False, "error": "No se seleccionó ningún archivo"}), 400
    if not allowed_file(file.filename):
        return jsonify({
            "success": False,
            "error": "Tipo de archivo no permitido. Solo se aceptan .xlsx o .xls",
        }), 400

    try:
        contenido = file.read()
    except Exception as exc:
        log_endpoint_error("POST /api/comparativa/upload-excel", exc)
        return jsonify({"success": False, "error": safe_error_message(exc, generic="Error al leer el archivo")}), 400

    try:
        cs.guardar_comparativa(contenido, auth_loaded=is_auth_loaded())
    except cs.ComparativaUploadError as exc:
        return jsonify({"success": False, "error": exc.message}), exc.status_code

    return jsonify({
        "success": True,
        "message": "Archivo comparativo cargado correctamente.",
    })


@comparativa_bp.route("/upload-plantilla", methods=["POST"])
def upload_plantilla_comparativa():
    """
    Recibe la plantilla semanal del cliente —una fila por punto con los días en
    columnas— y la convierte al formato interno para poder verla en el mapa
    comparativo, sin pedirle a nadie que reprocese nada.
    """
    if "file" not in request.files:
        return jsonify({"success": False, "error": "No se encontró el archivo en la petición"}), 400

    file = request.files["file"]
    if not file.filename:
        return jsonify({"success": False, "error": "No se seleccionó ningún archivo"}), 400
    if not allowed_file(file.filename):
        return jsonify({
            "success": False,
            "error": "Tipo de archivo no permitido. Solo se aceptan .xlsx o .xls",
        }), 400

    try:
        contenido = file.read()
    except Exception as exc:
        log_endpoint_error("POST /api/comparativa/upload-plantilla:leer", exc)
        return jsonify({
            "success": False,
            "error": safe_error_message(exc, generic="Error al leer el archivo"),
        }), 400

    try:
        df = pss.convertir_plantilla(contenido)
        # La plantilla identifica cada punto por su código SAP: se le pone el
        # nombre del local del dataset activo, emparejando por coordenadas.
        try:
            renombrados = pss.nombrar_puntos_con_el_dataset(
                df, active_horarios_path(auth_loaded=is_auth_loaded())
            )
        except Exception:
            renombrados = 0
        convertido = pss.plantilla_a_excel(df)
    except pss.PlantillaError as exc:
        return jsonify({"success": False, "error": exc.message}), 400
    except Exception as exc:
        log_endpoint_error("POST /api/comparativa/upload-plantilla:convertir", exc)
        return jsonify({
            "success": False,
            "error": safe_error_message(exc, generic="No se pudo convertir la plantilla"),
        }), 500

    try:
        cs.guardar_comparativa(convertido, auth_loaded=is_auth_loaded())
    except cs.ComparativaUploadError as exc:
        return jsonify({"success": False, "error": exc.message}), exc.status_code

    mercaderistas = int(df["Mercadista"].nunique()) if not df.empty else 0
    return jsonify({
        "success": True,
        "message": (
            f"Plantilla cargada: {len(df)} visita(s) de {mercaderistas} mercaderista(s), "
            "repartidas en las cuatro semanas."
            + (f" {renombrados} punto(s) con el nombre del local." if renombrados else "")
        ),
        "visitas": len(df),
        "mercadistas": mercaderistas,
    })


@comparativa_bp.route("/mercadistas", methods=["GET"])
def get_mercadistas_comparativa():
    try:
        cp = active_comparativa_path(auth_loaded=is_auth_loaded())
        if not cp:
            return jsonify({"error": "Archivo comparativa no encontrado"}), 404
        return jsonify(cs.listar_mercadistas(cp, auth_loaded=is_auth_loaded()))
    except Exception as e:
        log_endpoint_error("GET /api/comparativa/mercadistas", e)
        return jsonify({"error": safe_error_message(e)}), 500


@comparativa_bp.route("/mercadista/<mercadista_name>", methods=["GET"])
def get_mercadista_details_comparativa(mercadista_name):
    try:
        cp = active_comparativa_path(auth_loaded=is_auth_loaded())
        if not cp:
            return jsonify({"error": "Archivo comparativa no encontrado"}), 404
        semana = request.args.get("semana", "").strip()
        detalle = cs.detalle_mercadista(cp, mercadista_name, semana, auth_loaded=is_auth_loaded())
        if detalle is None:
            return jsonify({"error": "Mercadista no encontrado"}), 404
        return jsonify(detalle)
    except Exception as e:
        log_endpoint_error(f"GET /api/comparativa/mercadista/{mercadista_name}", e)
        return jsonify({"error": safe_error_message(e)}), 500


@comparativa_bp.route("/todas-ubicaciones", methods=["GET"])
def get_todas_ubicaciones_comparativa():
    try:
        cp = active_comparativa_path(auth_loaded=is_auth_loaded())
        if not cp:
            return jsonify({"error": "Archivo comparativa no encontrado"}), 404
        semana = request.args.get("semana", "").strip()
        return jsonify(cs.todas_ubicaciones(cp, semana, auth_loaded=is_auth_loaded()))
    except Exception as e:
        log_endpoint_error("GET /api/comparativa/todas-ubicaciones", e)
        return jsonify({"error": safe_error_message(e)}), 500


@comparativa_bp.route("/dia/<dia>", methods=["GET"])
def get_ubicaciones_por_dia_comparativa(dia):
    try:
        cp = active_comparativa_path(auth_loaded=is_auth_loaded())
        if not cp:
            return jsonify({"error": "Archivo comparativa no encontrado"}), 404
        semana = request.args.get("semana", "").strip()
        return jsonify(cs.ubicaciones_por_dia(cp, dia, semana, auth_loaded=is_auth_loaded()))
    except Exception as e:
        log_endpoint_error(f"GET /api/comparativa/dia/{dia}", e)
        return jsonify({"error": safe_error_message(e)}), 500


@comparativa_bp.route("/stats", methods=["GET"])
def get_stats_comparativa():
    try:
        cp = active_comparativa_path(auth_loaded=is_auth_loaded())
        if not cp:
            return jsonify({"error": "Archivo comparativa no encontrado"}), 404
        return jsonify(cs.stats(cp, auth_loaded=is_auth_loaded()))
    except Exception as e:
        log_endpoint_error("GET /api/comparativa/stats", e)
        return jsonify({"error": safe_error_message(e)}), 500
