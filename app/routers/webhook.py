"""Webhook de WhatsApp (YCloud o Meta directa) y endpoint de prueba local.

- GET  /webhook : verificacion del webhook de Meta (hub.challenge). No aplica a YCloud.
- POST /webhook : recepcion de eventos. Responde 200 rapido y procesa cada evento en
  background siguiendo el flujo del §4 del CLAUDE.md v2 (`services/pipeline.py`).
- POST /dev/simulate : inyecta un mensaje directamente al agente (sin clasificador,
  modo humano ni modo sombra) para probarlo localmente sin webhook real.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import PlainTextResponse

from app.config import settings
from app.db import SessionLocal
from app.schemas import SimulateRequest, SimulateResponse
from app.services import agente, pipeline, whatsapp

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


def _token_ycloud_valido(request: Request) -> bool:
    """Token secreto de YCloud en la query string del webhook (§3/§8 v2).

    YCloud no siempre ofrece firma HMAC del payload; la mitigacion documentada es
    un token compartido en la URL configurada en su panel.
    """
    if not settings.ycloud_webhook_secret:
        return True  # sin secreto configurado (desarrollo): no se exige
    return request.query_params.get("token") == settings.ycloud_webhook_secret


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


@router.post("/webhook")
async def recibir_webhook(request: Request, background: BackgroundTasks) -> Response:
    """Recibe eventos del proveedor activo. Devuelve 200 de inmediato y procesa en
    background (§4 v2): cada evento se enruta a `pipeline.procesar_evento`.
    """
    body = await request.body()

    if settings.ycloud_enabled:
        if not _token_ycloud_valido(request):
            log.warning("Webhook de YCloud con token invalido o ausente; se rechaza (403)")
            return Response(status_code=403)
    elif settings.webhook_signature_required and not _firma_valida(
        body, request.headers.get("X-Hub-Signature-256")
    ):
        log.warning("Webhook con firma invalida o ausente; se rechaza (403)")
        return Response(status_code=403)

    # Payload crudo solo en DEBUG (§14: contiene datos personales, nunca en INFO).
    log.debug("Payload de webhook: %s", body.decode("utf-8", errors="replace"))

    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        log.warning("Payload de webhook no es JSON valido")
        return Response(content='{"status": "ignored"}', media_type="application/json")

    for evento in whatsapp.parse_webhook(payload):
        background.add_task(pipeline.procesar_evento, evento)

    return Response(content='{"status": "received"}', media_type="application/json")


# /dev/simulate es un atajo de pruebas que ejecuta el agente SIN autenticacion ni
# firma, y SIN pasar por el clasificador, modo humano o modo sombra del pipeline
# (§4 v2) — es una via directa al agente para probarlo en local. Solo se expone en
# modo desarrollo (DEBUG=true); nunca en produccion.
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
