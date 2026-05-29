"""Configuracion de logging compartida por la app y los scripts de cron.

Asi los entrypoints (recordatorios, resumen, seed, chat_local) muestran tambien los
logs INFO (incluidos los del modo stub de WhatsApp/Calendar) y cron.log resulta util.
"""

from __future__ import annotations

import logging


def setup(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
