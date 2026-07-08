"""Confirmar/consultar una cita ya reservada NO debe cancelarla.

Regresion del incidente 2026-07-08: una clienta pidio "confirmar mi cita de hoy"
y el agente, sin herramienta de lectura, llamo a cancelar_cita y la borro.
`citas_futuras_cliente` es la via de solo lectura que arregla eso.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db import SessionLocal
from app.models import ESTADO_CANCELADA, Cliente
from app.services import agenda, calendar_gcal
from app.services.config_repo import get_timezone

TELEFONO = "+34600222444"


@pytest.fixture()
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def _quiropodia(session):  # noqa: ANN202
    return next(s for s in agenda.listar_servicios_activos(session) if s.nombre == "Quiropodia")


def _primer_hueco(session, servicio_id: int, tz):  # noqa: ANN001
    hoy = dt.datetime.now(tz).date()
    for offset in range(1, 15):
        fecha = hoy + dt.timedelta(days=offset)
        huecos = agenda.huecos_libres(session, fecha, servicio_id, tz=tz)
        if huecos:
            return fecha, huecos[0]
    pytest.skip("sin huecos libres en 2 semanas (BD de dev saturada)")


def _cita_de_prueba(session, monkeypatch):  # noqa: ANN001, ANN202
    """Crea una cita real futura para TELEFONO (sin tocar Calendar)."""
    monkeypatch.setattr(calendar_gcal, "create_event", lambda **kw: "evt_test")
    if session.query(Cliente).filter_by(telefono=TELEFONO).one_or_none() is None:
        session.add(Cliente(telefono=TELEFONO, nombre="Maria Prueba"))
        session.commit()
    tz = get_timezone(session)
    servicio = _quiropodia(session)
    _fecha, hueco = _primer_hueco(session, servicio.id, tz)
    return agenda.crear_cita(session, TELEFONO, servicio.id, hueco.isoformat(), nombre="Maria Prueba")


def test_consultar_cita_devuelve_la_cita_sin_cancelarla(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    cita = _cita_de_prueba(session, monkeypatch)

    citas = agenda.citas_futuras_cliente(session, TELEFONO)

    assert any(c.id == cita.id for c in citas)
    # La cita sigue confirmada: consultar no muta estado.
    session.refresh(cita)
    assert cita.estado != ESTADO_CANCELADA


def test_consultar_cita_filtra_por_fecha(session, monkeypatch: pytest.MonkeyPatch) -> None:
    cita = _cita_de_prueba(session, monkeypatch)
    tz = get_timezone(session)
    dia = cita.inicio.astimezone(tz).date()

    assert any(c.id == cita.id for c in agenda.citas_futuras_cliente(session, TELEFONO, fecha=dia))
    otro_dia = dia + dt.timedelta(days=1)
    assert all(c.id != cita.id for c in agenda.citas_futuras_cliente(session, TELEFONO, fecha=otro_dia))
