"""Entrypoint de cron: resumen de fin de dia al podologo (L-V a las 20:30).

    python -m scripts.resumen_diario

Envia por Telegram (al chat `TELEGRAM_CHAT_ID` / config `telegram_chat_id`) las
citas RESERVADAS hoy (la actividad del dia del agente), ordenadas por fecha de
cita. Va por Telegram porque WhatsApp no permite escribirse al propio numero de
coexistencia (decision del usuario 2026-07-07).
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
        print("resumen_diario: enviado" if ok else "resumen_diario: omitido (sin telegram_chat_id)")
    finally:
        session.close()


if __name__ == "__main__":
    main()
