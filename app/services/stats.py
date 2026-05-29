"""Calculo de estadisticas para la API y el panel (citas por estado, top servicios, no-show)."""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import ESTADO_COMPLETADA, ESTADO_NO_SHOW, Cita, Servicio


def resumen_estadisticas(session: Session, periodo_dias: int = 30) -> dict[str, Any]:
    ahora = dt.datetime.now(dt.timezone.utc)
    desde = ahora - dt.timedelta(days=periodo_dias)

    por_estado = {
        estado: n
        for estado, n in session.execute(
            select(Cita.estado, func.count()).where(Cita.inicio >= desde).group_by(Cita.estado)
        ).all()
    }
    total = sum(por_estado.values())

    servicios_top = [
        {"servicio": nombre, "citas": n}
        for nombre, n in session.execute(
            select(Servicio.nombre, func.count())
            .join(Cita, Cita.servicio_id == Servicio.id)
            .where(Cita.inicio >= desde)
            .group_by(Servicio.nombre)
            .order_by(func.count().desc())
            .limit(5)
        ).all()
    ]

    no_show = por_estado.get(ESTADO_NO_SHOW, 0)
    finalizadas = no_show + por_estado.get(ESTADO_COMPLETADA, 0)
    tasa = round(no_show / finalizadas, 3) if finalizadas else 0.0

    return {
        "periodo_dias": periodo_dias,
        "total": total,
        "por_estado": por_estado,
        "servicios_top": servicios_top,
        "tasa_no_show": tasa,
    }
