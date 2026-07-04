"""La agenda debe tratar los eventos de Google Calendar como huecos ocupados
(el podologo apunta citas a mano en su Calendar; requisito 2026-07-04)."""

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


def _quiropodia(session):  # noqa: ANN202
    return next(s for s in agenda.listar_servicios_activos(session) if s.nombre == "Quiropodia")


def _primer_hueco(session, servicio_id: int, tz) -> tuple[dt.date, dt.datetime]:  # noqa: ANN001
    """Primer hueco libre real en las proximas 2 semanas (BD compartida entre tests)."""
    hoy = dt.datetime.now(tz).date()
    for offset in range(1, 15):
        fecha = hoy + dt.timedelta(days=offset)
        huecos = agenda.huecos_libres(session, fecha, servicio_id, tz=tz)
        if huecos:
            return fecha, huecos[0]
    pytest.skip("sin huecos libres en 2 semanas (BD de dev saturada)")


def test_evento_de_calendar_bloquea_huecos(session, monkeypatch: pytest.MonkeyPatch) -> None:
    tz = get_timezone(session)
    servicio = _quiropodia(session)
    fecha, hueco = _primer_hueco(session, servicio.id, tz)

    ocupado_ini = hueco.astimezone(dt.timezone.utc)
    ocupado_fin = ocupado_ini + dt.timedelta(hours=1)
    monkeypatch.setattr(
        calendar_gcal, "eventos_ocupados", lambda d, h: [("evt_manual", ocupado_ini, ocupado_fin)]
    )

    libres = agenda.huecos_libres(session, fecha, servicio.id, tz=tz)

    dur = dt.timedelta(minutes=servicio.duracion_min)
    for h in libres:
        h_utc = h.astimezone(dt.timezone.utc)
        assert not (h_utc < ocupado_fin and ocupado_ini < h_utc + dur), h


def test_evento_de_calendar_impide_crear_cita(session, monkeypatch: pytest.MonkeyPatch) -> None:
    tz = get_timezone(session)
    servicio = _quiropodia(session)
    _fecha, hueco = _primer_hueco(session, servicio.id, tz)

    ocupado_ini = hueco.astimezone(dt.timezone.utc)
    ocupado_fin = ocupado_ini + dt.timedelta(hours=1)
    monkeypatch.setattr(
        calendar_gcal, "eventos_ocupados", lambda d, h: [("evt_manual", ocupado_ini, ocupado_fin)]
    )

    with pytest.raises(agenda.SlotNoDisponible):
        agenda.simular_crear_cita(session, servicio_id=servicio.id, inicio_iso=hueco.isoformat())


def test_fallo_de_calendar_no_tumba_la_disponibilidad(
    session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Best-effort: si Calendar falla, la agenda sigue funcionando solo con la BD."""

    def _explota(d, h):  # noqa: ANN001
        raise RuntimeError("gcal caido")

    monkeypatch.setattr(calendar_gcal, "eventos_ocupados", _explota)
    tz = get_timezone(session)
    servicio = _quiropodia(session)
    fecha, hueco = _primer_hueco(session, servicio.id, tz)  # sin monkeypatch aplicado aun? si, ya aplicado
    # _primer_hueco ya corre con el fallo inyectado: si llega aqui, no ha tumbado nada.
    assert hueco is not None
