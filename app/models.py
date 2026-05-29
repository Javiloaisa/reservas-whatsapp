"""Modelos SQLAlchemy 2.0 (tablas de la §4 del CLAUDE.md).

Convenciones:
- Fechas/horas con `UTCDateTime`, almacenadas SIEMPRE en UTC-aware.
- Estados como cadenas (`confirmada|cancelada|completada|no_show`, etc.).
- Se definen todas las tablas (incluidas las de fases posteriores como
  `usuarios_admin`) para no rehacer migraciones mas adelante.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime


def utcnow() -> dt.datetime:
    """Ahora en UTC, timezone-aware (consistente en SQLite y PostgreSQL)."""
    return dt.datetime.now(dt.timezone.utc)


# --- Estados de cita ---
ESTADO_CONFIRMADA = "confirmada"
ESTADO_CANCELADA = "cancelada"
ESTADO_COMPLETADA = "completada"
ESTADO_NO_SHOW = "no_show"

RECORDATORIO_PENDIENTE = "pendiente"
RECORDATORIO_ENVIADO = "enviado"
RECORDATORIO_NO_APLICA = "no_aplica"


class Servicio(Base):
    __tablename__ = "servicios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    nombre: Mapped[str] = mapped_column(Text, nullable=False)
    duracion_min: Mapped[int] = mapped_column(Integer, nullable=False)
    buffer_min: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    precio: Mapped[Decimal | None] = mapped_column(Numeric(8, 2), nullable=True)
    activo: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    citas: Mapped[list[Cita]] = relationship(back_populates="servicio")


class Horario(Base):
    """Franja de apertura semanal recurrente."""

    __tablename__ = "horarios"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dia_semana: Mapped[int] = mapped_column(SmallInteger, nullable=False)  # 0=lunes..6=domingo
    hora_inicio: Mapped[dt.time] = mapped_column(Time, nullable=False)
    hora_fin: Mapped[dt.time] = mapped_column(Time, nullable=False)


class Bloqueo(Base):
    """Bloqueos puntuales: vacaciones, festivos, ausencias."""

    __tablename__ = "bloqueos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inicio: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    fin: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)


class Cliente(Base):
    __tablename__ = "clientes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telefono: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)  # internacional sin '+'
    nombre: Mapped[str | None] = mapped_column(Text, nullable=True)
    creado_en: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    citas: Mapped[list[Cita]] = relationship(back_populates="cliente")
    mensajes: Mapped[list[Mensaje]] = relationship(back_populates="cliente")


class Cita(Base):
    __tablename__ = "citas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), nullable=False)
    servicio_id: Mapped[int] = mapped_column(ForeignKey("servicios.id"), nullable=False)
    inicio: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    fin: Mapped[dt.datetime] = mapped_column(UTCDateTime, nullable=False)
    estado: Mapped[str] = mapped_column(Text, nullable=False, default=ESTADO_CONFIRMADA)
    gcal_event_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    recordatorio: Mapped[str] = mapped_column(Text, nullable=False, default=RECORDATORIO_PENDIENTE)
    creado_en: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )
    notas: Mapped[str | None] = mapped_column(Text, nullable=True)

    cliente: Mapped[Cliente] = relationship(back_populates="citas")
    servicio: Mapped[Servicio] = relationship(back_populates="citas")


Index("idx_citas_inicio", Cita.inicio)


class Mensaje(Base):
    """Historial de conversacion por cliente (contexto del agente)."""

    __tablename__ = "mensajes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cliente_id: Mapped[int] = mapped_column(ForeignKey("clientes.id"), nullable=False)
    rol: Mapped[str] = mapped_column(Text, nullable=False)  # user|assistant
    contenido: Mapped[str] = mapped_column(Text, nullable=False)
    creado_en: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )

    cliente: Mapped[Cliente] = relationship(back_populates="mensajes")


Index("idx_mensajes_cliente", Mensaje.cliente_id, Mensaje.creado_en)


class MensajeProcesado(Base):
    """Deduplicacion de webhooks de Meta (idempotencia)."""

    __tablename__ = "mensajes_procesados"

    wa_message_id: Mapped[str] = mapped_column(Text, primary_key=True)
    procesado_en: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )


class UsuarioAdmin(Base):
    __tablename__ = "usuarios_admin"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    creado_en: Mapped[dt.datetime] = mapped_column(
        UTCDateTime, nullable=False, default=utcnow
    )


class Config(Base):
    """Configuracion global clave/valor."""

    __tablename__ = "config"

    clave: Mapped[str] = mapped_column(Text, primary_key=True)
    valor: Mapped[str] = mapped_column(Text, nullable=False)
