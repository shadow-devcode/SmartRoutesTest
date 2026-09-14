"""
Rutas de autenticación: login, refresh, logout, me (usuario actual).

- Refresh token: cookie HttpOnly (path=/), no debe persistirse el access JWT en localStorage.
- Access token: solo en JSON de login/refresh para Authorization: Bearer en el cliente (memoria).
"""
from flask import Blueprint, request, jsonify, make_response
from pydantic import ValidationError

from config.settings import settings
from services.auth_service import AuthService
from schemas.auth_schemas import LoginRequest
from exceptions.handlers import BadRequestError, ForbiddenError, UnauthorizedError

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

# Nombre de la cookie. Path=/ para que el navegador la envíe en cualquier ruta
# del origen, también al restaurar la sesión tras recargar la página.
_COOKIE_NAME = "rt"
_COOKIE_PATH = "/"


def _get_client_ip() -> str | None:
    """
    IP del cliente para registrar intentos de login.

    Usa siempre request.remote_addr: detrás de un proxy de confianza, ProxyFix
    (activado con TRUST_PROXY_HEADERS) ya lo ha reescrito con la IP real del
    cliente. Si no hay proxy de confianza, NO se leen cabeceras X-Forwarded-*
    porque son falsificables y permitirían evadir el rate-limit por IP.
    """
    return (request.remote_addr or "").strip() or None


def _set_refresh_cookie(response, refresh_token: str) -> None:
    """Adjunta el refresh token como HttpOnly cookie a la respuesta."""
    response.set_cookie(
        _COOKIE_NAME,
        refresh_token,
        httponly=True,                                          # JS no puede leerla
        secure=settings.COOKIE_SECURE,                         # True en producción (HTTPS)
        samesite=settings.COOKIE_SAMESITE,                     # Lax en dev, Strict en prod
        max_age=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path=_COOKIE_PATH,
    )


def _replace_refresh_cookie(response, refresh_token: str) -> None:
    """Borra rt en todos los paths conocidos y fija una sola cookie (evita duplicados tras cambios de path)."""
    _clear_refresh_cookie(response)
    _set_refresh_cookie(response, refresh_token)


def _clear_refresh_cookie(response) -> None:
    """Elimina la cookie del refresh token (path actual y rutas legadas)."""
    response.delete_cookie(_COOKIE_NAME, path=_COOKIE_PATH)
    response.delete_cookie(_COOKIE_NAME, path="/api")
    response.delete_cookie(_COOKIE_NAME, path="/api/auth")


def _read_refresh_token() -> str | None:
    """Lee el refresh token de la cookie HttpOnly (única fuente válida)."""
    return request.cookies.get(_COOKIE_NAME)


# --- Defensa CSRF en profundidad (más allá de SameSite) --------------------
# /refresh y /logout son las únicas rutas autenticadas solo por cookie (`rt`);
# el resto exige `Authorization: Bearer`, que un sitio cross-origin no puede
# adjuntar. SameSite=Lax/Strict ya bloquea el envío de la cookie en POST
# cross-site en navegadores modernos, pero como capa adicional exigimos una
# cabecera personalizada: un <form>/<img> cross-site no puede agregarla, y
# fetch/XHR cross-origin que la incluya dispara un preflight CORS que nuestra
# política (orígenes explícitos, sin '*') rechaza para sitios no confiables.
_CSRF_HEADER_NAME = "X-Requested-With"
_CSRF_HEADER_VALUE = "XMLHttpRequest"


def _verificar_csrf_header() -> None:
    if request.headers.get(_CSRF_HEADER_NAME) != _CSRF_HEADER_VALUE:
        raise ForbiddenError("Falta cabecera de protección CSRF")


@auth_bp.route("/login", methods=["POST"])
def login():
    """
    Login: email + password.
    Devuelve access_token en el cuerpo JSON.
    El refresh_token se almacena en una cookie HttpOnly (invisible para JS).
    """
    try:
        body = request.get_json() or {}
        data = LoginRequest(**body)
    except ValidationError as e:
        raise BadRequestError(e.errors()[0].get("msg", "Datos inválidos") if e.errors() else "Datos inválidos")

    from database.connection import SessionLocal
    session = SessionLocal()
    try:
        service = AuthService(session)
        result = service.login(
            email=data.email,
            password=data.password,
            ip_address=_get_client_ip(),
        )
        response = make_response(jsonify(
            success=True,
            access_token=result["access_token"],
            token_type=result["token_type"],
            expires_in=result["expires_in"],
            user=result["user"],
            # refresh_token NO se incluye en el body: vive en la cookie
        ))
        _replace_refresh_cookie(response, result["refresh_token"])
        return response
    finally:
        session.close()


@auth_bp.route("/refresh", methods=["POST"])
def refresh():
    """
    Refresh: lee el refresh_token de la cookie HttpOnly y devuelve
    un nuevo access_token + rota la cookie.
    """
    _verificar_csrf_header()

    refresh_token = _read_refresh_token()
    if not refresh_token:
        raise UnauthorizedError("Sesión expirada. Inicia sesión de nuevo.")

    from database.connection import SessionLocal
    session = SessionLocal()
    try:
        service = AuthService(session)
        result = service.refresh(refresh_token=refresh_token)
        response = make_response(jsonify(
            success=True,
            access_token=result["access_token"],
            token_type=result["token_type"],
            expires_in=result["expires_in"],
            user=result["user"],
        ))
        _replace_refresh_cookie(response, result["refresh_token"])
        return response
    finally:
        session.close()


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """
    Logout: revoca los refresh tokens y elimina la cookie.
    """
    _verificar_csrf_header()

    from database.connection import SessionLocal
    session = SessionLocal()
    try:
        service = AuthService(session)
        refresh_token = _read_refresh_token()
        user_id = getattr(request, "user_id", None)
        service.logout(refresh_token=refresh_token, user_id=user_id)
        response = make_response(jsonify(success=True, message="Sesión cerrada"))
        _clear_refresh_cookie(response)
        return response
    finally:
        session.close()


@auth_bp.route("/me", methods=["GET"])
def me():
    """
    Devuelve el usuario actual (requiere Authorization: Bearer <access_token>).
    Ruta protegida por middleware.
    """
    user_id = getattr(request, "user_id", None)
    if not user_id:
        raise UnauthorizedError("Token requerido")

    from database.connection import SessionLocal
    session = SessionLocal()
    try:
        from repositories.user_repository import UserRepository
        repo = UserRepository(session)
        user = repo.get_by_id_with_role(user_id)
        if not user:
            raise UnauthorizedError("Usuario no encontrado")
        return jsonify(
            success=True,
            user={
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role.name,
                "assigned_mercadista": user.assigned_mercadista,
                "assigned_route_dataset_id": getattr(user, "assigned_route_dataset_id", None),
            },
        )
    finally:
        session.close()
