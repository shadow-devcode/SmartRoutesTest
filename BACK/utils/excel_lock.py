"""
Lock de archivo por path para los ciclos leer→modificar→escribir de Excel.

Dos requests concurrentes que editan el mismo Excel (dos editores, doble
click) se pisaban: el segundo escribía sobre una lectura obsoleta y el cambio
del primero desaparecía sin error (lost update). El invariante
«agendadas + pendientes == frecuencia» quedaba roto silenciosamente.

Se usa lock del sistema operativo (no threading.Lock) para que la exclusión
funcione también entre procesos (gunicorn con varios workers):
  - POSIX: fcntl.flock sobre un fichero sidecar `<path>.lock`.
  - Windows: msvcrt.locking sobre el mismo sidecar.

El sidecar nunca se borra al liberar: eliminarlo permitiría que un proceso
mantuviera el lock de un inode ya borrado mientras otro crea y bloquea un
archivo nuevo (dos «exclusivos» a la vez). Quedan ficheros .lock vacíos junto
a los .xlsx; son inofensivos.

Anti-deadlock: adquisición no bloqueante con reintentos hasta un deadline.
Cada caso de uso adquiere exactamente UN lock (los helpers compartidos no
bloquean), así que no existe orden de adquisición que pueda cruzarse.
"""
from __future__ import annotations

import inspect
import os
import sys
import time
from contextlib import contextmanager
from functools import wraps

from exceptions.handlers import AppException

_DEFAULT_TIMEOUT_S = float(os.getenv("EXCEL_LOCK_TIMEOUT_S", "30"))
_POLL_INTERVAL_S = 0.1


class ExcelLockTimeout(AppException):
    """503 - Otra operación mantiene bloqueado el Excel y no terminó a tiempo."""

    def __init__(self, path: str, timeout_s: float):
        super().__init__(
            (
                f"No se pudo editar «{os.path.basename(path)}»: otra operación de "
                f"edición sigue en curso tras {timeout_s:.0f}s. Intenta de nuevo."
            ),
            status_code=503,
        )


if sys.platform == "win32":
    import msvcrt

    def _try_lock(fd: int) -> bool:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass

else:
    import fcntl

    def _try_lock(fd: int) -> bool:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


@contextmanager
def excel_file_lock(path: str, timeout_s: float = _DEFAULT_TIMEOUT_S):
    """Lock exclusivo inter-proceso sobre el Excel `path`.

    Debe envolver el ciclo COMPLETO leer→modificar→escribir: bloquear solo la
    escritura no evita el lost update (la lectura obsoleta es el problema).
    """
    lock_path = os.path.abspath(str(path)) + ".lock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    acquired = False
    try:
        deadline = time.monotonic() + timeout_s
        while not acquired:
            if _try_lock(fd):
                acquired = True
                break
            if time.monotonic() >= deadline:
                raise ExcelLockTimeout(path, timeout_s)
            time.sleep(_POLL_INTERVAL_S)
        yield
    finally:
        if acquired:
            _unlock(fd)
        os.close(fd)


def with_excel_file_lock(path_param: str, timeout_s: float = _DEFAULT_TIMEOUT_S):
    """Decorador: ejecuta la función con `excel_file_lock` sobre el argumento
    `path_param` (resuelto por nombre, posicional o keyword, con defaults).

    Aplicar SOLO a casos de uso de nivel superior, nunca a helpers que un caso
    de uso ya bloqueado pueda invocar (el lock no es reentrante)."""

    def decorator(fn):
        sig = inspect.signature(fn)

        @wraps(fn)
        def wrapper(*args, **kwargs):
            bound = sig.bind(*args, **kwargs)
            bound.apply_defaults()
            path = bound.arguments[path_param]
            with excel_file_lock(path, timeout_s=timeout_s):
                return fn(*args, **kwargs)

        return wrapper

    return decorator
