"""Engine, sesion y Base declarativa de SQLAlchemy 2.x.

Codigo agnostico al motor: con `DATABASE_URL` apuntando a SQLite (desarrollo)
o a PostgreSQL (produccion). En SQLite se activan las claves foraneas, que por
defecto vienen desactivadas.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterator

from sqlalchemy import DateTime, create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from app.config import settings


class UTCDateTime(TypeDecorator):
    """DateTime que SIEMPRE se almacena y devuelve como UTC-aware.

    SQLite no guarda zona horaria (devuelve datetimes naive); PostgreSQL si.
    Este tipo normaliza ambos: al escribir, convierte a UTC; al leer, garantiza
    tzinfo=UTC. Asi el resto del codigo trabaja siempre con datetimes aware.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: dt.datetime | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)

    def process_result_value(self, value: dt.datetime | None, dialect):  # noqa: ANN001
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.timezone.utc)
        return value.astimezone(dt.timezone.utc)

_is_sqlite = settings.database_url.startswith("sqlite")

engine: Engine = create_engine(
    settings.database_url,
    # SQLite + acceso desde hilos del servidor / background tasks.
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    future=True,
    pool_pre_ping=not _is_sqlite,
)


if _is_sqlite:

    @event.listens_for(engine, "connect")
    def _enable_sqlite_fk(dbapi_connection, _connection_record) -> None:  # noqa: ANN001
        """Activar claves foraneas en cada conexion SQLite."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    """Base declarativa comun a todos los modelos."""


def get_session() -> Iterator[Session]:
    """Dependencia FastAPI: una sesion por request, cerrada al terminar."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
