"""
Middleware de autenticación JWT y permisos por rol.

- Rutas públicas: login, refresh, logout, health, OPTIONS.
- Lectura mapa / stats / mercadistas: todos los roles autenticados.
- Dashboard (frecuencia, provincias): GET solo ADMIN, EDITOR, VISUALIZADOR.
- Carga y procesamiento de Excel, gestión de datasets: solo ADMIN.
- Comparativa y mutaciones de ruta (orden, mover visita): ADMIN o EDITOR.
- CRUD usuarios y asignación mercadista en /api/admin/users y mercadistas-asignacion: ADMIN o EDITOR.
"""
from flask import request, g

from exceptions.handlers import UnauthorizedError, ForbiddenError
from services.auth_service import AuthService

_KNOWN_ROLES = frozenset({"ADMIN", "USER", "EDITOR", "VISUALIZADOR"})


def _get_bearer_token() -> str | None:
    auth = request.headers.get("Authorization")
    if not auth or not auth.startswith("Bearer "):
        return None
    return auth[7:].strip()


def _is_options() -> bool:
    return request.method == "OPTIONS"


def _is_public_path(path: str) -> bool:
    if path == "/health":
        return True
    if path == "/api/auth/login" and request.method == "POST":
        return True
    if path == "/api/auth/refresh" and request.method == "POST":
        return True
    if path == "/api/auth/logout" and request.method == "POST":
        return True
    return False


def _path_allows_authenticated_user(path: str) -> bool:
    """Lectura del mapa principal y datos agregados (todos los roles autenticados)."""
    if path == "/api/auth/me":
        return True
    if path == "/api/mercadistas":
        return True
    if path.startswith("/api/mercadista/"):
        return True
    if path == "/api/todas-ubicaciones":
        return True
    # Rutas asignadas agrupadas por punto: es la misma información del mapa
    # vista de otra forma, así que el nivel de acceso es el mismo. El rol USER
    # queda filtrado a su propio mercaderista por `df_filtrar_mercadista_usuario`.
    if path == "/api/rutas-asignadas":
        return True
    if path.startswith("/api/dia/"):
        return True
    if path == "/api/stats":
        return True
    # Grupos de cadenas del reparto multicadena: es la misma información que ya
    # lleva cada fila (su CADENA), agrupada. Mismo nivel que /api/stats.
    if path == "/api/grupos-mercadistas":
        return True
    return False


def _path_admin_only_strict(path: str) -> bool:
    """Solo ADMIN: datasets, ejemplo /admin/dashboard, carga de archivos."""
    if path.startswith("/api/admin/route-datasets"):
        return True
    if path == "/api/admin/dashboard" or path.startswith("/api/admin/dashboard/"):
        return True
    if path == "/api/categorias":
        return True
    if path == "/api/preview-excel":
        return True
    if path == "/api/procesar-excel":
        return True
    if path == "/api/upload-excel":
        return True
    if path == "/api/processing-status":
        return True
    if path == "/api/cancelar-procesamiento":
        return True
    return False


def _path_admin_or_editor(path: str) -> bool:
    if path.startswith("/api/admin/users"):
        return True
    if path.startswith("/api/admin/mercadistas-asignacion"):
        return True
    # Descargar Excel actualizado: ADMIN (Excel activo global)
    # y EDITOR (su dataset asignado). La lógica interna del endpoint
    # resuelve qué archivo corresponde a cada rol.
    if path.startswith("/api/admin/horarios-excel"):
        return True
    return False


def _path_dashboard_read(path: str) -> bool:
    return request.method == "GET" and path.startswith("/api/dashboard/")


def _path_editor_or_admin_staff(path: str) -> bool:
    """EDITOR o ADMIN: comparativa, resultado, mutaciones de ruta, pendientes, unión de mercaderistas."""
    if path == "/api/dashboard/unir-mercadistas":
        return True
    if path.startswith("/api/comparativa"):
        return True
    if path == "/api/descargar-resultado":
        return True
    if path.startswith("/api/ruta/"):
        return True
    # Visitas pendientes (lectura, resumen de gestión y asignación manual): solo
    # ADMIN o EDITOR pueden ver el panel y operar sobre él. USER/VISUALIZADOR
    # quedan fuera. Se comprueba por prefijo para que las rutas nuevas bajo
    # /api/pendientes/... hereden el mismo nivel en vez de caer en 403.
    if path == "/api/pendientes" or path.startswith("/api/pendientes/"):
        return True
    # Puntos sin coordenadas (lectura y corrección): mismo nivel que pendientes.
    if path.startswith("/api/puntos-sin-coordenadas"):
        return True
    return False


def _path_requires_admin(path: str) -> bool:
    return path.startswith("/api/admin/")


def _requires_jwt(path: str) -> bool:
    if not path.startswith("/api/"):
        return False
    return not _is_public_path(path)


def init_auth_middleware(app):
    """
    Valida JWT en rutas /api protegidas y aplica reglas por rol.
    Establece g.user_id y g.user_role.
    """

    @app.before_request
    def auth_before_request():
        path = request.path

        if _is_options():
            return None

        if not _requires_jwt(path):
            return None

        token = _get_bearer_token()
        if not token:
            raise UnauthorizedError("Token de acceso requerido")

        from database.connection import SessionLocal

        session = SessionLocal()
        try:
            service = AuthService(session)
            payload = service.verify_access_token(token)
            raw_sub = payload.get("sub")
            try:
                g.user_id = int(raw_sub) if raw_sub is not None else None
            except (TypeError, ValueError):
                raise UnauthorizedError("Token inválido")
            g.user_role = payload.get("role")
            request.user_id = g.user_id
            request.user_role = g.user_role
        finally:
            session.close()

        role = g.get("user_role")
        if role not in _KNOWN_ROLES:
            raise ForbiddenError("Rol no reconocido")

        g.assigned_mercadista = None
        if role == "USER" and _path_allows_authenticated_user(path):
            from database.connection import SessionLocal
            from repositories.user_repository import UserRepository

            s2 = SessionLocal()
            try:
                ur = UserRepository(s2)
                u = ur.get_by_id(g.user_id)
                if u and u.assigned_mercadista:
                    g.assigned_mercadista = str(u.assigned_mercadista).strip() or None
            finally:
                s2.close()

        if _path_allows_authenticated_user(path):
            return None

        if _path_dashboard_read(path):
            if role in ("ADMIN", "EDITOR", "VISUALIZADOR"):
                return None
            raise ForbiddenError("No tienes permiso para ver el dashboard")

        if path.startswith("/api/ruta/") and request.method != "GET":
            if role not in ("ADMIN", "EDITOR"):
                raise ForbiddenError("Solo administrador o editor pueden modificar rutas")
            return None

        if _path_admin_only_strict(path):
            if role != "ADMIN":
                raise ForbiddenError("Se requieren permisos de administrador")
            return None

        if _path_admin_or_editor(path):
            if role not in ("ADMIN", "EDITOR"):
                raise ForbiddenError("Se requieren permisos de administrador o editor")
            return None

        if _path_requires_admin(path):
            if role != "ADMIN":
                raise ForbiddenError("Se requieren permisos de administrador")
            return None

        if _path_editor_or_admin_staff(path):
            if role not in ("ADMIN", "EDITOR"):
                raise ForbiddenError("Se requieren permisos de editor o administrador")
            return None

        raise ForbiddenError("Acceso denegado")


