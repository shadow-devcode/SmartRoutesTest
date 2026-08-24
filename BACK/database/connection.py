"""
Conexión a MySQL con SQLAlchemy.
Uso de engine y sesión; todas las consultas se hacen por ORM (protección contra SQL Injection).
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session
from sqlalchemy.pool import QueuePool

# Importar después de definir Base en models
def get_engine(database_uri: str, echo: bool = False):
    """Crea el engine de SQLAlchemy con pool de conexiones."""
    return create_engine(
        database_uri,
        echo=echo,
        poolclass=QueuePool,
        pool_size=5,
        max_overflow=10,
        pool_pre_ping=True,
    )


def create_session_factory(engine):
    """Crea SessionLocal (scoped_session para thread-safety)."""
    return scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))


# Se inicializan en app factory cuando se carga la config
engine = None
SessionLocal = None


def init_db(database_uri: str, echo: bool = False):
    """Inicializa engine y SessionLocal. Llamar al arrancar la app."""
    global engine, SessionLocal
    engine = get_engine(database_uri, echo=echo)
    SessionLocal = create_session_factory(engine)
    return engine
