"""Rutas HTTP de edición de rutas: orden, mover-visita, mover-a-pendientes, asignar-pendiente."""
from __future__ import annotations

from functools import wraps

from flask import Blueprint, jsonify, request

from controllers.auth_state import is_auth_loaded
from route_engine.geo import parse_coordenada_a_float
from services import ruta_edit_service as res
from services.path_resolution_service import active_horarios_path
from utils.excel_lock import ExcelLockTimeout
from utils.logging import log_endpoint_error, safe_error_message

ruta_bp = Blueprint("ruta_edit", __name__, url_prefix="/api/ruta")

_EXCEL_LOCKED_MSG = (
    "No se puede escribir en el archivo Excel. "
    "Cierra 'minoristas_horarios.xlsx' si lo tienes abierto (por ejemplo en Excel) e intenta de nuevo."
)


def _handle_ruta_edit_error(err: res.RutaEditError):
    body: dict = {"success": False, "error": err.message}
    body.update(err.payload)
    return jsonify(body), err.status_code


def _maneja_errores_ruta_edit(endpoint_label: str):
    """Mapeo común de excepciones de los endpoints de edición de ruta:
    RutaEditError → status del negocio; archivo bloqueado por Excel → 503;
    resto → 500 con mensaje saneado (sin detalle interno en producción)."""

    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except res.RutaEditError as err:
                return _handle_ruta_edit_error(err)
            except ExcelLockTimeout as err:
                return jsonify({"error": err.message}), err.status_code
            except PermissionError:
                return jsonify({"error": _EXCEL_LOCKED_MSG}), 503
            except OSError as e:
                if getattr(e, "errno", None) == 13:
                    return jsonify({"error": _EXCEL_LOCKED_MSG}), 503
                log_endpoint_error(endpoint_label, e)
                return jsonify({"error": safe_error_message(e)}), 500
            except Exception as e:
                log_endpoint_error(endpoint_label, e)
                return jsonify({"error": safe_error_message(e)}), 500

        return wrapper

    return decorator


@ruta_bp.route("/orden", methods=["PUT"])
@_maneja_errores_ruta_edit("PUT /api/ruta/orden")
def actualizar_orden_ruta():
    """Reordena visitas y recalcula tiempo entre sucursales, km y horarios."""
    hp = active_horarios_path(auth_loaded=is_auth_loaded())
    if not hp:
        return jsonify({"error": "Archivo no encontrado"}), 404
    data = request.get_json()
    if not data or not data.get("mercadista") or not data.get("dia") or not data.get("ubicaciones"):
        return jsonify({"error": "Faltan mercadista, dia o ubicaciones"}), 400

    return jsonify(
        res.actualizar_orden_ruta(
            hp,
            mercadista=data["mercadista"],
            semana=(data.get("semana") or "").strip() or "semana 1",
            dia=data["dia"],
            ubicaciones=data["ubicaciones"],
        )
    )


@ruta_bp.route("/mover-a-pendientes", methods=["POST"])
@_maneja_errores_ruta_edit("POST /api/ruta/mover-a-pendientes")
def mover_a_pendientes():
    """Quita una o todas las visitas de un punto y las pasa a Pendientes_Sin_Asignar."""
    hp = active_horarios_path(auth_loaded=is_auth_loaded())
    if not hp:
        return jsonify({"error": "Archivo no encontrado"}), 404
    data = request.get_json() or {}
    merc_orig = (data.get("mercadista_origen") or "").strip()
    dia_orig = (data.get("dia_origen") or "").strip()
    visita = data.get("visita") or {}
    if not merc_orig or not dia_orig:
        return jsonify({"error": "Faltan mercadista_origen o dia_origen"}), 400
    if not visita.get("descripcion"):
        return jsonify({"error": "Falta visita.descripcion"}), 400

    return jsonify(
        res.mover_a_pendientes(
            hp,
            semana=(data.get("semana") or "").strip() or "semana 1",
            mercadista_origen=merc_orig,
            dia_origen=dia_orig,
            todas_las_visitas=bool(data.get("todas_las_visitas")),
            visita=visita,
        )
    )


@ruta_bp.route("/intercambiar-dias", methods=["PUT"])
@_maneja_errores_ruta_edit("PUT /api/ruta/intercambiar-dias")
def intercambiar_dias():
    """Intercambia las jornadas completas de dos días del mismo mercadista."""
    hp = active_horarios_path(auth_loaded=is_auth_loaded())
    if not hp:
        return jsonify({"error": "Archivo no encontrado"}), 404
    data = request.get_json()
    if not data:
        return jsonify({"error": "Body JSON requerido"}), 400

    mercadista = data.get("mercadista")
    dia_a = data.get("dia_a")
    dia_b = data.get("dia_b")
    semana = (data.get("semana") or "").strip() or "semana 1"
    if not mercadista or not dia_a or not dia_b:
        return jsonify({"error": "Faltan mercadista, dia_a o dia_b"}), 400

    return jsonify(
        res.intercambiar_dias(
            hp, semana=semana, mercadista=mercadista, dia_a=dia_a, dia_b=dia_b
        )
    )


@ruta_bp.route("/mover-visita", methods=["PUT"])
@_maneja_errores_ruta_edit("PUT /api/ruta/mover-visita")
def mover_visita():
    """Mueve una visita a otro día/semana del MISMO mercadista (no cambia mercadista)."""
    hp = active_horarios_path(auth_loaded=is_auth_loaded())
    if not hp:
        return jsonify({"error": "Archivo no encontrado"}), 404
    data = request.get_json()
    if not data:
        return jsonify({"error": "Body JSON requerido"}), 400

    semana = (data.get("semana") or "").strip() or "semana 1"
    semana_dest = (data.get("semana_destino") or "").strip() or semana
    merc_orig = data.get("mercadista_origen")
    dia_orig = data.get("dia_origen")
    merc_dest = data.get("mercadista_destino")
    dia_dest = data.get("dia_destino")
    try:
        orden_dest = int(data.get("orden_destino") or 1)
    except (TypeError, ValueError):
        orden_dest = 1
    visita = data.get("visita") or {}

    if not merc_orig or not dia_orig or not merc_dest or not dia_dest:
        return jsonify({
            "error": "Faltan mercadista_origen, dia_origen, mercadista_destino o dia_destino"
        }), 400
    if not visita or not visita.get("descripcion"):
        return jsonify({
            "error": "Falta objeto visita con descripcion (y latitud/longitud)"
        }), 400

    return jsonify(
        res.mover_visita(
            hp,
            semana=semana,
            semana_destino=semana_dest,
            mercadista_origen=merc_orig,
            dia_origen=dia_orig,
            mercadista_destino=merc_dest,
            dia_destino=dia_dest,
            orden_destino=orden_dest,
            visita=visita,
        )
    )


@ruta_bp.route("/asignar-pendiente", methods=["POST"])
@_maneja_errores_ruta_edit("POST /api/ruta/asignar-pendiente")
def asignar_pendiente():
    """Inserta una visita pendiente en una ruta destino, recalcula y persiste."""
    hp = active_horarios_path(auth_loaded=is_auth_loaded())
    if not hp:
        return jsonify({"error": "Archivo no encontrado"}), 404

    data = request.get_json() or {}
    merc_dest = (data.get("mercadista_destino") or "").strip()
    dia_dest = (data.get("dia_destino") or "").strip()
    forzar = bool(data.get("forzar") or False)
    try:
        orden_dest = int(data.get("orden_destino") or 0)
    except (TypeError, ValueError):
        orden_dest = 0
    visita = data.get("visita") or {}
    desc_v = (visita.get("descripcion") or "").strip()
    lat_v = parse_coordenada_a_float(visita.get("latitud"))
    lon_v = parse_coordenada_a_float(visita.get("longitud"))
    semana_visita = (visita.get("semana") or "").strip()
    semana_dest = (data.get("semana_destino") or "").strip() or semana_visita or "semana 1"

    if not merc_dest or not dia_dest:
        return jsonify({"error": "Faltan mercadista_destino o dia_destino"}), 400
    if not desc_v or lat_v is None or lon_v is None:
        return jsonify({"error": "Falta visita con descripcion, latitud y longitud válidas"}), 400

    try:
        tiempo_serv = float(visita.get("tiempo_servicio") or 0)
    except (TypeError, ValueError):
        tiempo_serv = 0.0

    return jsonify(
        res.asignar_pendiente(
            hp,
            mercadista_destino=merc_dest,
            dia_destino=dia_dest,
            semana_destino=semana_dest,
            orden_destino=orden_dest,
            forzar=forzar,
            desc_v=desc_v,
            lat_v=lat_v,
            lon_v=lon_v,
            semana_visita=semana_visita,
            tiempo_servicio_fallback=tiempo_serv,
            provincia_fallback=visita.get("provincia", "") or "",
        )
    )
