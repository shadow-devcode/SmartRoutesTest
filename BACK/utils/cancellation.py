"""
Mecanismo de cancelación de procesamiento desde el endpoint HTTP.

Hereda de BaseException (no Exception) para que los bloques
'except Exception: pass' del route_engine no la absorban.
"""
from __future__ import annotations

import ctypes
import threading


class ProcesamientoCancelado(BaseException):
    """Excepción interna para señalizar cancelación por parte del usuario."""


def inyectar_excepcion_en_hilo(hilo: threading.Thread) -> bool:
    """
    Inyecta ProcesamientoCancelado directamente en el bytecode del hilo worker.

    Funciona en CPython independientemente de si el hilo está en _notify o no.
    La excepción se lanza en el próximo ciclo de bytecode del hilo.
    """
    tid = hilo.ident
    if tid is None or not hilo.is_alive():
        return False
    resultado = ctypes.pythonapi.PyThreadState_SetAsyncExc(
        ctypes.c_ulong(tid),
        ctypes.py_object(ProcesamientoCancelado),
    )
    if resultado > 1:
        # Más de un hilo afectado (nunca debería pasar): revertir
        ctypes.pythonapi.PyThreadState_SetAsyncExc(ctypes.c_ulong(tid), None)
        return False
    return resultado == 1
