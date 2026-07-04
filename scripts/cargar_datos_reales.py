"""Carga los servicios y horarios REALES (§13) en una BD que ya tiene placeholders.

    python -m scripts.cargar_datos_reales

A diferencia del seed (que se omite si ya hay datos), este script:
- Desactiva los servicios existentes cuyo nombre no este en la lista real
  (no los borra: pueden estar referenciados por citas de prueba).
- Inserta los servicios reales que falten (por nombre); si ya existen,
  sincroniza duracion/buffer/precio con la lista del seed y los reactiva.
  La lista de scripts/seed.py es la fuente de verdad: cuando el podologo
  aporte precios, actualizarlos alli y re-ejecutar este script.
- Reemplaza TODOS los horarios por el horario semanal real.

Es idempotente: re-ejecutarlo deja el mismo estado.
"""

from __future__ import annotations

from sqlalchemy import delete, select

from app.db import SessionLocal
from app.models import Horario, Servicio
from scripts.seed import HORARIO_SEMANAL, SERVICIOS


def main() -> None:
    session = SessionLocal()
    try:
        nombres_reales = {nombre for nombre, *_ in SERVICIOS}
        existentes = {s.nombre: s for s in session.scalars(select(Servicio)).all()}

        for nombre, servicio in existentes.items():
            if nombre not in nombres_reales and servicio.activo:
                servicio.activo = False
                print(f"servicio desactivado (placeholder): {nombre}")

        for nombre, dur, buf, precio in SERVICIOS:
            servicio = existentes.get(nombre)
            if servicio is None:
                session.add(
                    Servicio(nombre=nombre, duracion_min=dur, buffer_min=buf, precio=precio, activo=True)
                )
                print(f"servicio creado: {nombre} ({dur} min)")
            else:
                servicio.duracion_min, servicio.buffer_min = dur, buf
                servicio.precio, servicio.activo = precio, True
                print(f"servicio actualizado: {nombre} ({dur} min)")

        session.execute(delete(Horario))
        n = 0
        for dia, franjas in HORARIO_SEMANAL.items():
            for ini, fin in franjas:
                session.add(Horario(dia_semana=dia, hora_inicio=ini, hora_fin=fin))
                n += 1
        print(f"horarios: reemplazados por {n} franjas reales (§13)")

        session.commit()
        print("datos reales: OK")
    finally:
        session.close()


if __name__ == "__main__":
    main()
