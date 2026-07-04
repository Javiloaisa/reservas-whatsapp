"""Orquestacion del flujo por evento de webhook (§4 del CLAUDE.md v2).

Punto de entrada unico llamado desde el webhook (en background) para cada evento
neutral ya parseado por `services/whatsapp.parse_webhook`: eco del podologo,
mensaje de un cliente, u otro evento que se ignora.

Cada llamada abre su propia sesion (se ejecuta en una background task, fuera del
ciclo de vida del request).
"""

from __future__ import annotations

import datetime as dt
import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import SessionLocal
from app.models import CLASIFICACION_CITA, ROL_BOT, ROL_CLIENTE, ROL_PODOLOGO, LogSombra, Mensaje, MensajeProcesado
from app.services import agente, clasificador, whatsapp
from app.services.config_repo import bot_activo, intervalo_modo_humano_horas, palabra_reactivacion
from app.services.whatsapp import EcoSaliente, Evento, MensajeEntrante, Otro

log = logging.getLogger("pipeline")

# Ventana para reconocer el eco de un envio propio del bot y no activar modo humano (§5 v2).
VENTANA_ECO_PROPIO_SEGUNDOS = 60


def _reclamar_evento(session: Session, message_id: str) -> bool:
    """Reclama un `message_id` para procesarlo una unica vez. True si es la primera vez."""
    if session.get(MensajeProcesado, message_id) is not None:
        return False
    session.add(MensajeProcesado(wa_message_id=message_id))
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        return False
    return True


def procesar_evento(evento: Evento) -> None:
    """Procesa un evento de webhook siguiendo el flujo del §4. Nunca lanza."""
    if isinstance(evento, Otro):
        return

    session = SessionLocal()
    try:
        if evento.message_id is not None and not _reclamar_evento(session, evento.message_id):
            log.info("Evento %s ya procesado; se ignora.", evento.message_id)
            return

        if isinstance(evento, EcoSaliente):
            _procesar_eco(session, evento)
        elif isinstance(evento, MensajeEntrante):
            _procesar_entrante(session, evento)
    except Exception:  # noqa: BLE001 - nunca tragar en silencio
        log.exception("Error procesando evento de webhook")
    finally:
        session.close()


# --------------------------------------------------------------------------- #
#  Ecos de la app del podologo (coexistencia) -> modo humano (§5 v2)
# --------------------------------------------------------------------------- #
def _es_eco_propio(session: Session, cliente_id: int, texto: str, ahora: dt.datetime) -> bool:
    """True si el eco coincide con un envio del propio bot hecho hace <60s (§5 v2)."""
    ultimo = session.scalar(
        select(Mensaje)
        .where(Mensaje.cliente_id == cliente_id, Mensaje.rol == ROL_BOT)
        .order_by(Mensaje.creado_en.desc(), Mensaje.id.desc())
    )
    if ultimo is None or ultimo.contenido.strip() != texto.strip():
        return False
    return 0 <= (ahora - ultimo.creado_en).total_seconds() <= VENTANA_ECO_PROPIO_SEGUNDOS


def _procesar_eco(session: Session, eco: EcoSaliente) -> None:
    cliente = agente.resolver_cliente(session, eco.telefono_destino)
    ahora = dt.datetime.now(dt.timezone.utc)

    if _es_eco_propio(session, cliente.id, eco.texto, ahora):
        log.info("Eco del propio envio del bot a %s; se ignora.", eco.telefono_destino)
        return

    session.add(
        Mensaje(
            cliente_id=cliente.id,
            rol=ROL_PODOLOGO,
            contenido=eco.texto,
            message_id_proveedor=eco.message_id,
        )
    )

    if eco.texto.strip().lower() == palabra_reactivacion(session).lower():
        cliente.modo_humano_hasta = None
        log.info("Modo humano reactivado por palabra clave para %s", eco.telefono_destino)
    else:
        cliente.modo_humano_hasta = ahora + dt.timedelta(hours=intervalo_modo_humano_horas(session))
        log.info(
            "Modo humano activado para %s hasta %s", eco.telefono_destino, cliente.modo_humano_hasta
        )

    session.commit()


# --------------------------------------------------------------------------- #
#  Mensajes de clientes -> modo sombra / modo humano / clasificador / agente
# --------------------------------------------------------------------------- #
def _registrar_silencio(
    session: Session, cliente_id: int, texto: str, message_id: str | None, clasificacion: str | None
) -> None:
    session.add(
        Mensaje(
            cliente_id=cliente_id,
            rol=ROL_CLIENTE,
            contenido=texto,
            clasificacion=clasificacion,
            message_id_proveedor=message_id,
        )
    )
    session.commit()


def _procesar_entrante(session: Session, msg: MensajeEntrante) -> None:
    cliente = agente.resolver_cliente(session, msg.telefono, nombre=msg.nombre_perfil)

    if not bot_activo(session):
        _ejecutar_en_sombra(session, cliente.id, msg)
        return

    ahora = dt.datetime.now(dt.timezone.utc)
    if cliente.modo_humano_hasta is not None and cliente.modo_humano_hasta > ahora:
        _registrar_silencio(session, cliente.id, msg.texto, msg.message_id, clasificacion=None)
        log.info("Modo humano activo para %s; mensaje registrado sin respuesta.", msg.telefono)
        return

    clasificacion = clasificador.clasificar(session, cliente.id, msg.texto)
    if clasificacion != CLASIFICACION_CITA:
        _registrar_silencio(session, cliente.id, msg.texto, msg.message_id, clasificacion)
        log.info("Mensaje de %s clasificado como %s; silencio.", msg.telefono, clasificacion)
        return

    respuesta = agente.procesar_mensaje(
        session, msg.telefono, msg.texto, wa_message_id=msg.message_id, clasificacion=clasificacion
    )
    whatsapp.send_text(msg.telefono, respuesta)


def _ejecutar_en_sombra(session: Session, cliente_id: int, msg: MensajeEntrante) -> None:
    """Modo sombra global (§12 v2): corre clasificador + agente en seco, registra en
    `mensajes` y `log_sombra` la respuesta que se HABRIA enviado, sin enviar nada."""
    clasificacion = clasificador.clasificar(session, cliente_id, msg.texto)

    respuesta: str | None = None
    if clasificacion == CLASIFICACION_CITA:
        respuesta = agente.procesar_mensaje(
            session,
            msg.telefono,
            msg.texto,
            wa_message_id=msg.message_id,
            clasificacion=clasificacion,
            dry_run=True,
        )
    else:
        _registrar_silencio(session, cliente_id, msg.texto, msg.message_id, clasificacion)

    session.add(
        LogSombra(
            cliente_id=cliente_id,
            mensaje_entrante=msg.texto,
            clasificacion=clasificacion,
            respuesta_no_enviada=respuesta,
        )
    )
    session.commit()
    log.info("Modo sombra: %s -> %s", msg.telefono, clasificacion)
