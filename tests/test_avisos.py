"""Pruebas de `services/avisos.py` (§11 v2): la agenda del dia se arma por el
INICIO de la cita, no por cuando se reservo (`creado_en`)."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.db import SessionLocal
from app.models import ESTADO_CONFIRMADA, Cita, Cliente
from app.services import avisos, whatsapp
from app.services.config_repo import get_config, get_timezone, set_config


@pytest.fixture()
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def restaurar_podologo_whatsapp(session):
    original = get_config(session, "podologo_whatsapp")
    yield
    if original is not None:
        set_config(session, "podologo_whatsapp", original)
        session.commit()


def _cliente(session, nombre: str) -> Cliente:
    cliente = Cliente(telefono="34600" + uuid.uuid4().hex[:6], nombre=nombre)
    session.add(cliente)
    session.flush()
    return cliente


def test_resumen_diario_incluye_solo_citas_reservadas_hoy(
    session, restaurar_podologo_whatsapp, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El resumen de fin de dia lista las citas RESERVADAS hoy (aunque la cita sea
    para otro dia), no la agenda de hoy (decision del usuario 2026-07-04)."""
    set_config(session, "podologo_whatsapp", "34699999999")
    session.commit()

    tz = get_timezone(session)
    ahora_local = dt.datetime.now(tz)
    hoy_10am_utc = ahora_local.replace(hour=10, minute=0, second=0, microsecond=0).astimezone(
        dt.timezone.utc
    )
    manana_10am_utc = hoy_10am_utc + dt.timedelta(days=1)
    ayer_utc = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=1)

    cliente_vieja = _cliente(session, "Reservada ayer, cita hoy")
    session.add(
        Cita(
            cliente_id=cliente_vieja.id, servicio_id=1, inicio=hoy_10am_utc,
            fin=hoy_10am_utc + dt.timedelta(minutes=30), estado=ESTADO_CONFIRMADA,
            creado_en=ayer_utc,
        )
    )
    cliente_nueva = _cliente(session, "Reservada hoy, cita manana")
    session.add(
        Cita(
            cliente_id=cliente_nueva.id, servicio_id=1, inicio=manana_10am_utc,
            fin=manana_10am_utc + dt.timedelta(minutes=30), estado=ESTADO_CONFIRMADA,
        )
    )
    session.commit()

    capturado: dict = {}

    def _fake_send_template(to, name, lang=None, components=None):  # noqa: ANN001
        capturado["to"] = to
        capturado["components"] = components

    monkeypatch.setattr(whatsapp, "send_template", _fake_send_template)

    enviado = avisos.enviar_resumen_diario(session)

    assert enviado is True
    texto = capturado["components"][0]["parameters"][0]["text"]
    assert "Reservada hoy, cita manana" in texto
    assert "Reservada ayer, cita hoy" not in texto
