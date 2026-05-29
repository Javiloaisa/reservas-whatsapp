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
