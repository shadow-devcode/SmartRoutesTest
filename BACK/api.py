"""
Shim de compatibilidad: la app real está organizada por capas en `app.py` y
los blueprints viven en `controllers/`. Este módulo reexporta `app` para no
romper integraciones que aún importen `api:app` (gunicorn, scripts antiguos).

Para extender la API agrega un nuevo controller en `controllers/` y regístralo
en `app._register_domain_blueprints` (o en `_bootstrap_auth` si requiere auth).
"""
from app import app  # noqa: F401

__all__ = ["app"]


if __name__ == "__main__":
    # Misma configuración endurecida que app.py: debug y exposición de red
    # controlados por entorno, nunca activados por defecto.
    import os

    debug = os.getenv("FLASK_DEBUG", "false").strip().lower() in ("1", "true", "yes", "on")
    host = os.getenv("FLASK_RUN_HOST", "127.0.0.1")
    port = int(os.getenv("FLASK_RUN_PORT", "5000"))
    app.run(debug=debug, host=host, port=port)
