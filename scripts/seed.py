"""Carga inicial idempotente: servicios, horarios, config y usuario admin.

Ejecutar tras `alembic upgrade head`:
    python -m scripts.seed

Es idempotente: re-ejecutarlo no duplica datos.

Datos placeholder (decisiones §16 sin respuesta del usuario; documentado en README):
- Servicios: Quiropodia 30/0, Estudio biomecanico 45/0, Una encarnada 40/0, Revision 20/0.
- Horario: L-V 9:00-14:00 y 16:00-20:00.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.models import Horario, Servicio, UsuarioAdmin
from app.security import hash_password
from app.services.config_repo import get_config, set_config

# (nombre, duracion_min, buffer_min, precio)  -- placeholders, ver §16
SERVICIOS = [
    ("Quiropodia", 30, 0, Decimal("30.00")),
    ("Estudio biomecanico", 45, 0, Decimal("50.00")),
    ("Una encarnada", 40, 0, Decimal("45.00")),
    ("Revision", 20, 0, Decimal("15.00")),
]

# L-V (0..4), dos franjas: manana y tarde.
FRANJAS = [(dt.time(9, 0), dt.time(14, 0)), (dt.time(16, 0), dt.time(20, 0))]
DIAS_LABORABLES = range(0, 5)

CONFIG_DEFAULTS = {
    "timezone": settings.timezone,
    "bot_activo": "true",
    "modelo_claude": settings.claude_model,
    "mensaje_bienvenida": (
        "Hola! Soy el asistente de la clinica de podologia. Puedo informarte de los "
        "servicios y reservar tu cita. En que puedo ayudarte?"
    ),
    # Numero del podologo (internacional sin '+') para el resumen diario (fase 5). Placeholder.
    "podologo_whatsapp": "",
}


def seed_servicios(session) -> None:
    if session.scalar(select(func.count()).select_from(Servicio)):
        print("servicios: ya existen, se omite")
        return
    for nombre, dur, buf, precio in SERVICIOS:
        session.add(Servicio(nombre=nombre, duracion_min=dur, buffer_min=buf, precio=precio, activo=True))
    print(f"servicios: insertados {len(SERVICIOS)}")


def seed_horarios(session) -> None:
    if session.scalar(select(func.count()).select_from(Horario)):
        print("horarios: ya existen, se omite")
        return
    n = 0
    for dia in DIAS_LABORABLES:
        for ini, fin in FRANJAS:
            session.add(Horario(dia_semana=dia, hora_inicio=ini, hora_fin=fin))
            n += 1
    print(f"horarios: insertadas {n} franjas (L-V)")


def seed_config(session) -> None:
    for clave, valor in CONFIG_DEFAULTS.items():
        if get_config(session, clave) is None:
            set_config(session, clave, valor)
            print(f"config: {clave} = {valor!r}")
        else:
            print(f"config: {clave} ya existe, se omite")


def seed_admin(session) -> None:
    existente = session.scalar(select(UsuarioAdmin).where(UsuarioAdmin.email == settings.admin_email))
    if existente is not None:
        print(f"admin: {settings.admin_email} ya existe, se omite")
        return
    session.add(
        UsuarioAdmin(email=settings.admin_email, password_hash=hash_password(settings.admin_password))
    )
    print(f"admin: creado {settings.admin_email}")


def main() -> None:
    session = SessionLocal()
    try:
        seed_servicios(session)
        seed_horarios(session)
        seed_config(session)
        seed_admin(session)
        session.commit()
        print("seed: OK")
    finally:
        session.close()


if __name__ == "__main__":
    main()
