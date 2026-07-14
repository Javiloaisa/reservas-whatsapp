"""Carga inicial idempotente: servicios, horarios, config y usuario admin.

Ejecutar tras `alembic upgrade head`:
    python -m scripts.seed

Es idempotente: re-ejecutarlo no duplica datos.

Datos reales de la clinica Jesus Garcia Podoleg (§13 del CLAUDE.md v2).
Precios pendientes (§16): quedan a NULL hasta que el podologo los aporte.
"""

from __future__ import annotations

import datetime as dt

from sqlalchemy import func, select

from app.config import settings
from app.db import SessionLocal
from app.models import Horario, Servicio, UsuarioAdmin
from app.security import hash_password
from app.services.config_repo import get_config, set_config

# (nombre, duracion_min, buffer_min, precio) -- §13; precios pendientes (§16)
SERVICIOS = [
    ("Primera visita", 45, 0, None),
    ("Quiropodia", 45, 0, None),
    ("Exploración biomecánica", 60, 0, None),
    ("Exploración biomecánica + análisis de la carrera", 90, 0, None),
    ("Entrega de resultados", 30, 0, None),
    ("Revisión soportes plantares", 15, 0, None),
    ("Vendaje deportivo", 15, 0, None),
    ("Cura papiloma", 15, 0, None),
    ("Silicona simple", 15, 0, None),
    ("Silicona complicada", 30, 0, None),
    ("Reconstrucción ungueal", 40, 0, None),
    ("Ortonixia", 30, 0, None),
]

# dia_semana (0=lunes..6=domingo) -> franjas; sabado y domingo cerrado (§13)
HORARIO_SEMANAL: dict[int, list[tuple[dt.time, dt.time]]] = {
    0: [(dt.time(9, 0), dt.time(13, 30))],
    1: [(dt.time(9, 0), dt.time(13, 30)), (dt.time(15, 0), dt.time(20, 0))],
    2: [(dt.time(9, 0), dt.time(13, 30)), (dt.time(15, 0), dt.time(20, 0))],
    3: [(dt.time(9, 0), dt.time(15, 0))],
    4: [(dt.time(9, 0), dt.time(15, 0))],
}

CONFIG_DEFAULTS = {
    "timezone": settings.timezone,
    # Modo sombra por defecto (§12 v2): el pipeline corre entero pero no se envia
    # nada hasta activarlo a mano tras revisar `log_sombra`.
    "bot_activo": "false",
    # Modelos separados: el agente necesita Sonnet (prompt complejo + tool use);
    # al clasificador (JSON de una palabra) le basta Haiku, mas barato.
    "modelo_agente": settings.claude_model_agente,
    "modelo_clasificador": settings.claude_model,
    "mensaje_bienvenida": (
        "Hola! Soy el asistente de la clinica de podologia. Puedo informarte de los "
        "servicios y reservar tu cita. En que puedo ayudarte?"
    ),
    # Chat de Telegram del podologo para el resumen diario (§11 v2). Vacio => usa
    # TELEGRAM_CHAT_ID del .env. Editable desde el panel (Ajustes).
    "telegram_chat_id": "",
    # Modo humano tras un eco del podologo (§5 v2): horas que el bot calla para ese cliente.
    "intervalo_modo_humano_horas": "4",
    # Palabra clave que el podologo escribe para reactivar el bot antes de tiempo (§5 v2).
    "palabra_reactivacion": "#bot",
    # Retencion RGPD de mensajes/log_sombra en meses (§14 v2).
    "retencion_mensajes_meses": "12",
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
    for dia, franjas in HORARIO_SEMANAL.items():
        for ini, fin in franjas:
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
