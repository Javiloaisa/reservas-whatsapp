"""Avisos programados (§9 del CLAUDE.md): recordatorios 24 h y resumen diario.

Ambos son mensajes iniciados por el negocio fuera de la ventana de 24 h, por lo que
DEBEN enviarse con plantillas aprobadas (§8 / §A.4), no con texto libre. En modo stub
(sin credenciales de WhatsApp) las plantillas se registran en consola.

Idempotencia:
- Recordatorios: cada cita se marca `recordatorio='enviado'` tras enviarse; reejecutar
  no reenvia.
- El commit es por cita, de modo que un fallo aislado no bloquea al resto.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import (
    ESTADO_CANCELADA,
    ESTADO_CONFIRMADA,
    RECORDATORIO_ENVIADO,
    RECORDATORIO_PENDIENTE,
    Cita,
)
from app.services import whatsapp
from app.services.config_repo import get_config, get_timezone

log = logging.getLogger("avisos")

# Nombres de las plantillas aprobadas en WhatsApp Manager (§8).
TEMPLATE_RECORDATORIO = "recordatorio_cita"  # vars: {{1}}=nombre {{2}}=servicio {{3}}=hora
TEMPLATE_RESUMEN = "resumen_dia"  # var: {{1}}=resumen (fin de dia; sustituye a resumen_diario)


def _body(*textos: str) -> list[dict[str, Any]]:
    """Componente 'body' de una plantilla con parametros de texto posicionales."""
    return [{"type": "body", "parameters": [{"type": "text", "text": t} for t in textos]}]


# --------------------------------------------------------------------------- #
#  Recordatorios a los clientes con cita MAÑANA
# --------------------------------------------------------------------------- #
def enviar_recordatorios(session: Session) -> int:
    """Envia recordatorio a todas las citas confirmadas de MAÑANA (dia local).

    Ejecutar una vez al dia a las 10:00 hora local, TODOS los dias (el domingo
    avisa las citas del lunes). Decision del usuario 2026-07-04; la plantilla
    dice "mañana", coherente con enviarse siempre el dia antes.
    Devuelve el numero de recordatorios enviados.
    """
    tz = get_timezone(session)
    manana = dt.datetime.now(tz).date() + dt.timedelta(days=1)
    desde = dt.datetime.combine(manana, dt.time.min, tzinfo=tz).astimezone(dt.timezone.utc)
    hasta = desde + dt.timedelta(days=1)
    lang = settings.whatsapp_template_lang

    citas = list(
        session.scalars(
            select(Cita).where(
                Cita.estado == ESTADO_CONFIRMADA,
                Cita.recordatorio == RECORDATORIO_PENDIENTE,
                Cita.inicio >= desde,
                Cita.inicio < hasta,
            )
        ).all()
    )

    enviados = 0
    for cita in citas:
        nombre = cita.cliente.nombre or "cliente"
        hora = cita.inicio.astimezone(tz).strftime("%H:%M")
        try:
            whatsapp.send_template(
                to=cita.cliente.telefono,
                name=TEMPLATE_RECORDATORIO,
                lang=lang,
                components=_body(nombre, cita.servicio.nombre, hora),
            )
            cita.recordatorio = RECORDATORIO_ENVIADO
            session.commit()
            enviados += 1
        except Exception:  # noqa: BLE001 - no bloquear al resto; no tragar en silencio
            session.rollback()
            log.exception("Fallo enviando recordatorio de la cita %s", cita.id)

    log.info("Recordatorios enviados: %s (de %s candidatas)", enviados, len(citas))
    return enviados


# --------------------------------------------------------------------------- #
#  Resumen diario (al podologo)
# --------------------------------------------------------------------------- #
def _resumen_texto(citas: list[Cita], tz) -> str:  # noqa: ANN001
    """Resumen en una sola linea (las plantillas no admiten saltos de linea)."""
    if not citas:
        return "No se han reservado citas hoy."
    partes = []
    for c in citas:
        ini = c.inicio.astimezone(tz)
        nombre = c.cliente.nombre or c.cliente.telefono
        partes.append(f"{ini.strftime('%d/%m %H:%M')} {c.servicio.nombre} ({nombre})")
    return f"{len(citas)} cita(s): " + "; ".join(partes)


def enviar_resumen_diario(session: Session) -> bool:
    """Envia al podologo, al final del dia, las citas RESERVADAS hoy (decision del
    usuario 2026-07-04: resumen de la actividad del agente, no agenda de manana).

    Devuelve True si se envio, False si no hay numero de podologo configurado.
    """
    tz = get_timezone(session)
    hoy_local = dt.datetime.now(tz).date()
    inicio_dia = dt.datetime.combine(hoy_local, dt.time.min, tzinfo=tz).astimezone(dt.timezone.utc)
    fin_dia = inicio_dia + dt.timedelta(days=1)

    citas = list(
        session.scalars(
            select(Cita)
            .where(
                Cita.creado_en >= inicio_dia,
                Cita.creado_en < fin_dia,
                Cita.estado != ESTADO_CANCELADA,
            )
            .order_by(Cita.inicio)
        ).all()
    )

    podologo = get_config(session, "podologo_whatsapp")
    if not podologo:
        log.warning("Resumen diario: 'podologo_whatsapp' no configurado; no se envia.")
        return False

    resumen = _resumen_texto(citas, tz)
    whatsapp.send_template(
        to=podologo,
        name=TEMPLATE_RESUMEN,
        lang=settings.whatsapp_template_lang,
        components=_body(resumen),
    )
    log.info("Resumen diario enviado al podologo (%s citas).", len(citas))
    return True
