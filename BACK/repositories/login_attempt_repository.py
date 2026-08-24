"""
Repositorio de intentos de login (fuerza bruta).
"""
from datetime import datetime, timedelta
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from models import LoginAttempt


class LoginAttemptRepository:
    def __init__(self, session: Session):
        self.session = session

    def record_attempt(self, email: str, ip_address: str | None, success: bool) -> LoginAttempt:
        attempt = LoginAttempt(email=email, ip_address=ip_address, success=success)
        self.session.add(attempt)
        self.session.flush()
        return attempt

    def count_failed_since(self, email: str, since: datetime) -> int:
        """Cuenta intentos fallidos por email desde una fecha (rate limiting)."""
        stmt = select(func.count(LoginAttempt.id)).where(
            LoginAttempt.email == email,
            LoginAttempt.success == False,
            LoginAttempt.attempted_at >= since,
        )
        return self.session.execute(stmt).scalar() or 0

    def count_failed_by_ip_since(self, ip_address: str, since: datetime) -> int:
        """Cuenta intentos fallidos por IP desde una fecha (rate limiting secundario)."""
        stmt = select(func.count(LoginAttempt.id)).where(
            LoginAttempt.ip_address == ip_address,
            LoginAttempt.success == False,
            LoginAttempt.attempted_at >= since,
        )
        return self.session.execute(stmt).scalar() or 0
