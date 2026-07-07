"""Envio por Telegram Bot API (§11 v2, decision del usuario 2026-07-07).

El resumen diario va al PROPIO podologo. No puede ir por WhatsApp: WhatsApp no deja
enviarse un mensaje al mismo numero desde el que se opera (coexistencia). La
alternativa cero-coste que contempla el CLAUDE.md §11 es Telegram, el mismo patron
que usa el proyecto crypto-agent.

Credenciales en `.env`: `TELEGRAM_TOKEN` (del @BotFather) y `TELEGRAM_CHAT_ID` (el
chat del podologo con el bot). Sin token opera en "modo stub": registra en consola
en vez de llamar a la API (igual que WhatsApp/Calendar).
"""

from __future__ import annotations

import logging

import httpx

from app.config import settings

log = logging.getLogger("telegram")

_TIMEOUT = httpx.Timeout(10.0)
# Telegram corta los mensajes a 4096 caracteres; dejamos margen.
_LIMITE = 3800


def _url() -> str:
    return f"https://api.telegram.org/bot{settings.telegram_token}/sendMessage"


def _enviar_trozo(chat_id: str, texto: str) -> None:
    resp = httpx.post(
        _url(),
        json={"chat_id": chat_id, "text": texto, "disable_web_page_preview": True},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()


def enviar(chat_id: str, texto: str) -> bool:
    """Envia `texto` al chat de Telegram. Devuelve True si se envio (o se registro en stub).

    Trocea por saltos de linea si excede el limite de Telegram. No traga errores en
    silencio: loguea y devuelve False ante fallo de red/HTTP.
    """
    if not settings.telegram_enabled:
        log.info("[TELEGRAM STUB] -> %s : %s", chat_id, texto)
        return True

    trozos: list[str] = []
    actual = ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > _LIMITE and actual:
            trozos.append(actual)
            actual = ""
        actual = f"{actual}\n{linea}" if actual else linea
    if actual:
        trozos.append(actual)

    try:
        for trozo in trozos:
            _enviar_trozo(chat_id, trozo)
    except httpx.HTTPStatusError as exc:
        log.error("Telegram error %s al enviar a %s: %s", exc.response.status_code, chat_id, exc.response.text)
        return False
    except httpx.HTTPError as exc:
        log.error("Fallo de red enviando a Telegram (%s): %s", chat_id, exc)
        return False
    return True
