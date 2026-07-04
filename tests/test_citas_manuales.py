"""Cancelacion de citas apuntadas A MANO por el podologo en Google Calendar
(no existen en la BD; requisito del usuario 2026-07-04).

Salvaguardas probadas: hora exacta + nombre del cliente en el titulo del evento.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.db import SessionLocal
from app.models import Cliente
from app.services import agenda, calendar_gcal
from app.services.config_repo import get_timezone

TELEFONO = "+34600111333"


@pytest.fixture()
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def cliente(session):  # noqa: ANN201
    c = session.query(Cliente).filter_by(telefono=TELEFONO).one_or_none()
    if c is None:
        c = Cliente(telefono=TELEFONO, nombre="Teresa Prueba")
        session.add(c)
        session.flush()
    else:
        c.nombre = "Teresa Prueba"
    session.commit()
    return c


def _manana_a_las(session, hora: int) -> dt.datetime:
    tz = get_timezone(session)
    fecha = dt.datetime.now(tz).date() + dt.timedelta(days=1)
    return dt.datetime.combine(fecha, dt.time(hora, 30), tzinfo=tz)


def _evento(inicio: dt.datetime, titulo: str) -> tuple:
    ini = inicio.astimezone(dt.timezone.utc)
    return ("evt_manual_1", titulo, ini, ini + dt.timedelta(minutes=45))


def test_cancela_evento_manual_con_nombre_coincidente(
    session, cliente, monkeypatch: pytest.MonkeyPatch
) -> None:
    inicio = _manana_a_las(session, 19)
    # El podologo apunta "Nombre Apellido - Servicio" (con acentos a veces).
    monkeypatch.setattr(
        calendar_gcal, "eventos_en", lambda i: [_evento(inicio, "Teresa Cardona - Quiró ")]
    )
    borrados: list[str] = []
    monkeypatch.setattr(calendar_gcal, "delete_event", borrados.append)

    info = agenda.cancelar_cita_manual(session, TELEFONO, inicio.isoformat())

    assert borrados == ["evt_manual_1"]
    assert info["inicio"].endswith("19:30")


def test_no_cancela_si_el_nombre_no_coincide(
    session, cliente, monkeypatch: pytest.MonkeyPatch
) -> None:
    inicio = _manana_a_las(session, 19)
    monkeypatch.setattr(
        calendar_gcal, "eventos_en", lambda i: [_evento(inicio, "Marisa Pineda - Quiro")]
    )
    borrados: list[str] = []
    monkeypatch.setattr(calendar_gcal, "delete_event", borrados.append)

    with pytest.raises(agenda.CitaNoEncontrada):
        agenda.cancelar_cita_manual(session, TELEFONO, inicio.isoformat())
    assert borrados == []


def test_simular_no_borra_nada(session, cliente, monkeypatch: pytest.MonkeyPatch) -> None:
    inicio = _manana_a_las(session, 19)
    monkeypatch.setattr(
        calendar_gcal, "eventos_en", lambda i: [_evento(inicio, "Teresa Cardona - Quiro")]
    )
    borrados: list[str] = []
    monkeypatch.setattr(calendar_gcal, "delete_event", borrados.append)

    info = agenda.simular_cancelar_cita_manual(session, TELEFONO, inicio.isoformat())

    assert borrados == []
    assert info["inicio"].endswith("19:30")


def test_sin_nombre_en_bd_deriva_al_podologo(session, monkeypatch: pytest.MonkeyPatch) -> None:
    telefono = "+34600111444"
    c = session.query(Cliente).filter_by(telefono=telefono).one_or_none()
    if c is None:
        session.add(Cliente(telefono=telefono, nombre=None))
    else:
        c.nombre = None
    session.commit()

    inicio = _manana_a_las(session, 19)
    monkeypatch.setattr(
        calendar_gcal, "eventos_en", lambda i: [_evento(inicio, "Teresa Cardona - Quiro")]
    )
    with pytest.raises(agenda.CitaNoEncontrada):
        agenda.cancelar_cita_manual(session, telefono, inicio.isoformat())


def test_citas_pasadas_no_se_cancelan(session, cliente, monkeypatch: pytest.MonkeyPatch) -> None:
    tz = get_timezone(session)
    ayer = dt.datetime.now(tz) - dt.timedelta(days=1)
    monkeypatch.setattr(
        calendar_gcal, "eventos_en", lambda i: [_evento(ayer, "Teresa Cardona - Quiro")]
    )
    with pytest.raises(agenda.SlotNoDisponible):
        agenda.cancelar_cita_manual(session, TELEFONO, ayer.isoformat())
