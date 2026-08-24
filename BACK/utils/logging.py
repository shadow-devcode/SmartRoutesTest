"""Helpers de logging usados por los controllers."""
from __future__ import annotations

import sys
import traceback


def log_endpoint_error(endpoint: str, exc: BaseException) -> None:
    """Imprime traceback al stderr para diagnosticar fallos de endpoints (no afecta la respuesta JSON)."""
    print(f"[ERROR] {endpoint}: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    traceback.print_exc(file=sys.stderr)


def safe_error_message(exc: BaseException, *, generic: str = "Error interno del servidor") -> str:
    """
    Mensaje de error apto para devolver al cliente.

    En producción (APP_ENV=production) oculta el detalle interno (evita filtrar
    rutas, trazas o estructura). En desarrollo devuelve el detalle para depurar.
    Si no se puede determinar el entorno, asume producción (lo más seguro).
    """
    try:
        from config.settings import settings

        if settings.IS_PRODUCTION:
            return generic
    except Exception:
        return generic
    return str(exc)
