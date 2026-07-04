"""Pruebas de `services/retencion.py` (§14 v2): purga RGPD de mensajes antiguos."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.db import SessionLocal
from app.models import ROL_CLIENTE, Cliente, LogSombra, Mensaje
from app.services import retencion
from app.services.config_repo import get_config, set_config


@pytest.fixture()
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def restaurar_retencion(session):
    original = get_config(session, "retencion_mensajes_meses")
    yield
    if original is not None:
        set_config(session, "retencion_mensajes_meses", original)
        session.commit()


def _cliente(session) -> Cliente:
    cliente = Cliente(telefono="34600" + uuid.uuid4().hex[:6])
    session.add(cliente)
    session.flush()
    return cliente


def test_purga_solo_lo_anterior_al_corte(session, restaurar_retencion) -> None:
    set_config(session, "retencion_mensajes_meses", "12")
    session.commit()

    cliente = _cliente(session)
    antiguo = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=400)
    reciente = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=10)

    session.add(
        Mensaje(cliente_id=cliente.id, rol=ROL_CLIENTE, contenido="viejo", creado_en=antiguo)
    )
    session.add(
        Mensaje(cliente_id=cliente.id, rol=ROL_CLIENTE, contenido="reciente", creado_en=reciente)
    )
    session.add(
        LogSombra(
            cliente_id=cliente.id, mensaje_entrante="viejo", clasificacion="cita", creado_en=antiguo
        )
    )
    session.commit()

    n_mensajes, n_log = retencion.purgar_mensajes_antiguos(session)

    assert n_mensajes == 1
    assert n_log == 1
    restantes = session.query(Mensaje).filter_by(cliente_id=cliente.id).all()
    assert len(restantes) == 1
    assert restantes[0].contenido == "reciente"
    assert session.query(LogSombra).filter_by(cliente_id=cliente.id).count() == 0
