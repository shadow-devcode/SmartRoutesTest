"""
Application factory para Smart Routes.

Estructura del backend (clean-architecture lite):
    config/             configuración y settings desde .env
    database/           SQLAlchemy session/engine
    models/             entidades persistentes
    repositories/       acceso a datos
    services/           lógica de negocio (no conocen Flask)
    controllers/        blueprints HTTP (Flask)
    middleware/         autenticación
    exceptions/         excepciones y handlers
    utils/              helpers transversales (cache, geo, serialización)
    route_engine/       motor de generación de rutas (dominio externo)

Punto de entrada WSGI: `app:app` (importable como `from app import app`).
"""
from __future__ import annotations

# Cargar .env ANTES de cualquier import del proyecto: route_engine.config y
# config.settings leen variables (MAPBOX_ACCESS_TOKEN, JWT_SECRET_KEY, DB_*)
# al ser importados, así que dotenv debe correr primero o quedan en valores
# por defecto.
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

import sys

from flask import Flask
from flask_cors import CORS

from controllers.auth_state import set_auth_loaded
from controllers.comparativa_controller import comparativa_bp
from controllers.dashboard_controller import dashboard_bp
from controllers.excel_processing_controller import excel_bp
from controllers.health_controller import health_bp
from controllers.mercadistas_controller import mercadistas_bp
from controllers.pendientes_controller import pendientes_bp
from controllers.puntos_sin_coordenadas_controller import puntos_sin_coord_bp
from controllers.ruta_edit_controller import ruta_bp
from utils.uploads import ensure_upload_folder


def _bootstrap_auth(app: Flask) -> bool:
    """
    Carga config + BD + auth + admin blueprints.

    Sin .env, sin MySQL o sin dependencias, devolvemos False y la API de
    rutas/Excel sigue funcionando sin filtros por rol ni multi-dataset.
    """
    try:
        from config.settings import settings
        from database.connection import init_db
        from exceptions.handlers import register_error_handlers
        from middleware.auth_middleware import init_auth_middleware
        from models import Base

        register_error_handlers(app)

        # Detrás de un proxy de confianza (nginx/PaaS): hacer que request.remote_addr
        # y el esquema reflejen los del cliente real. Sin esto, X-Forwarded-For es
        # spoofable; con TRUST_PROXY_HEADERS=false NO se confía en esas cabeceras.
        if settings.TRUST_PROXY_HEADERS:
            from werkzeug.middleware.proxy_fix import ProxyFix

            n = max(1, settings.TRUSTED_PROXY_COUNT)
            app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

        engine = init_db(settings.DATABASE_URI, echo=False)
        Base.metadata.create_all(bind=engine)

        from controllers.admin_controller import admin_bp
        from controllers.auth_controller import auth_bp

        app.register_blueprint(auth_bp)
        app.register_blueprint(admin_bp)
        init_auth_middleware(app)
        CORS(app, origins=settings.CORS_ORIGINS, supports_credentials=True)

        print("Auth cargado: login en /api/auth/login")

        # Bootstrap del dataset legacy si la BD está vacía
        try:
            from database.connection import SessionLocal
            from services.route_dataset_service import RouteDatasetService

            s0 = SessionLocal()
            try:
                RouteDatasetService(s0).bootstrap_legacy_if_empty()
            finally:
                s0.close()
        except Exception as boot_exc:
            print(
                f"AVISO: no se pudo ejecutar bootstrap de route_dataset: {boot_exc}",
                file=sys.stderr,
            )
        return True

    except Exception as e:
        CORS(app)
        print(f"AVISO: Auth NO cargado. Login no funcionará. Causa: {e}", file=sys.stderr)
        if "No module named" in str(e) or "ModuleNotFoundError" in type(e).__name__:
            print("  -> Instala dependencias: pip install -r requirements.txt", file=sys.stderr)
        else:
            print("  - Crea BACK/.env (copia de .env.example) con DB_* y JWT_SECRET_KEY", file=sys.stderr)
            print("  - Asegúrate de que MySQL esté corriendo y la BD exista (init_db.sql)", file=sys.stderr)
        return False


def _install_no_auth_lockdown(app: Flask) -> None:
    """
    Si la autenticación NO se cargó, bloquea por defecto todos los endpoints
    /api/* (fail-closed) devolviendo 503. Antes, la API de rutas/Excel quedaba
    completamente abierta sin login ni filtros por rol.

    Escotilla de desarrollo: exporta ALLOW_INSECURE_NO_AUTH=true para permitir
    explícitamente el modo sin auth en local. Se lee de os.environ directamente
    porque config.settings puede no haber podido importarse.
    """
    import os

    allow_insecure = os.getenv("ALLOW_INSECURE_NO_AUTH", "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if allow_insecure:
        print(
            "[SEGURIDAD][AVISO] Auth no cargado y ALLOW_INSECURE_NO_AUTH=true: "
            "la API /api/* queda ABIERTA sin autenticación. Solo para desarrollo.",
            file=sys.stderr,
        )
        return

    from flask import jsonify, request

    @app.before_request
    def _block_api_without_auth():
        if request.method == "OPTIONS":
            return None
        if request.path.startswith("/api/"):
            return (
                jsonify(
                    success=False,
                    error=(
                        "Servicio no disponible: la autenticación no está "
                        "configurada en el servidor. Revisa BACK/.env y la base de datos."
                    ),
                ),
                503,
            )
        return None

    print(
        "[SEGURIDAD] Auth no cargado: API /api/* bloqueada (503). "
        "Configura .env/MySQL, o usa ALLOW_INSECURE_NO_AUTH=true en desarrollo.",
        file=sys.stderr,
    )


def _register_domain_blueprints(app: Flask) -> None:
    """Registra los blueprints de rutas/Excel/dashboard/comparativa/pendientes/health."""
    app.register_blueprint(mercadistas_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(excel_bp)
    app.register_blueprint(comparativa_bp)
    app.register_blueprint(pendientes_bp)
    app.register_blueprint(puntos_sin_coord_bp)
    app.register_blueprint(ruta_bp)
    app.register_blueprint(health_bp)


def create_app() -> Flask:
    """Factory de la aplicación Flask."""
    import os

    app = Flask(__name__)

    # Límite de tamaño del cuerpo de la petición (subidas de Excel). Evita DoS
    # por memoria/disco con archivos enormes. Configurable con MAX_UPLOAD_MB.
    max_mb = int(os.getenv("MAX_UPLOAD_MB", "25"))
    app.config["MAX_CONTENT_LENGTH"] = max_mb * 1024 * 1024

    ensure_upload_folder()
    auth_loaded = _bootstrap_auth(app)
    set_auth_loaded(auth_loaded)
    if not auth_loaded:
        _install_no_auth_lockdown(app)
    _register_domain_blueprints(app)

    return app


# Instancia WSGI usada por gunicorn / `flask run` / scripts.
app = create_app()


if __name__ == "__main__":
    import os

    # debug DESACTIVADO por defecto: el debugger de Werkzeug permite ejecución
    # remota de código si queda accesible. Actívalo solo en local con FLASK_DEBUG=true.
    debug = os.getenv("FLASK_DEBUG", "false").strip().lower() in ("1", "true", "yes", "on")
    # 127.0.0.1 por defecto: no exponer el servidor de desarrollo a toda la red.
    host = os.getenv("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_RUN_PORT", "5000"))
    print(f"Iniciando API en http://{host}:{port} (debug={debug})")
    app.run(debug=debug, host=host, port=port)
