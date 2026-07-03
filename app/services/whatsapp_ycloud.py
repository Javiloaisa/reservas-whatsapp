"""Proveedor YCloud (BSP, coexistencia) — §8 del CLAUDE.md v2.

Envio por la API v2 de YCloud (X-API-Key) y parseo de sus webhooks a los
eventos neutrales de `services/whatsapp.py` (MensajeEntrante / EcoSaliente / Otro).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger("whatsapp.ycloud")

_TIMEOUT = httpx.Timeout(15.0)
_API_URL = "https://api.ycloud.com/v2/whatsapp/messages"

# Tipos de mensaje no-texto -> marcador que espera el clasificador (§6).
_MARCADORES = {
    "audio": "[audio]",
    "voice": "[audio]",
    "image": "[imagen]",
    "video": "[video]",
    "document": "[documento]",
    "location": "[ubicacion]",
    "sticker": "[sticker]",
    "contacts": "[contacto]",
}


def _post(payload: dict[str, Any]) -> str | None:
    """POST a YCloud; devuelve el id del mensaje creado. Nunca traga errores."""
    headers = {"X-API-Key": settings.ycloud_api_key, "Content-Type": "application/json"}
    try:
        resp = httpx.post(_API_URL, json=payload, headers=headers, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        return data.get("id") or data.get("wamid")
    except httpx.HTTPStatusError as exc:
        log.error(
            "YCloud error %s enviando a %s: %s",
            exc.response.status_code, payload.get("to"), exc.response.text,
        )
        raise
    except httpx.HTTPError as exc:
        log.error("Fallo de red con YCloud (%s): %s", payload.get("to"), exc)
        raise


def enviar_texto(telefono: str, texto: str) -> str | None:
    """Texto libre (solo dentro de la ventana de 24 h)."""
    payload = {
        "from": settings.whatsapp_phone_id or telefono,  # numero del negocio si esta configurado
        "to": telefono,
        "type": "text",
        "text": {"body": texto},
    }
    if settings.whatsapp_phone_id:
        payload["from"] = settings.whatsapp_phone_id
    else:
        payload.pop("from", None)  # YCloud usa el numero unico de la cuenta
    return _post(payload)


def enviar_plantilla(
    telefono: str, plantilla: str, variables: list[str] | None = None, lang: str | None = None
) -> str | None:
    """Plantilla aprobada (mensajes iniciados por el negocio). Variables posicionales."""
    template: dict[str, Any] = {
        "name": plantilla,
        "language": {"code": lang or settings.whatsapp_template_lang},
    }
    if variables:
        template["components"] = [
            {"type": "body", "parameters": [{"type": "text", "text": v} for v in variables]}
        ]
    payload: dict[str, Any] = {"to": telefono, "type": "template", "template": template}
    if settings.whatsapp_phone_id:
        payload["from"] = settings.whatsapp_phone_id
    return _post(payload)


def _parse_ts(valor: str | None) -> dt.datetime | None:
    if not valor:
        return None
    try:
        return dt.datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        return None


def _texto_de_mensaje(msg: dict[str, Any]) -> str:
    """Texto del mensaje, o un marcador tipo '[audio]' si no es texto."""
    tipo = msg.get("type", "")
    if tipo == "text":
        return (msg.get("text") or {}).get("body", "")
    return _MARCADORES.get(tipo, f"[{tipo or 'desconocido'}]")


def parse_webhook(payload: dict[str, Any]) -> list[Any]:
    """Convierte un webhook de YCloud en eventos neutrales.

    Tipos manejados:
    - whatsapp.inbound_message.received -> MensajeEntrante (mensaje del cliente).
    - eventos de eco de la app del negocio (coexistencia; el objeto llega como
      *businessAppMessage* o similar) -> EcoSaliente (lo escribio el podologo).
    - whatsapp.message.updated y demas -> Otro (estados de entrega, etc.).
    """
    from app.services.whatsapp import EcoSaliente, MensajeEntrante, Otro

    tipo_evento = payload.get("type", "")

    if tipo_evento == "whatsapp.inbound_message.received":
        msg = payload.get("whatsappInboundMessage") or {}
        telefono = msg.get("from", "")
        texto = _texto_de_mensaje(msg)
        if not telefono or not texto:
            return [Otro(tipo=tipo_evento)]
        perfil = (msg.get("customerProfile") or {}).get("name")
        return [
            MensajeEntrante(
                telefono=telefono,
                nombre_perfil=perfil,
                texto=texto,
                message_id=msg.get("id") or msg.get("wamid"),
                timestamp=_parse_ts(msg.get("sendTime")),
            )
        ]

    # Ecos de la app WhatsApp Business del podologo (coexistencia). YCloud los
    # entrega con el objeto del mensaje saliente; el nombre exacto del evento
    # puede variar segun version del webhook -> deteccion defensiva.
    if "business_app" in tipo_evento or "whatsappBusinessAppMessage" in payload:
        msg = (
            payload.get("whatsappBusinessAppMessage")
            or payload.get("whatsappMessage")
            or {}
        )
        destino = msg.get("to", "")
        if not destino:
            return [Otro(tipo=tipo_evento)]
        return [
            EcoSaliente(
                telefono_destino=destino,
                texto=_texto_de_mensaje(msg),
                message_id=msg.get("id") or msg.get("wamid"),
                timestamp=_parse_ts(msg.get("sendTime")),
            )
        ]

    return [Otro(tipo=tipo_evento or "desconocido")]
