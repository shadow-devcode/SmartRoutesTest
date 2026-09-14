"""
Servicio de autenticación: login, refresh, logout, verificación de fuerza bruta.
Contraseñas con BCrypt; tokens JWT.
"""
import hashlib
import secrets
import sys
import time
from datetime import datetime, timedelta
from typing import Any

import bcrypt
import jwt
from sqlalchemy.orm import Session

from config.settings import settings
from exceptions.handlers import (
    UnauthorizedError,
    BadRequestError,
    TooManyRequestsError,
)
from models import User
from repositories.user_repository import UserRepository
from repositories.refresh_token_repository import RefreshTokenRepository
from repositories.login_attempt_repository import LoginAttemptRepository


# Hash -> instante (monotónico) en que ese refresh token fue rotado. Ver
# `_es_carrera_de_rotacion`: distingue dos peticiones legítimas simultáneas de
# un replay tardío. Se limpia solo, y no sobrevive a un reinicio a propósito.
_ROTADOS_RECIENTES: dict[str, float] = {}


class AuthService:
    """Lógica de autenticación y tokens."""

    def __init__(self, session: Session):
        self.session = session
        self.user_repo = UserRepository(session)
        self.refresh_repo = RefreshTokenRepository(session)
        self.login_attempt_repo = LoginAttemptRepository(session)

    def _hash_password(self, password: str) -> str:
        """Hash con BCrypt (cost 12)."""
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode("utf-8")

    def _check_password(self, plain: str, hashed: str) -> bool:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))

    def _lockout_cutoff(self) -> datetime:
        """Fecha desde la cual contar intentos fallidos."""
        return datetime.utcnow() - timedelta(minutes=settings.LOCKOUT_MINUTES)

    def _record_failed_attempt(self, email: str, ip_address: str | None) -> None:
        """
        Registra y PERSISTE un intento fallido.

        El commit es imprescindible: el endpoint de login lanza una excepción
        justo después y el `session.close()` del controlador haría rollback,
        descartando el INSERT. Sin este commit, `count_failed_since` siempre
        devolvía 0 y el bloqueo por fuerza bruta nunca se activaba.
        """
        self.login_attempt_repo.record_attempt(email, ip_address, success=False)
        self.session.commit()

    def _check_brute_force(self, email: str, ip_address: str | None = None) -> None:
        """
        Si hay demasiados intentos fallidos, lanza TooManyRequestsError.

        Dos gates independientes:
        - Por email: protege una cuenta concreta (umbral bajo).
        - Por IP: frena un atacante que prueba muchas cuentas desde un mismo
          origen (umbral más alto por NAT). Se omite si no hay IP o está a 0.
        """
        since = self._lockout_cutoff()
        count = self.login_attempt_repo.count_failed_since(email, since)
        if count >= settings.MAX_LOGIN_ATTEMPTS:
            raise TooManyRequestsError(
                f"Demasiados intentos de acceso. Espera {settings.LOCKOUT_MINUTES} minutos."
            )

        ip_limit = settings.MAX_LOGIN_ATTEMPTS_PER_IP
        if ip_address and ip_limit > 0:
            ip_count = self.login_attempt_repo.count_failed_by_ip_since(ip_address, since)
            if ip_count >= ip_limit:
                raise TooManyRequestsError(
                    f"Demasiados intentos desde esta red. Espera {settings.LOCKOUT_MINUTES} minutos."
                )

    def _create_access_token(self, user_id: int, role: str) -> tuple[str, int]:
        """Genera JWT access token. Retorna (token, expires_in segundos)."""
        expire_min = settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES
        expires_at = datetime.utcnow() + timedelta(minutes=expire_min)
        # PyJWT 2.8+ exige que "sub" sea str (InvalidSubjectError si es int).
        payload = {
            "sub": str(user_id),
            "role": role,
            "exp": expires_at,
            "iat": datetime.utcnow(),
            "type": "access",
        }
        token = jwt.encode(
            payload,
            settings.JWT_SECRET_KEY,
            algorithm=settings.JWT_ALGORITHM,
        )
        return token, expire_min * 60

    def _create_refresh_token(self, user_id: int) -> tuple[str, str, datetime]:
        """Genera refresh token (string aleatorio), guarda hash en BD. Retorna (token, hash, expires_at)."""
        raw = secrets.token_urlsafe(64)
        token_hash = hashlib.sha256(raw.encode()).hexdigest()
        expires_at = datetime.utcnow() + timedelta(days=settings.JWT_REFRESH_TOKEN_EXPIRE_DAYS)
        self.refresh_repo.create(user_id=user_id, token_hash=token_hash, expires_at=expires_at)
        return raw, token_hash, expires_at

    def login(self, email: str, password: str, ip_address: str | None = None) -> dict[str, Any]:
        """
        Login: verifica fuerza bruta, contraseña, genera access + refresh token.
        Registra intento (éxito o fallo).
        """
        self._check_brute_force(email, ip_address)

        user = self.user_repo.get_by_email_with_role(email)
        if not user:
            self._record_failed_attempt(email, ip_address)
            raise UnauthorizedError("Credenciales inválidas")

        if not user.is_active:
            self._record_failed_attempt(email, ip_address)
            raise UnauthorizedError("Usuario desactivado")

        if not self._check_password(password, user.password_hash):
            self._record_failed_attempt(email, ip_address)
            raise UnauthorizedError("Credenciales inválidas")

        self.login_attempt_repo.record_attempt(email, ip_address, success=True)
        self.session.commit()

        access_token, expires_in = self._create_access_token(user.id, user.role.name)
        refresh_raw, _, _ = self._create_refresh_token(user.id)
        self.session.commit()

        return {
            "access_token": access_token,
            "refresh_token": refresh_raw,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role.name,
                "assigned_mercadista": user.assigned_mercadista,
                "assigned_route_dataset_id": getattr(user, "assigned_route_dataset_id", None),
            },
        }

    def _es_carrera_de_rotacion(self, token_hash: str) -> bool:
        """¿Este token concreto se acaba de rotar, hace menos que la gracia?

        Se consulta por el hash EXACTO del token presentado. Una primera versión
        miraba si el usuario tenía algún token vigente reciente, y eso daba por
        buena una reutilización tardía en cuanto la persona tuviera otra sesión
        abierta en otro dispositivo: justo el caso que hay que rechazar.

        El registro vive en memoria del proceso a propósito, no en la base:
        - Es exacto y no exige tocar el esquema de una base en producción.
        - Falla del lado seguro. Si el proceso se reinicia —o si en el futuro se
          sirve con varios workers y la rotación quedó anotada en otro—, el
          registro no está y se aplica la revocación completa de siempre. Se
          pierde comodidad, nunca seguridad.
        """
        gracia = settings.JWT_REFRESH_ROTATION_GRACE_SECONDS
        if gracia <= 0:
            return False
        ahora = time.monotonic()
        # Limpieza perezosa: el registro solo guarda la ventana de gracia.
        for h, momento in list(_ROTADOS_RECIENTES.items()):
            if ahora - momento > gracia:
                _ROTADOS_RECIENTES.pop(h, None)
        momento = _ROTADOS_RECIENTES.get(token_hash)
        return momento is not None and (ahora - momento) <= gracia

    def _emitir_sesion(self, user_id: int) -> dict[str, Any]:
        """Access + refresh nuevos para un usuario ya identificado.

        En una carrera de rotación se emite un refresh PROPIO en vez de intentar
        devolver el del sucesor: del sucesor solo se guarda el hash, así que su
        valor en claro ya no existe. Las dos pestañas acaban con tokens válidos
        e independientes, y ninguna pisa a la otra.
        """
        user = self.user_repo.get_by_id_with_role(user_id)
        if not user or not user.is_active:
            raise UnauthorizedError("Usuario no válido")

        access_token, expires_in = self._create_access_token(user.id, user.role.name)
        refresh_raw, _, _ = self._create_refresh_token(user.id)
        self.session.commit()

        return {
            "access_token": access_token,
            "refresh_token": refresh_raw,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role.name,
                "assigned_mercadista": user.assigned_mercadista,
                "assigned_route_dataset_id": getattr(user, "assigned_route_dataset_id", None),
            },
        }

    def refresh(self, refresh_token: str) -> dict[str, Any]:
        """Intercambia refresh token por nuevo access token (y opcionalmente nuevo refresh)."""
        if not refresh_token:
            raise BadRequestError("refresh_token requerido")

        token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
        rt = self.refresh_repo.find_valid_by_hash_for_update(token_hash)
        if not rt:
            # El hash coincide con un token YA revocado (rotado en un refresh
            # anterior). Hay dos causas posibles y conviene distinguirlas.
            reused = self.refresh_repo.find_by_hash(token_hash)
            if reused and reused.revoked:
                if self._es_carrera_de_rotacion(token_hash):
                    # Dos peticiones legítimas salieron a la vez con la misma
                    # cookie —dos pestañas, o una recarga mientras el refresh
                    # anterior viajaba—. Revocar aquí cerraba la sesión del
                    # usuario justo al recargar la página.
                    return self._emitir_sesion(reused.user_id)
                # Sucesor viejo o inexistente: esto sí es un replay de un token
                # robado. Se revoca la familia entera para forzar relogin en
                # todos los dispositivos.
                self.refresh_repo.revoke_all_for_user(reused.user_id)
                self.session.commit()
                print(
                    f"[SECURITY] Refresh token reuse detectado para user_id={reused.user_id}: "
                    "se revocaron todas sus sesiones",
                    file=sys.stderr,
                )
            raise UnauthorizedError("Refresh token inválido o expirado")

        user = self.user_repo.get_by_id_with_role(rt.user_id)
        if not user or not user.is_active:
            raise UnauthorizedError("Usuario no válido")

        # Revocar el refresh usado (rotación) y emitir uno nuevo. Queda anotado
        # el instante para que una segunda petición legítima con la misma cookie
        # —otra pestaña, una recarga— no se confunda con un robo.
        rt.revoked = True
        _ROTADOS_RECIENTES[token_hash] = time.monotonic()
        access_token, expires_in = self._create_access_token(user.id, user.role.name)
        refresh_raw, _, _ = self._create_refresh_token(user.id)
        self.session.commit()

        return {
            "access_token": access_token,
            "refresh_token": refresh_raw,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "user": {
                "id": user.id,
                "email": user.email,
                "full_name": user.full_name,
                "role": user.role.name,
                "assigned_mercadista": user.assigned_mercadista,
                "assigned_route_dataset_id": getattr(user, "assigned_route_dataset_id", None),
            },
        }

    def logout(self, refresh_token: str | None, user_id: int | None) -> None:
        """
        Logout: si se envía refresh_token se revoca; si se envía user_id se revocan todos sus tokens.
        """
        if refresh_token:
            token_hash = hashlib.sha256(refresh_token.encode()).hexdigest()
            self.refresh_repo.revoke_by_hash(token_hash)
        if user_id:
            self.refresh_repo.revoke_all_for_user(user_id)
        self.session.commit()

    def verify_access_token(self, token: str) -> dict[str, Any]:
        """Decodifica y valida el JWT access token. Retorna payload (sub, role, exp, ...)."""
        try:
            payload = jwt.decode(
                token,
                settings.JWT_SECRET_KEY,
                algorithms=[settings.JWT_ALGORITHM],
            )
            if payload.get("type") != "access":
                raise UnauthorizedError("Token inválido")
            return payload
        except jwt.ExpiredSignatureError:
            raise UnauthorizedError("Token expirado")
        except jwt.InvalidTokenError:
            raise UnauthorizedError("Token inválido")
