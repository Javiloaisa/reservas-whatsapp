"""Acceso a la tabla `config` (clave/valor) y a la zona horaria activa.

Claves esperadas (ver §4): `timezone`, `podologo_whatsapp`, `bot_activo`,
`mensaje_bienvenida`, `modelo_claude`.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Config


def get_config(session: Session, clave: str, default: str | None = None) -> str | None:
    valor = session.get(Config, clave)
    return valor.valor if valor is not None else default


def set_config(session: Session, clave: str, valor: str) -> None:
    fila = session.get(Config, clave)
    if fila is None:
        session.add(Config(clave=clave, valor=valor))
    else:
        fila.valor = valor


def all_config(session: Session) -> dict[str, str]:
    return {c.clave: c.valor for c in session.scalars(select(Config)).all()}


def bot_activo(session: Session) -> bool:
    """`bot_activo` se almacena como 'true'/'false'. Por defecto activo."""
    return (get_config(session, "bot_activo", "true") or "true").strip().lower() == "true"


def get_timezone(session: Session) -> ZoneInfo:
    """Zona horaria efectiva: valor de `config.timezone` o el de settings."""
    tz_name = get_config(session, "timezone", settings.timezone) or settings.timezone
    return ZoneInfo(tz_name)


def modelo_claude(session: Session) -> str:
    return get_config(session, "modelo_claude", settings.claude_model) or settings.claude_model
