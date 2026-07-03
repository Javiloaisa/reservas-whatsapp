"""Envio de mensajes por WhatsApp Cloud API (Meta Graph API).

Si no hay credenciales en `.env` (`settings.whatsapp_enabled == False`), opera en
"modo stub": registra el mensaje en consola en vez de llamar a la API. Esto permite
desarrollar y verificar el flujo completo sin tener todavia el numero de Meta dado de alta.

Reglas (§8 / §A.4):
- `send_text`: solo valido dentro de la ventana de 24 h desde el ultimo mensaje del cliente.
- `send_template`: obligatorio para mensajes iniciados por el negocio fuera de esa ventana
  (recordatorio 24 h y resumen al podologo). Se usara de lleno en la fase 5.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger("whatsapp")

_TIMEOUT = httpx.Timeout(15.0)


# --------------------------------------------------------------------------- #
#  Eventos neutrales de webhook (§3 v2) — independientes del proveedor
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MensajeEntrante:
    """Mensaje que un cliente envia al numero de la clinica."""

    telefono: str
    nombre_perfil: str | None
    texto: str
    message_id: str | None
    timestamp: dt.datetime | None


@dataclass(frozen=True)
class EcoSaliente:
    """Mensaje que el PODOLOGO envio desde su app (eco de coexistencia)."""

    telefono_destino: str
    texto: str
    message_id: str | None
    timestamp: dt.datetime | None


@dataclass(frozen=True)
class Otro:
    """Evento de webhook que no procesamos (estados de entrega, etc.)."""

    tipo: str


Evento = MensajeEntrante | EcoSaliente | Otro


def parse_webhook(payload: dict[str, Any]) -> list[Evento]:
    """Parsea un payload de webhook con el proveedor activo (YCloud si hay key)."""
    if settings.ycloud_enabled:
        from app.services import whatsapp_ycloud

        return whatsapp_ycloud.parse_webhook(payload)
    return _parse_webhook_meta(payload)


def _parse_webhook_meta(payload: dict[str, Any]) -> list[Evento]:
    """Payload de la Cloud API de Meta (legado y modo stub de desarrollo)."""
    eventos: list[Evento] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            contactos = {
                c.get("wa_id"): (c.get("profile") or {}).get("name")
                for c in value.get("contacts", [])
            }
            for msg in value.get("messages", []):
                telefono = msg.get("from", "")
                if not telefono:
                    continue
                if msg.get("type") == "text":
                    texto = (msg.get("text") or {}).get("body", "")
                else:
                    texto = f"[{msg.get('type', 'desconocido')}]"
                if not texto:
                    continue
                eventos.append(
                    MensajeEntrante(
                        telefono=telefono,
                        nombre_perfil=contactos.get(telefono),
                        texto=texto,
                        message_id=msg.get("id"),
                        timestamp=None,
                    )
                )
    return eventos


def _graph_url() -> str:
    return (
        f"https://graph.facebook.com/{settings.graph_api_version}"
        f"/{settings.whatsapp_phone_id}/messages"
    )


def _post(payload: dict[str, Any]) -> dict[str, Any] | None:
    """POST a la Graph API. Lanza/loguea ante error; nunca lo traga en silencio."""
    headers = {
        "Authorization": f"Bearer {settings.whatsapp_token}",
        "Content-Type": "application/json",
    }
    try:
        resp = httpx.post(_graph_url(), json=payload, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        log.error(
            "WhatsApp API error %s al enviar a %s: %s",
            exc.response.status_code,
            payload.get("to"),
            exc.response.text,
        )
        raise
    except httpx.HTTPError as exc:
        log.error("Fallo de red enviando a WhatsApp (%s): %s", payload.get("to"), exc)
        raise


def send_text(to: str, body: str) -> Any:
    """Mensaje de texto libre (solo dentro de la ventana de 24 h).

    Devuelve el message_id del proveedor (YCloud) o el payload de respuesta (Meta).
    """
    if settings.ycloud_enabled:
        from app.services import whatsapp_ycloud

        return whatsapp_ycloud.enviar_texto(to, body)
    if not settings.whatsapp_enabled:
        log.info("[WHATSAPP STUB] -> %s : %s", to, body)
        return None

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    return _post(payload)


def send_template(
    to: str,
    name: str,
    lang: str | None = None,
    components: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Mensaje basado en plantilla aprobada (mensajes iniciados por el negocio)."""
    lang = lang or settings.whatsapp_template_lang
    if settings.ycloud_enabled:
        from app.services import whatsapp_ycloud

        # Extraer las variables posicionales del componente body (formato Meta).
        variables = [
            p.get("text", "")
            for comp in (components or [])
            if comp.get("type") == "body"
            for p in comp.get("parameters", [])
        ]
        return whatsapp_ycloud.enviar_plantilla(to, name, variables, lang)
    if not settings.whatsapp_enabled:
        log.info("[WHATSAPP STUB] plantilla '%s' (%s) -> %s : %s", name, lang, to, components)
        return None

    payload: dict[str, Any] = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {"name": name, "language": {"code": lang}},
    }
    if components:
        payload["template"]["components"] = components
    return _post(payload)
