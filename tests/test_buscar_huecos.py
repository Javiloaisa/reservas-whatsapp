"""Busqueda de disponibilidad por RANGO con filtro de franja (fix causa 2:
evitar que el agente sondee dia a dia peticiones abiertas)."""

from __future__ import annotations

import datetime as dt

import pytest

from app.db import SessionLocal
from app.services import agenda, calendar_gcal
from app.services.config_repo import get_timezone


@pytest.fixture()
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _sin_gcal(monkeypatch: pytest.MonkeyPatch):
    """Aisla de Google Calendar: disponibilidad deterministica solo con la BD."""
    monkeypatch.setattr(calendar_gcal, "eventos_ocupados", lambda d, h: [])


def _quiropodia(session):  # noqa: ANN202
    return next(s for s in agenda.listar_servicios_activos(session) if s.nombre == "Quiropodia")


def test_franja_horaria_filtra_por_hora_de_inicio(session) -> None:
    tz = get_timezone(session)
    servicio = _quiropodia(session)
    hoy = dt.datetime.now(tz).date()

    resultados, _ = agenda.buscar_huecos(
        session,
        servicio_id=servicio.id,
        desde=hoy + dt.timedelta(days=1),
        hasta=hoy + dt.timedelta(days=21),
        hora_desde=dt.time(18, 0),
        max_dias=5,
    )

    assert resultados, "esperaba algun hueco de tarde (martes/miercoles hasta 20:00)"
    for _fecha, huecos in resultados:
        assert huecos, "un dia sin huecos no deberia aparecer"
        assert all(h.time() >= dt.time(18, 0) for h in huecos)


def test_coincide_con_huecos_libres_por_dia(session) -> None:
    """Cada dia devuelto debe coincidir exactamente con huecos_libres filtrado."""
    tz = get_timezone(session)
    servicio = _quiropodia(session)
    hoy = dt.datetime.now(tz).date()

    resultados, _ = agenda.buscar_huecos(
        session,
        servicio_id=servicio.id,
        desde=hoy + dt.timedelta(days=1),
        hasta=hoy + dt.timedelta(days=14),
        hora_desde=dt.time(9, 0),
        hora_hasta=dt.time(9, 30),
        max_dias=10,
    )

    for fecha, huecos in resultados:
        esperados = [
            h for h in agenda.huecos_libres(session, fecha, servicio.id, tz=tz)
            if dt.time(9, 0) <= h.time() <= dt.time(9, 30)
        ]
        assert huecos == esperados


def test_max_dias_limita_resultados(session) -> None:
    servicio = _quiropodia(session)
    tz = get_timezone(session)
    hoy = dt.datetime.now(tz).date()

    resultados, _ = agenda.buscar_huecos(
        session,
        servicio_id=servicio.id,
        desde=hoy + dt.timedelta(days=1),
        hasta=hoy + dt.timedelta(days=30),
        max_dias=2,
    )

    assert len(resultados) <= 2


def test_rango_invertido_no_devuelve_nada(session) -> None:
    servicio = _quiropodia(session)
    tz = get_timezone(session)
    hoy = dt.datetime.now(tz).date()

    resultados, truncado = agenda.buscar_huecos(
        session,
        servicio_id=servicio.id,
        desde=hoy + dt.timedelta(days=5),
        hasta=hoy + dt.timedelta(days=1),
    )

    assert resultados == []
    assert truncado is False


def test_servicio_invalido_falla_pronto(session) -> None:
    with pytest.raises(agenda.ServicioInvalido):
        agenda.buscar_huecos(
            session,
            servicio_id=999999,
            desde=dt.date(2026, 7, 20),
            hasta=dt.date(2026, 7, 25),
        )
