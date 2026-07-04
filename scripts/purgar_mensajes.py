"""Entrypoint de cron: purgar mensajes y log_sombra antiguos (§14 v2 - RGPD).

    python -m scripts.purgar_mensajes

Ejecutar mensualmente. El periodo de retencion se configura con
`config.retencion_mensajes_meses` (default 12 meses). No toca `citas` ni
`clientes`: solo el contenido de las conversaciones.
"""

from __future__ import annotations

from app import logconf
from app.db import SessionLocal
from app.services import retencion


def main() -> None:
    logconf.setup()
    session = SessionLocal()
    try:
        n_mensajes, n_log = retencion.purgar_mensajes_antiguos(session)
        print(f"purgar_mensajes: {n_mensajes} mensajes, {n_log} registros de log_sombra")
    finally:
        session.close()


if __name__ == "__main__":
    main()
