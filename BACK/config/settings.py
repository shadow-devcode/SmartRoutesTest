"""
Configuración centralizada desde variables de entorno.
Carga .env y expone valores tipados para la aplicación.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Cargar .env desde la raíz del backend (BACK/)
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=_env_path)


def _get_env(key: str, default: str | None = None) -> str:
    """Obtiene variable de entorno; fallback a default."""
    value = os.getenv(key, default)
    if value is None and default is None:
        raise ValueError(f"Variable de entorno requerida: {key}")
    return value or ""


def _get_env_int(key: str, default: int = 0) -> int:
    try:
        return int(os.getenv(key, default))
    except (TypeError, ValueError):
        return default


def _get_env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


# Valor placeholder que NUNCA debe usarse en producción.
_JWT_SECRET_PLACEHOLDER = "change-me-in-production"


class Settings:
    """Configuración de la aplicación."""

    # Entorno: "production" activa validaciones estrictas (secreto JWT obligatorio,
    # sin fail-open de auth). Cualquier otro valor se trata como desarrollo.
    APP_ENV: str = _get_env("APP_ENV", "development").strip().lower()

    # Escotilla de desarrollo: si la carga de auth falla, permitir que la API de
    # rutas/Excel funcione SIN autenticación. NUNCA debe ser true en producción.
    ALLOW_INSECURE_NO_AUTH: bool = _get_env_bool("ALLOW_INSECURE_NO_AUTH", False)

    @property
    def IS_PRODUCTION(self) -> bool:
        return self.APP_ENV == "production"

    # Base de datos MySQL
    DB_HOST: str = _get_env("DB_HOST", "localhost")
    DB_PORT: int = _get_env_int("DB_PORT", 3306)
    DB_USER: str = _get_env("DB_USER", "root")
    DB_PASSWORD: str = _get_env("DB_PASSWORD", "")
    DB_NAME: str = _get_env("DB_NAME", "smartroutes_auth")

    @property
    def DATABASE_URI(self) -> str:
        """URI de conexión MySQL para SQLAlchemy (protección SQL Injection: usar parámetros, no concatenar)."""
        return (
            f"mysql+pymysql://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    # JWT
    JWT_SECRET_KEY: str = _get_env("JWT_SECRET_KEY", _JWT_SECRET_PLACEHOLDER)
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = _get_env_int("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", 15)
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = _get_env_int("JWT_REFRESH_TOKEN_EXPIRE_DAYS", 7)

    # Segundos durante los que un refresh token recién rotado sigue valiendo.
    #
    # La cookie de sesión es una sola y el navegador puede pedir dos refreshes a
    # la vez: dos pestañas que se recargan, o una recarga mientras la petición
    # anterior aún viajaba. La primera rota el token; la segunda llega con el
    # viejo y, sin esta ventana, se interpretaba como robo y se cerraban TODAS
    # las sesiones del usuario. Ese era el motivo de que recargar la página te
    # devolviera al login.
    #
    # Pasada la ventana —o si el sucesor ya fue rotado a su vez, que es la
    # huella de un replay real— se mantiene la revocación completa.
    JWT_REFRESH_ROTATION_GRACE_SECONDS: int = _get_env_int(
        "JWT_REFRESH_ROTATION_GRACE_SECONDS", 30
    )
    JWT_ALGORITHM: str = _get_env("JWT_ALGORITHM", "HS256")

    # Fuerza bruta
    MAX_LOGIN_ATTEMPTS: int = _get_env_int("MAX_LOGIN_ATTEMPTS", 5)
    LOCKOUT_MINUTES: int = _get_env_int("LOCKOUT_MINUTES", 15)
    # Umbral por IP (más alto que por email: una IP con NAT puede tener varios
    # usuarios legítimos). 0 = desactivado.
    MAX_LOGIN_ATTEMPTS_PER_IP: int = _get_env_int("MAX_LOGIN_ATTEMPTS_PER_IP", 20)

    # Proxy / reverse-proxy: activar SOLO si la app corre detrás de un proxy de
    # confianza (nginx, balanceador, PaaS) que fija X-Forwarded-For. Si está en
    # false, se usa la IP real del socket (request.remote_addr) y NO se confía en
    # cabeceras (evita spoofing de IP para saltarse el rate-limit).
    TRUST_PROXY_HEADERS: bool = _get_env_bool("TRUST_PROXY_HEADERS", False)
    # Número de proxies de confianza encadenados (para ProxyFix). Normalmente 1.
    TRUSTED_PROXY_COUNT: int = _get_env_int("TRUSTED_PROXY_COUNT", 1)

    # CORS: lista de orígenes permitidos
    CORS_ORIGINS: list[str] = [
        x.strip() for x in _get_env("CORS_ORIGINS", "http://localhost:4200").split(",") if x.strip()
    ]

    # Cookies de sesión
    # COOKIE_SECURE=true en producción (requiere HTTPS). En desarrollo usar false.
    COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "false").lower() == "true"
    # COOKIE_SAMESITE: "Strict" en producción same-domain, "Lax" en desarrollo con proxy
    COOKIE_SAMESITE: str = _get_env("COOKIE_SAMESITE", "Lax")


def _validate_settings(s: "Settings") -> None:
    """
    Valida configuración sensible al arrancar.

    - En producción (APP_ENV=production) el secreto JWT debe estar definido y
      no puede ser el placeholder ni un valor trivial: de lo contrario cualquiera
      podría firmar tokens válidos. Se aborta el arranque (fail-closed).
    - En desarrollo solo se emite una advertencia para no bloquear el flujo local.
    """
    import sys

    weak_secret = (
        not s.JWT_SECRET_KEY
        or s.JWT_SECRET_KEY == _JWT_SECRET_PLACEHOLDER
        or len(s.JWT_SECRET_KEY) < 32
    )

    if weak_secret:
        msg = (
            "JWT_SECRET_KEY inseguro: define un valor aleatorio de >=32 caracteres "
            "en BACK/.env (p. ej. `python -c \"import secrets;print(secrets.token_urlsafe(48))\"`)."
        )
        if s.IS_PRODUCTION:
            raise ValueError(f"[SEGURIDAD] {msg}")
        print(f"[SEGURIDAD][AVISO] {msg}", file=sys.stderr)

    if s.IS_PRODUCTION and s.ALLOW_INSECURE_NO_AUTH:
        raise ValueError(
            "[SEGURIDAD] ALLOW_INSECURE_NO_AUTH no puede ser true en producción."
        )


# Instancia global
settings = Settings()
_validate_settings(settings)
