"""Configuracion de logging compartida por la app y los scripts de cron.

Asi los entrypoints (recordatorios, resumen, seed, chat_local) muestran tambien los
logs INFO (incluidos los del modo stub de WhatsApp/Calendar) y cron.log resulta util.

Nivel configurable con la variable de entorno LOG_LEVEL (p. ej. DEBUG para ver los
payloads crudos de webhook durante un diagnostico, §14: nunca en INFO por RGPD).
"""

from __future__ import annotations

import logging
import os


def setup(level: int | None = None) -> None:
    if level is None:
        nombre = os.environ.get("LOG_LEVEL", "INFO").upper()
        level = getattr(logging, nombre, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-5s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
