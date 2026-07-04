"""Entrypoint de cron: enviar la agenda del dia al podologo (§11 v2: L-V a las 08:00).

    python -m scripts.resumen_diario

Envia al numero de `config.podologo_whatsapp` la plantilla con las citas cuyo INICIO
cae hoy (hora, nombre, servicio) - la agenda del dia, no las reservas hechas hoy.
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
