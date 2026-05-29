"""REPL de terminal para probar el bot sin Meta ni Google.

Inyecta tus mensajes en el MISMO pipeline que usa el webhook
(`agente.procesar_mensaje`), con Claude real, logica de agenda real y DB real.
WhatsApp y Calendar quedan en modo stub (se registran en consola).

Uso:
    python -m scripts.chat_local                 # telefono de prueba por defecto
    python -m scripts.chat_local 34600111222     # telefono concreto

Escribe 'salir' para terminar.
"""

from __future__ import annotations

import sys

from app.config import settings
from app.db import SessionLocal
from app.services import agente


def main() -> None:
    telefono = sys.argv[1] if len(sys.argv) > 1 else "34600000000"
    if not settings.anthropic_enabled:
        print("AVISO: ANTHROPIC_API_KEY no configurada -> el bot respondera en modo eco.\n")

    print(f"Chat local con el agente (telefono={telefono}). Escribe 'salir' para terminar.\n")
    while True:
        try:
            texto = input("Tu: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not texto:
            continue
        if texto.lower() in {"salir", "exit", "quit"}:
            break

        session = SessionLocal()
        try:
            respuesta = agente.procesar_mensaje(session, telefono, texto)
        finally:
            session.close()
        print(f"Bot: {respuesta}\n")


if __name__ == "__main__":
    main()
