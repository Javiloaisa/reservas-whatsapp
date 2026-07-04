"""Entrypoint de cron: recordatorios a los clientes con cita MAÑANA.

    python -m scripts.recordatorios

Ejecutar una vez al dia a las 10:00 hora local, TODOS los dias (el domingo
avisa las citas del lunes). Envia la plantilla `recordatorio_cita` a cada
cita confirmada de mañana y la marca como avisada (idempotente).
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
