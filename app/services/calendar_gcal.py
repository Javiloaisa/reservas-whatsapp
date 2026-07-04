"""Integracion con Google Calendar (cuenta de servicio).

La base de datos es la fuente de verdad; Calendar es un espejo de salida. Si no
hay credenciales (`settings.gcal_enabled == False`), opera en "modo stub":
registra la accion y devuelve None, sin romper el flujo de reserva.

El sync es best-effort: si Calendar falla, la cita ya esta en la DB y se deja
`gcal_event_id = None` para reintento posterior (no se pierde la reserva).
"""

from __future__ import annotations

import datetime as dt
import logging
from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from app.config import settings

log = logging.getLogger("gcal")

_SCOPES = ["https://www.googleapis.com/auth/calendar"]


@lru_cache
def _service() -> Any:
    """Cliente de la API de Calendar (cacheado). Solo se llama si gcal_enabled."""
    from google.oauth2 import service_account  # import perezoso
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_file(
        settings.google_credentials_file, scopes=_SCOPES
    )
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


def eventos_ocupados(
    desde: dt.datetime, hasta: dt.datetime
) -> list[tuple[str | None, dt.datetime, dt.datetime]]:
    """Eventos del calendario que solapan [desde, hasta): (event_id, inicio, fin) en UTC.

    El podologo tambien apunta citas A MANO en su Calendar; la agenda debe
    tratarlas como huecos ocupados aunque no existan en la BD. Los eventos de
    dia completo (vacaciones, festivos) bloquean el dia entero en hora local.
    Vacio en modo stub.
    """
    if not settings.gcal_enabled:
        return []

    resp = (
        _service()
        .events()
        .list(
            calendarId=settings.google_calendar_id,
            timeMin=desde.astimezone(dt.timezone.utc).isoformat(),
            timeMax=hasta.astimezone(dt.timezone.utc).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=250,
        )
        .execute()
    )

    tz_local = ZoneInfo(settings.timezone)
    ocupados: list[tuple[str | None, dt.datetime, dt.datetime]] = []
    for ev in resp.get("items", []):
        if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
            continue  # borrados o marcados como "libre"
        ini, fin = ev.get("start") or {}, ev.get("end") or {}
        if "dateTime" in ini and "dateTime" in fin:
            s = dt.datetime.fromisoformat(ini["dateTime"])
            e = dt.datetime.fromisoformat(fin["dateTime"])
        elif "date" in ini and "date" in fin:  # evento de dia completo
            s = dt.datetime.combine(dt.date.fromisoformat(ini["date"]), dt.time.min, tzinfo=tz_local)
            e = dt.datetime.combine(dt.date.fromisoformat(fin["date"]), dt.time.min, tzinfo=tz_local)
        else:
            continue
        ocupados.append((ev.get("id"), s.astimezone(dt.timezone.utc), e.astimezone(dt.timezone.utc)))
    return ocupados


def eventos_en(
    inicio: dt.datetime,
) -> list[tuple[str | None, str, dt.datetime, dt.datetime]]:
    """Eventos cuyo inicio coincide exactamente con `inicio`: (id, titulo, ini, fin) en UTC.

    Para localizar citas que el podologo apunto A MANO cuando un cliente pide
    cancelarlas o cambiarlas (no existen en la BD). Vacio en modo stub.
    """
    if not settings.gcal_enabled:
        return []

    inicio_utc = inicio.astimezone(dt.timezone.utc)
    resp = (
        _service()
        .events()
        .list(
            calendarId=settings.google_calendar_id,
            timeMin=inicio_utc.isoformat(),
            timeMax=(inicio_utc + dt.timedelta(minutes=1)).isoformat(),
            singleEvents=True,
            orderBy="startTime",
            maxResults=25,
        )
        .execute()
    )

    encontrados: list[tuple[str | None, str, dt.datetime, dt.datetime]] = []
    for ev in resp.get("items", []):
        if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent":
            continue
        ini, fin = ev.get("start") or {}, ev.get("end") or {}
        if "dateTime" not in ini or "dateTime" not in fin:
            continue  # los eventos de dia completo no son citas de cliente
        s = dt.datetime.fromisoformat(ini["dateTime"]).astimezone(dt.timezone.utc)
        if s != inicio_utc:
            continue
        e = dt.datetime.fromisoformat(fin["dateTime"]).astimezone(dt.timezone.utc)
        encontrados.append((ev.get("id"), ev.get("summary") or "", s, e))
    return encontrados


def create_event(
    summary: str,
    start: dt.datetime,
    end: dt.datetime,
    description: str | None = None,
) -> str | None:
    """Crea un evento y devuelve su id, o None en modo stub / ante fallo controlado."""
    if not settings.gcal_enabled:
        log.info("[GCAL STUB] create_event '%s' %s -> %s", summary, start.isoformat(), end.isoformat())
        return None

    body = {
        "summary": summary,
        "description": description or "",
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }
    event = (
        _service()
        .events()
        .insert(calendarId=settings.google_calendar_id, body=body)
        .execute()
    )
    return event.get("id")


def update_event(
    event_id: str | None,
    summary: str,
    start: dt.datetime,
    end: dt.datetime,
    description: str | None = None,
) -> str | None:
    """Actualiza un evento existente. Si no hay id, lo crea. No-op (log) en modo stub."""
    if not settings.gcal_enabled:
        log.info("[GCAL STUB] update_event %s -> %s/%s", event_id, start.isoformat(), end.isoformat())
        return event_id
    if not event_id:
        return create_event(summary, start, end, description)

    body = {
        "summary": summary,
        "description": description or "",
        "start": {"dateTime": start.isoformat()},
        "end": {"dateTime": end.isoformat()},
    }
    event = (
        _service()
        .events()
        .patch(calendarId=settings.google_calendar_id, eventId=event_id, body=body)
        .execute()
    )
    return event.get("id")


def delete_event(event_id: str | None) -> None:
    """Borra un evento. No-op si no hay id o en modo stub."""
    if not event_id:
        return
    if not settings.gcal_enabled:
        log.info("[GCAL STUB] delete_event %s", event_id)
        return

    try:
        _service().events().delete(
            calendarId=settings.google_calendar_id, eventId=event_id
        ).execute()
    except Exception as exc:  # noqa: BLE001 - loguear, no tragar
        log.error("Fallo borrando evento %s en Calendar: %s", event_id, exc)
        raise
