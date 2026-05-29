"""Entrypoint de cron: enviar recordatorios 24 h (ejecutar cada hora).

    python -m scripts.recordatorios

Selecciona las citas confirmadas cuyo inicio cae en [ahora+23h, ahora+25h], envia la
plantilla de recordatorio y las marca como enviadas (idempotente).
"""

from __future__ import annotations

from app import logconf
from app.db import SessionLocal
from app.services import avisos


def main() -> None:
    logconf.setup()
    session = SessionLocal()
    try:
        n = avisos.enviar_recordatorios(session)
        print(f"recordatorios: enviados {n}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
