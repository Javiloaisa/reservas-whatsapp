"""Webhook de WhatsApp (Meta) y endpoint de prueba local.

- GET  /webhook : verificacion del webhook (hub.challenge).
- POST /webhook : recepcion de mensajes. Responde 200 rapido y procesa en background.
- POST /dev/simulate : inyecta un mensaje en el MISMO pipeline sin pasar por Meta
  (clave para verificar el bot localmente cuando WhatsApp esta en modo stub).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.db import SessionLocal
from app.models import MensajeProcesado
from app.schemas import SimulateRequest, SimulateResponse
from app.services import agente, whatsapp

log = logging.getLogger("webhook")

router = APIRouter()


def _firma_valida(body: bytes, cabecera: str | None) -> bool:
    """Valida la firma HMAC-SHA256 que Meta envia en X-Hub-Signature-256.

    La firma se calcula sobre el cuerpo crudo con el App Secret. Comparacion en
    tiempo constante para no filtrar informacion por temporizacion.
    """
    if not cabecera or not cabecera.startswith("sha256="):
        return False
    esperado = hmac.new(
        settings.whatsapp_app_secret.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(esperado, cabecera.split("=", 1)[1])


@router.get("/webhook", response_class=PlainTextResponse)
async def verificar_webhook(request: Request) -> Response:
    """Meta valida el webhook con hub.mode/hub.verify_token y espera hub.challenge."""
    params = request.query_params
    if (
        params.get("hub.mode") == "subscribe"
        and params.get("hub.verify_token") == settings.whatsapp_verify_token
    ):
        return PlainTextResponse(params.get("hub.challenge", ""))
    return PlainTextResponse("verification failed", status_code=403)


def _extraer_mensajes(payload: dict[str, Any]) -> list[tuple[str, str, str | None]]:
    """Extrae (telefono, texto, wa_message_id) de los mensajes de texto del payload."""
    out: list[tuple[str, str, str | None]] = []
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []):
                if msg.get("type") != "text":
                    continue  # en esta fase solo se procesa texto
                telefono = msg.get("from", "")
                texto = (msg.get("text") or {}).get("body", "")
                wa_id = msg.get("id")
                if telefono and texto:
                    out.append((telefono, texto, wa_id))
    return out


def _procesar_entrante(telefono: str, texto: str, wa_message_id: str | None) -> None:
    """Tarea en background: dedup -> agente -> envio. Sesion propia (request ya cerrado)."""
    session = SessionLocal()
    try:
        # Idempotencia: reclamar el wa_message_id antes de procesar.
        if wa_message_id is not None:
            if session.get(MensajeProcesado, wa_message_id) is not None:
                log.info("Mensaje %s ya procesado; se ignora.", wa_message_id)
                return
            session.add(MensajeProcesado(wa_message_id=wa_message_id))
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                log.info("Mensaje %s reclamado por otra ejecucion; se ignora.", wa_message_id)
                return

        respuesta = agente.procesar_mensaje(session, telefono, texto, wa_message_id)
        whatsapp.send_text(telefono, respuesta)
    except Exception:  # noqa: BLE001 - nunca tragar en silencio
        log.exception("Error procesando mensaje entrante de %s", telefono)
    finally:
        session.close()


@router.post("/webhook")
async def recibir_webhook(request: Request, background: BackgroundTasks) -> Response:
    """Recibe eventos de Meta. Devuelve 200 de inmediato y procesa en background.

    Si hay App Secret configurado, valida la firma HMAC antes de procesar (rechaza
    con 403 los payloads que no provienen de Meta).
    """
    body = await request.body()

    if settings.webhook_signature_required and not _firma_valida(
        body, request.headers.get("X-Hub-Signature-256")
    ):
        log.warning("Webhook con firma invalida o ausente; se rechaza (403)")
        return Response(status_code=403)

    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        log.warning("Payload de webhook no es JSON valido")
        return Response(content='{"status": "ignored"}', media_type="application/json")

    for telefono, texto, wa_id in _extraer_mensajes(payload):
        background.add_task(_procesar_entrante, telefono, texto, wa_id)

    return Response(content='{"status": "received"}', media_type="application/json")


# /dev/simulate es un atajo de pruebas que ejecuta el agente SIN autenticacion ni
# firma. Solo se expone en modo desarrollo (DEBUG=true); nunca en produccion.
if settings.debug:

    @router.post("/dev/simulate", response_model=SimulateResponse)
    async def simular_mensaje(req: SimulateRequest) -> SimulateResponse:
        """Procesa un mensaje de forma SINCRONA y devuelve la respuesta (pruebas locales)."""
        session = SessionLocal()
        try:
            respuesta = agente.procesar_mensaje(session, req.telefono, req.texto)
            return SimulateResponse(respuesta=respuesta)
        finally:
            session.close()
