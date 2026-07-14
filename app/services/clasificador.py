"""Clasificador de intencion cita / no_cita / duda (§6 del CLAUDE.md v2).

Llamada rapida a Claude (modelo barato propio —Haiku—, max_tokens bajo, temperature 0)
con `prompts/system_clasificador.md`. Politica: solo "cita" pasa al agente; ante
cualquier problema (JSON invalido, error de API) se devuelve "duda" => silencio.

Sin ANTHROPIC_API_KEY (desarrollo) devuelve "cita" para poder probar el pipeline
completo en local; en produccion la clave siempre esta configurada.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    CLASIFICACION_CITA,
    CLASIFICACION_DUDA,
    CLASIFICACION_NO_CITA,
    ROL_BOT,
    ROL_CLIENTE,
    ROL_PODOLOGO,
    Mensaje,
)
from app.services.config_repo import modelo_clasificador
from app.services.whatsapp import es_no_texto

log = logging.getLogger("clasificador")

CONTEXTO_N = 5  # ultimos mensajes que se pasan como contexto
MAX_TOKENS = 50

_ETIQUETAS = {CLASIFICACION_CITA, CLASIFICACION_NO_CITA, CLASIFICACION_DUDA}
_ROL_TAG = {ROL_CLIENTE: "[cliente]", ROL_BOT: "[bot]", ROL_PODOLOGO: "[podologo]"}

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


@lru_cache
def _system() -> str:
    return (_PROMPTS_DIR / "system_clasificador.md").read_text(encoding="utf-8")


@lru_cache
def _client():  # noqa: ANN202
    import anthropic

    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def _contexto(session: Session, cliente_id: int, excluir_id: int | None = None) -> str:
    """Ultimos CONTEXTO_N mensajes del cliente, del mas antiguo al mas reciente."""
    stmt = (
        select(Mensaje)
        .where(Mensaje.cliente_id == cliente_id)
        .order_by(Mensaje.creado_en.desc(), Mensaje.id.desc())
        .limit(CONTEXTO_N + 1)
    )
    filas = [m for m in session.scalars(stmt).all() if m.id != excluir_id][:CONTEXTO_N]
    filas.reverse()
    return "\n".join(f"{_ROL_TAG.get(m.rol, '[cliente]')}: {m.contenido}" for m in filas)


def _parsear(texto: str) -> str | None:
    """Extrae {"intencion": ...} de la salida del modelo; None si no es parseable."""
    inicio, fin = texto.find("{"), texto.rfind("}")
    if inicio == -1 or fin <= inicio:
        return None
    try:
        etiqueta = json.loads(texto[inicio : fin + 1]).get("intencion", "")
    except (ValueError, AttributeError):
        return None
    return etiqueta if etiqueta in _ETIQUETAS else None


def clasificar(
    session: Session, cliente_id: int, texto: str, excluir_mensaje_id: int | None = None
) -> str:
    """Clasifica el mensaje nuevo de un cliente. Devuelve cita | no_cita | duda.

    `excluir_mensaje_id` evita duplicar en el CONTEXTO el mensaje nuevo si ya
    esta persistido en `mensajes`.
    """
    # Audios, imagenes, ubicaciones... llegan como marcador ('[audio]', '[imagen]'...)
    # y el clasificador SIEMPRE los trata como 'duda' (§6). Se resuelve aqui sin llamar
    # a la API: ahorra una llamada a Claude por cada mensaje no-texto.
    if es_no_texto(texto):
        return CLASIFICACION_DUDA

    if not settings.anthropic_enabled:
        return CLASIFICACION_CITA  # stub de desarrollo (ver docstring del modulo)

    contexto = _contexto(session, cliente_id, excluir_id=excluir_mensaje_id)
    entrada = ""
    if contexto:
        entrada += f"CONTEXTO (ultimos mensajes, del mas antiguo al mas reciente):\n{contexto}\n\n"
    entrada += f"MENSAJE NUEVO:\n[cliente]: {texto}"

    try:
        resp = _client().messages.create(
            model=modelo_clasificador(session),
            max_tokens=MAX_TOKENS,
            temperature=0,
            system=_system(),
            messages=[{"role": "user", "content": entrada}],
        )
        salida = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    except Exception:  # noqa: BLE001 - ante error de API: silencio (duda), nunca caerse
        log.exception("Error llamando al clasificador; se devuelve 'duda'")
        return CLASIFICACION_DUDA

    etiqueta = _parsear(salida)
    if etiqueta is None:
        log.warning("Salida no parseable del clasificador: %r", salida[:200])
        return CLASIFICACION_DUDA  # ante salida rara: silencio
    return etiqueta
