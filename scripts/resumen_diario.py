"""Entrypoint de cron: resumen de fin de dia al podologo (L-V a las 20:30).

    python -m scripts.resumen_diario

Envia al numero de `config.podologo_whatsapp` la plantilla con las citas
RESERVADAS hoy (la actividad del dia del agente), ordenadas por fecha de cita.
"""

from __future__ import annotations

from app import logconf
from app.db import SessionLocal
from app.services import avisos


def main() -> None:
    logconf.setup()
    session = SessionLocal()
    try:
        ok = avisos.enviar_resumen_diario(session)
        print("resumen_diario: enviado" if ok else "resumen_diario: omitido (sin podologo_whatsapp)")
    finally:
        session.close()


if __name__ == "__main__":
    main()
