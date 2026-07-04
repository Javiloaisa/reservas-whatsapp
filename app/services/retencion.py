"""Retencion de datos personales (§14 v2 - RGPD).

Los mensajes contienen datos personales y potencialmente de salud (el cliente
puede escribir cualquier cosa en el chat). Se purgan pasado un periodo
configurable (`config.retencion_mensajes_meses`, default 12 meses) para
minimizar la retencion. `citas` y `clientes` no se tocan: son el registro de
negocio que la clinica necesita conservar.
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import LogSombra, Mensaje
from app.services.config_repo import get_config

log = logging.getLogger("retencion")

DEFAULT_MESES = 12
DIAS_POR_MES = 30  # aproximacion suficiente para una purga de mantenimiento


def _meses_retencion(session: Session) -> int:
    valor = get_config(session, "retencion_mensajes_meses", str(DEFAULT_MESES))
    try:
        return int(valor)
    except (TypeError, ValueError):
        return DEFAULT_MESES


def purgar_mensajes_antiguos(session: Session) -> tuple[int, int]:
    """Borra `mensajes` y `log_sombra` anteriores al corte de retencion.

    Devuelve (mensajes_borrados, log_sombra_borrados).
    """
    meses = _meses_retencion(session)
    corte = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=meses * DIAS_POR_MES)

    n_mensajes = session.execute(delete(Mensaje).where(Mensaje.creado_en < corte)).rowcount
    n_log = session.execute(delete(LogSombra).where(LogSombra.creado_en < corte)).rowcount
    session.commit()

    log.info(
        "Retencion: purgados %s mensajes y %s registros de log_sombra (> %s meses)",
        n_mensajes, n_log, meses,
    )
    return n_mensajes, n_log
