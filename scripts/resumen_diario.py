"""Entrypoint de cron: enviar el resumen diario al podologo (ejecutar 1 vez/dia, p.ej. 20:30).

    python -m scripts.resumen_diario

Envia al numero de `config.podologo_whatsapp` la plantilla con las citas reservadas hoy.
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
