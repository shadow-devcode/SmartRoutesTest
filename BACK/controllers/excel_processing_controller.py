"""
Rutas HTTP del procesamiento asíncrono de Excel: preview, procesar, upload
legacy, status, cancelar y descargar resultado.
"""
from __future__ import annotations

import os

from flask import Blueprint, g, jsonify, request, send_file
from werkzeug.utils import secure_filename

from controllers.auth_state import is_auth_loaded
from route_engine.excel_writer import build_horarios_excel_download_bytes
from services import excel_processing_service as eps
from services.path_resolution_service import active_horarios_path
from utils.logging import log_endpoint_error, safe_error_message
from utils.serialization import serializar_valor
from utils.uploads import (
    OUTPUT_FILE,
    UPLOAD_FOLDER,
    UnsafeExcelError,
    allowed_file,
    validar_xlsx_no_es_zip_bomb,
)

excel_bp = Blueprint("excel_processing", __name__)

# Valores aceptados para el modo de jornada que llega del front.
_MODOS_CON_DESPLAZAMIENTO = frozenset({"con_desplazamiento", "true", "1", "si", "sí", "yes"})
_MODOS_SIN_DESPLAZAMIENTO = frozenset({"sin_desplazamiento", "false", "0", "no"})


# Cuotas de jornada ofrecidas en la pantalla de carga. Solo estas: un valor
# libre desde el navegador dimensionaría la flota con una jornada que nadie ha
# acordado, así que lo que no esté en la lista se ignora y manda el defecto.
JORNADAS_PERMITIDAS_MIN = (480, 400)

# Tipos de carga aceptados. Se validan aquí para que un valor inventado no
# acabe repartiendo el trabajo con un criterio que nadie eligió.
TIPOS_CARGA_PERMITIDOS = ("zona", "ciudad", "cadena", "canal")


def _parse_tipo_carga(valor) -> str | None:
    """Criterio de reparto del formulario. None = el de por defecto ('zona')."""
    texto = str(valor or "").strip().lower()
    return texto if texto in TIPOS_CARGA_PERMITIDOS else None


def _parse_canal(valor) -> str:
    """Canal comercial elegido en el formulario. Cadena vacía = todos."""
    return str(valor or "").strip()[:120]


def _parse_cadenas(valor) -> list:
    """
    Cadenas elegidas. Se aceptan una o varias; vacío = todas las del canal.

    No se validan contra una lista fija: las cadenas salen del propio Excel, y
    el motor descarta las que no aparezcan en el archivo. Se acota el tamaño
    para que un cliente no pueda mandar una lista arbitrariamente larga.
    """
    if isinstance(valor, str):
        valor = [valor]
    if not isinstance(valor, (list, tuple)):
        return []
    limpias = []
    for item in valor[:50]:
        texto = str(item or "").strip()[:120]
        if texto and texto not in limpias:
            limpias.append(texto)
    return limpias


def _parse_minutos_jornada(valor) -> int | None:
    """Preset de jornada diaria del formulario. None = valor por defecto."""
    if valor is None:
        return None
    try:
        minutos = int(float(str(valor).strip()))
    except (TypeError, ValueError):
        return None
    return minutos if minutos in JORNADAS_PERMITIDAS_MIN else None


def _parse_incluir_desplazamiento(valor) -> bool | None:
    """
    Interpreta la opción "con / sin tiempo de desplazamiento" del formulario.

    Devuelve None si no viene o no se reconoce, para que el motor aplique el
    valor por defecto del servidor en vez de inventar un modelo: la diferencia
    entre uno y otro cambia el número de mercaderistas del resultado.
    """
    if valor is None:
        return None
    if isinstance(valor, bool):
        return valor
    texto = str(valor).strip().lower()
    if texto in _MODOS_CON_DESPLAZAMIENTO:
        return True
    if texto in _MODOS_SIN_DESPLAZAMIENTO:
        return False
    return None


def _prepare_dataset_output() -> tuple[str, bool, int | None]:
    """Devuelve (output_target, register_dataset, user_id) según auth disponible."""
    output_target = OUTPUT_FILE
    register_ds = False
    uid: int | None = None
    if is_auth_loaded():
        from database.connection import SessionLocal
        from services.route_dataset_service import RouteDatasetService

        s_prep = SessionLocal()
        try:
            _, output_target = RouteDatasetService(s_prep).prepare_new_output_paths()
        finally:
            s_prep.close()
        register_ds = True
        uid = getattr(g, "user_id", None)
    return output_target, register_ds, uid


@excel_bp.route("/api/preview-excel", methods=["POST"])
def preview_excel():
    """Sube Excel temporal, devuelve vista previa (columnas + filas) y deja la ruta lista para /procesar-excel."""
    import pandas as pd

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
        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, f"pending_{filename}")
        file.save(filepath)

        try:
            validar_xlsx_no_es_zip_bomb(filepath)
        except UnsafeExcelError as exc:
            os.remove(filepath)
            log_endpoint_error("POST /api/preview-excel:zip_bomb_check", exc)
            return jsonify({"success": False, "error": str(exc)}), 400

        df_preview = pd.read_excel(filepath)
        df_preview.columns = [str(c) for c in df_preview.columns]

        columnas = list(df_preview.columns)
        filas_raw = df_preview.to_dict("records")
        filas = [
            {col: serializar_valor(val) for col, val in fila.items()}
            for fila in filas_raw
        ]

        eps.set_pending_input_file(filepath)

        # Canales y cadenas presentes en ESTE archivo: el formulario ofrece
        # exactamente lo que hay dentro, sin listas escritas en el código.
        from route_engine.excel_reader import resumen_canales

        try:
            canales = resumen_canales(df_preview)
        except Exception as exc:  # un Excel raro no debe tumbar la vista previa
            log_endpoint_error("POST /api/preview-excel:canales", exc)
            canales = {}

        return jsonify({
            "success": True,
            "columnas": columnas,
            "filas": filas,
            "total_filas": len(df_preview),
            "nombre_archivo": filename,
            "canales": canales,
        })
    except Exception as exc:
        log_endpoint_error("POST /api/preview-excel", exc)
        return jsonify({"success": False, "error": safe_error_message(exc, generic="Error al leer el archivo")}), 500


@excel_bp.route("/api/procesar-excel", methods=["POST"])
def procesar_excel():
    """Inicia el procesamiento del archivo previamente cargado en /api/preview-excel."""
    if eps.is_processing():
        return jsonify({
            "success": False,
            "error": "Ya hay un procesamiento en curso. Por favor espera.",
        }), 400

    pendiente = eps.get_pending_input_file()
    if not pendiente or not os.path.exists(pendiente):
        return jsonify({
            "success": False,
            "error": "No hay ningún archivo listo para procesar. Sube el archivo primero.",
        }), 400

    eps.set_pending_input_file(None)  # consumir referencia

    body = request.get_json(silent=True) or {}
    raw_label = body.get("display_name") or body.get("display_label")
    display_label = str(raw_label).strip() if raw_label is not None else ""
    if not display_label:
        display_label = os.path.basename(pendiente) or "Dataset sin nombre"
    tipo_ruta = str(body.get("tipo_ruta") or "tiempo_completo").strip()
    incluir_desplazamiento = _parse_incluir_desplazamiento(
        body.get("modo_desplazamiento", body.get("incluir_tiempo_desplazamiento"))
    )
    minutos_jornada = _parse_minutos_jornada(body.get("minutos_jornada_dia"))
    tipo_carga = _parse_tipo_carga(body.get("tipo_carga"))
    # El alcance solo se acepta en los repartos que lo ofrecen en pantalla:
    # aceptarlo en los demás recortaría el archivo sin que nadie lo hubiera
    # pedido.
    if tipo_carga == "cadena":
        canal = _parse_canal(body.get("canal"))
        cadenas = _parse_cadenas(body.get("cadenas"))
    elif tipo_carga == "canal":
        # Repartiendo por canal se eligen los canales directamente: no hay un
        # nivel de cadena por debajo que acotar.
        canal = _parse_cadenas(body.get("canales"))
        cadenas = []
    else:
        canal, cadenas = "", []

    output_target, register_ds, uid = _prepare_dataset_output()

    eps.reset_processing_state()
    eps.launch_worker(
        pendiente,
        output_target,
        display_label=display_label,
        created_by_user_id=uid,
        register_dataset=register_ds,
        auth_loaded=is_auth_loaded(),
        tipo_ruta=tipo_ruta,
        incluir_tiempo_desplazamiento=incluir_desplazamiento,
        minutos_jornada_dia=minutos_jornada,
        tipo_carga=tipo_carga,
        canal=canal,
        cadenas=cadenas,
    )

    return jsonify({"success": True, "message": "Procesamiento iniciado correctamente."})


@excel_bp.route("/api/upload-excel", methods=["POST"])
def upload_excel():
    """Endpoint legacy: sube y procesa en un solo paso."""
    if eps.is_processing():
        return jsonify({
            "success": False,
            "error": "Ya hay un procesamiento en curso. Por favor espera.",
        }), 400

    if "file" not in request.files:
        return jsonify({"success": False, "error": "No se encontró el archivo en la petición"}), 400

    file = request.files["file"]
    if file.filename == "":
        return jsonify({"success": False, "error": "No se seleccionó ningún archivo"}), 400
    if not allowed_file(file.filename):
        return jsonify({
            "success": False,
            "error": "Tipo de archivo no permitido. Solo se aceptan archivos .xlsx o .xls",
        }), 400

    try:
        filename = secure_filename(file.filename)
        filepath = os.path.join(UPLOAD_FOLDER, filename)
        file.save(filepath)

        try:
            validar_xlsx_no_es_zip_bomb(filepath)
        except UnsafeExcelError as exc:
            os.remove(filepath)
            log_endpoint_error("POST /api/upload-excel:zip_bomb_check", exc)
            return jsonify({"success": False, "error": str(exc)}), 400

        output_target, register_ds, uid = _prepare_dataset_output()
        display_label = filename or "Dataset sin nombre"
        body_legacy = request.get_json(silent=True) or {}
        tipo_ruta = str(request.form.get("tipo_ruta") or body_legacy.get("tipo_ruta") or "tiempo_completo").strip()
        incluir_desplazamiento = _parse_incluir_desplazamiento(
            request.form.get("modo_desplazamiento")
            or body_legacy.get("modo_desplazamiento")
            or body_legacy.get("incluir_tiempo_desplazamiento")
        )
        minutos_jornada = _parse_minutos_jornada(
            request.form.get("minutos_jornada_dia") or body_legacy.get("minutos_jornada_dia")
        )
        tipo_carga = _parse_tipo_carga(
            request.form.get("tipo_carga") or body_legacy.get("tipo_carga")
        )

        eps.reset_processing_state()
        eps.launch_worker(
            filepath,
            output_target,
            display_label=display_label,
            created_by_user_id=uid,
            register_dataset=register_ds,
            auth_loaded=is_auth_loaded(),
            tipo_ruta=tipo_ruta,
            incluir_tiempo_desplazamiento=incluir_desplazamiento,
            minutos_jornada_dia=minutos_jornada,
            tipo_carga=tipo_carga,
        )

        return jsonify({
            "success": True,
            "message": "Archivo recibido. Procesamiento iniciado.",
            "filename": filename,
        })
    except Exception as exc:
        log_endpoint_error("POST /api/procesar-excel", exc)
        mensaje = safe_error_message(exc, generic="Error al guardar el archivo")
        eps.processing_status["is_processing"] = False
        eps.processing_status["error"] = mensaje
        return jsonify({
            "success": False,
            "error": mensaje,
        }), 500


@excel_bp.route("/api/processing-status", methods=["GET"])
def get_processing_status():
    return jsonify({"success": True, "status": eps.processing_status})


@excel_bp.route("/api/cancelar-procesamiento", methods=["POST"])
def cancelar_procesamiento():
    """
    Cancela el procesamiento en curso usando dos mecanismos combinados:
    1. threading.Event: detectado en el próximo _notify().
    2. ctypes.PyThreadState_SetAsyncExc: inyecta la excepción en el bytecode.
    """
    if not eps.request_cancel():
        return jsonify({"success": False, "error": "No hay ningún procesamiento activo."}), 400
    return jsonify({
        "success": True,
        "message": "Cancelación enviada. El proceso se detendrá en breve.",
    })


@excel_bp.route("/api/descargar-resultado", methods=["GET"])
def descargar_resultado():
    """
    Descarga el Excel de resultados generado por el procesamiento.

    Query params:
      - dataset_id (opcional): descarga ese dataset específico (útil tras procesar
        un Excel cuando ya hay otro activo; el frontend pasa
        processing_status.last_completed_dataset_id). Si se omite, descarga el
        dataset activo del usuario.
    """
    abs_path: str | None = None
    download_name = "rutas_generadas.xlsx"

    dataset_id_raw = request.args.get("dataset_id", "").strip()
    if dataset_id_raw and is_auth_loaded():
        try:
            dataset_id = int(dataset_id_raw)
        except ValueError:
            return jsonify({"error": "dataset_id debe ser un número entero"}), 400

        from database.connection import SessionLocal
        from services.route_dataset_service import RouteDatasetService, _sanitize_filename

        session = SessionLocal()
        try:
            svc = RouteDatasetService(session)
            row = svc.repo.get_by_id(dataset_id)
            if not row:
                return jsonify({"error": "Dataset no encontrado"}), 404
            if not svc.ensure_file_on_disk(row):
                return jsonify({
                    "error": "El archivo del dataset no existe en disco ni en la base de datos",
                }), 404
            abs_path = os.path.abspath(row.horarios_relative_path)
            download_name = f"{_sanitize_filename(row.display_name, fallback='rutas_generadas')}.xlsx"
        finally:
            session.close()
    else:
        abs_path = active_horarios_path(auth_loaded=is_auth_loaded())
        if is_auth_loaded():
            try:
                from services.route_dataset_service import build_excel_download_name

                download_name = build_excel_download_name(fallback="rutas_generadas")
            except Exception:
                pass

    if not abs_path:
        return jsonify({
            "error": "No hay archivo de resultados disponible. Ejecuta el procesamiento primero.",
        }), 404

    try:
        bio = build_horarios_excel_download_bytes(abs_path)
    except Exception as exc:
        log_endpoint_error("GET /api/descargar-resultado", exc)
        return jsonify({"error": safe_error_message(exc, generic="No se pudo preparar el Excel")}), 500

    return send_file(
        bio,
        as_attachment=True,
        download_name=download_name,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
