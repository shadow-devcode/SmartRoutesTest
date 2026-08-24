"""
Excepciones de negocio y manejo centralizado.
Todas las respuestas de error pasan por aquí para formato consistente.
"""
import sys
import traceback

from flask import jsonify, request
from werkzeug.exceptions import HTTPException


# ---------------------------------------------------------------------------
# Excepciones de negocio (levantar desde services)
# ---------------------------------------------------------------------------

class AppException(Exception):
    """Base para excepciones de la aplicación."""

    def __init__(self, message: str, status_code: int = 400):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class UnauthorizedError(AppException):
    """401 - No autenticado o token inválido."""

    def __init__(self, message: str = "No autorizado"):
        super().__init__(message, status_code=401)


class ForbiddenError(AppException):
    """403 - Sin permiso para el recurso."""

    def __init__(self, message: str = "Acceso denegado"):
        super().__init__(message, status_code=403)


class NotFoundError(AppException):
    """404 - Recurso no encontrado."""

    def __init__(self, message: str = "Recurso no encontrado"):
        super().__init__(message, status_code=404)


class BadRequestError(AppException):
    """400 - Petición inválida."""

    def __init__(self, message: str = "Petición inválida"):
        super().__init__(message, status_code=400)


class TooManyRequestsError(AppException):
    """429 - Demasiados intentos (fuerza bruta)."""

    def __init__(self, message: str = "Demasiados intentos. Intenta más tarde."):
        super().__init__(message, status_code=429)


def register_error_handlers(app):
    """
    Registra manejadores de excepciones en la app Flask.
    Respuesta JSON uniforme: { "success": false, "error": "mensaje" }
    """

    @app.errorhandler(AppException)
    def handle_app_exception(e: AppException):
        return jsonify(success=False, error=e.message), e.status_code

    @app.errorhandler(HTTPException)
    def handle_http_exception(e: HTTPException):
        return jsonify(success=False, error=e.description or str(e)), e.code

    @app.errorhandler(400)
    def handle_bad_request(e):
        return jsonify(success=False, error="Petición inválida"), 400

    @app.errorhandler(401)
    def handle_unauthorized(e):
        return jsonify(success=False, error="No autorizado"), 401

    @app.errorhandler(403)
    def handle_forbidden(e):
        return jsonify(success=False, error="Acceso denegado"), 403

    @app.errorhandler(404)
    def handle_not_found(e):
        return jsonify(success=False, error="Recurso no encontrado"), 404

    @app.errorhandler(429)
    def handle_too_many_requests(e):
        return jsonify(success=False, error="Demasiados intentos. Intenta más tarde."), 429

    @app.errorhandler(500)
    def handle_internal_error(e):
        try:
            print(
                f"[ERROR] 500 en {request.method} {request.path}: {type(e).__name__}: {e}",
                file=sys.stderr,
                flush=True,
            )
            traceback.print_exc(file=sys.stderr)
        except Exception:
            pass
        return jsonify(success=False, error="Error interno del servidor"), 500

    @app.errorhandler(Exception)
    def handle_generic(e: Exception):
        try:
            print(
                f"[ERROR] Unhandled en {request.method} {request.path}: {type(e).__name__}: {e}",
                file=sys.stderr,
                flush=True,
            )
            traceback.print_exc(file=sys.stderr)
        except Exception:
            pass
        # En producción no exponer el mensaje interno
        msg = str(e) if app.debug else "Error interno del servidor"
        return jsonify(success=False, error=msg), 500
