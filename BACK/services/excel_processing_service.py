"""
Procesamiento asíncrono de archivos Excel: upload, preview, procesado en
background, status, cancelación y descarga del resultado.

Estado global del worker (procesamiento concurrente único) y archivo
pendiente de procesar son singletons a nivel de módulo porque el motor de
rutas se ejecuta en un único hilo daemon a la vez.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Optional

from utils.cancellation import ProcesamientoCancelado, inyectar_excepcion_en_hilo
from utils.logging import log_endpoint_error, safe_error_message

# ---------------------------------------------------------------------------
# Estado global del procesamiento (compartido entre hilo principal y worker)
# ---------------------------------------------------------------------------
processing_status: dict = {
    "is_processing": False,
    "progress": 0,
    "message": "",
    "error": None,
    "last_completed_dataset_id": None,
    "last_dataset_display_name": None,
}

# Evento de cancelación: se activa cuando el usuario solicita detener el proceso
_cancelar_procesamiento = threading.Event()

# Referencia al hilo worker activo (para inyección directa de excepción)
_worker_thread: Optional[threading.Thread] = None

# Ruta del archivo subido mediante /api/preview-excel, pendiente de procesar
_archivo_pendiente: Optional[str] = None


# ---------------------------------------------------------------------------
# Accesores del archivo pendiente (lo consume también pendientes_service)
# ---------------------------------------------------------------------------

def set_pending_input_file(path: Optional[str]) -> None:
    """Registra el último Excel subido por /api/preview-excel."""
    global _archivo_pendiente
    _archivo_pendiente = path


def get_pending_input_file() -> Optional[str]:
    """Devuelve el archivo pendiente de procesar (None si no hay)."""
    return _archivo_pendiente


def is_processing() -> bool:
    return bool(processing_status["is_processing"])


def reset_processing_state() -> None:
    """Reinicia el estado a 'iniciando'. Llamar antes de lanzar el worker."""
    processing_status["is_processing"] = True
    processing_status["progress"] = 0
    processing_status["message"] = "Iniciando el procesamiento..."
    processing_status["error"] = None
    processing_status["last_completed_dataset_id"] = None
    processing_status["last_dataset_display_name"] = None


# ---------------------------------------------------------------------------
# Hilo worker
# ---------------------------------------------------------------------------

def _actualizar_progreso(progress: int, message: str) -> None:
    """Callback de progreso invocado por procesar_minoristas.

    Lanza ProcesamientoCancelado si el usuario solicitó cancelar.
    """
    if _cancelar_procesamiento.is_set():
        raise ProcesamientoCancelado("Procesamiento cancelado por el usuario")
    processing_status["progress"] = progress
    processing_status["message"] = message


def _process_file_background(
    input_file: str,
    output_file: str,
    *,
    display_label: str | None = None,
    created_by_user_id: int | None = None,
    register_dataset: bool = False,
    auth_loaded: bool = False,
    tipo_ruta: str = "tiempo_completo",
    incluir_tiempo_desplazamiento: bool | None = None,
    minutos_jornada_dia: int | None = None,
    tipo_carga: str | None = None,
) -> None:
    """Ejecuta el procesamiento completo en un hilo separado."""
    global _worker_thread

    from route_engine import procesar_minoristas

    _cancelar_procesamiento.clear()
    _worker_thread = threading.current_thread()

    try:
        processing_status["message"] = "Iniciando el procesamiento..."
        processing_status["progress"] = 1

        procesar_minoristas(
            input_file,
            output_file,
            progress_callback=_actualizar_progreso,
            tipo_ruta=tipo_ruta,
            incluir_tiempo_desplazamiento=incluir_tiempo_desplazamiento,
            minutos_jornada_dia=minutos_jornada_dia,
            tipo_carga=tipo_carga,
        )

        processing_status["progress"] = 100
        processing_status["message"] = (
            "¡Proceso completado! Las rutas han sido generadas exitosamente."
        )
        processing_status["is_processing"] = False

        if register_dataset and display_label and auth_loaded:
            try:
                from database.connection import SessionLocal
                from services.route_dataset_service import RouteDatasetService

                s_reg = SessionLocal()
                try:
                    row = RouteDatasetService(s_reg).register_after_processing(
                        horarios_relative_path=output_file,
                        display_name=display_label,
                        created_by_user_id=created_by_user_id,
                    )
                    processing_status["last_completed_dataset_id"] = row.id
                    processing_status["last_dataset_display_name"] = row.display_name
                finally:
                    s_reg.close()
            except Exception as reg_exc:
                log_endpoint_error("worker:register_after_processing", reg_exc)
                detalle = safe_error_message(reg_exc, generic="no se pudo registrar el dataset")
                processing_status["error"] = f"Rutas generadas, pero {detalle}"

        time.sleep(5)
        if os.path.exists(input_file):
            try:
                os.remove(input_file)
            except OSError:
                pass

    except ProcesamientoCancelado:
        processing_status["is_processing"] = False
        processing_status["progress"] = 0
        processing_status["message"] = "El procesamiento fue cancelado."
        processing_status["error"] = "cancelled"
        if os.path.exists(input_file):
            try:
                os.remove(input_file)
            except OSError:
                pass

    except Exception as exc:
        log_endpoint_error("worker:procesar_excel_async", exc)
        processing_status["is_processing"] = False
        processing_status["error"] = safe_error_message(exc)
        processing_status["message"] = (
            "Ocurrió un error durante el procesamiento. "
            "Por favor revisa el archivo e intenta de nuevo."
        )

    except BaseException:
        # Red de seguridad: cualquier otro BaseException (ej. KeyboardInterrupt)
        processing_status["is_processing"] = False
        processing_status["error"] = "cancelled"
        processing_status["message"] = "El procesamiento fue interrumpido."

    finally:
        _worker_thread = None


def launch_worker(
    input_file: str,
    output_file: str,
    *,
    display_label: str | None,
    created_by_user_id: int | None,
    register_dataset: bool,
    auth_loaded: bool,
    tipo_ruta: str = "tiempo_completo",
    incluir_tiempo_desplazamiento: bool | None = None,
    minutos_jornada_dia: int | None = None,
    tipo_carga: str | None = None,
) -> None:
    """Lanza el procesamiento en un hilo daemon. El estado se debe haber reseteado antes."""
    thread = threading.Thread(
        target=_process_file_background,
        args=(input_file, output_file),
        kwargs={
            "display_label": display_label,
            "created_by_user_id": created_by_user_id,
            "register_dataset": register_dataset,
            "auth_loaded": auth_loaded,
            "tipo_ruta": tipo_ruta,
            "incluir_tiempo_desplazamiento": incluir_tiempo_desplazamiento,
            "minutos_jornada_dia": minutos_jornada_dia,
            "tipo_carga": tipo_carga,
        },
        daemon=True,
    )
    thread.start()


def request_cancel() -> bool:
    """Marca el evento de cancelación e inyecta la excepción en el worker.

    Devuelve True si había un proceso activo al solicitarse la cancelación.
    """
    if not processing_status["is_processing"]:
        return False
    _cancelar_procesamiento.set()
    if _worker_thread is not None:
        inyectar_excepcion_en_hilo(_worker_thread)
    return True
