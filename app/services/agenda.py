"""Regla de negocio de la agenda (§7 del CLAUDE.md).

Unica via por la que se calcula disponibilidad y se mutan citas. El agente solo
puede tocar el estado a traves de estas funciones (via tool use), nunca directamente.

Tiempo:
- Los `horarios` se interpretan en la zona horaria de `config.timezone` (hora local
  de la clinica). Las citas y bloqueos se almacenan en UTC-aware.
- La disponibilidad se calcula en local y se devuelve como datetimes local-aware.
"""

from __future__ import annotations

import datetime as dt
import logging
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    ESTADO_CANCELADA,
    ESTADO_COMPLETADA,
    ESTADO_CONFIRMADA,
    ESTADO_NO_SHOW,
    RECORDATORIO_NO_APLICA,
    RECORDATORIO_PENDIENTE,
    Bloqueo,
    Cita,
    Cliente,
    Horario,
    Servicio,
)
from app.services import calendar_gcal
from app.services.config_repo import get_timezone

ESTADOS_VALIDOS = {ESTADO_CONFIRMADA, ESTADO_CANCELADA, ESTADO_COMPLETADA, ESTADO_NO_SHOW}

log = logging.getLogger("agenda")

# Paso de generacion de huecos candidatos (configurable).
PASO_MIN = 15


class AgendaError(Exception):
    """Error de negocio de la agenda (mensaje apto para mostrar al cliente)."""


class ServicioInvalido(AgendaError):
    pass


class SlotNoDisponible(AgendaError):
    pass


class CitaNoEncontrada(AgendaError):
    pass


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _overlap(a0: dt.datetime, a1: dt.datetime, b0: dt.datetime, b1: dt.datetime) -> bool:
    """True si los intervalos [a0,a1) y [b0,b1) se solapan."""
    return a0 < b1 and b0 < a1


def _servicio_activo(session: Session, servicio_id: int) -> Servicio:
    servicio = session.get(Servicio, servicio_id)
    if servicio is None or not servicio.activo:
        raise ServicioInvalido(f"Servicio {servicio_id} no existe o no esta activo.")
    return servicio


def _citas_del_rango(session: Session, desde_utc: dt.datetime, hasta_utc: dt.datetime) -> list[Cita]:
    """Citas no canceladas que intersecan [desde, hasta)."""
    stmt = (
        select(Cita)
        .where(
            Cita.estado != ESTADO_CANCELADA,
            Cita.inicio < hasta_utc,
            Cita.fin > desde_utc,
        )
        .order_by(Cita.inicio)
    )
    return list(session.scalars(stmt).all())


def _bloqueos_del_rango(session: Session, desde_utc: dt.datetime, hasta_utc: dt.datetime) -> list[Bloqueo]:
    stmt = select(Bloqueo).where(Bloqueo.inicio < hasta_utc, Bloqueo.fin > desde_utc)
    return list(session.scalars(stmt).all())


def _reservado(cita: Cita) -> tuple[dt.datetime, dt.datetime]:
    """Intervalo ocupado por una cita = [inicio, fin + buffer del servicio]."""
    buffer = dt.timedelta(minutes=cita.servicio.buffer_min)
    return cita.inicio, cita.fin + buffer


def huecos_libres(
    session: Session,
    fecha: dt.date,
    servicio_id: int,
    tz: ZoneInfo | None = None,
) -> list[dt.datetime]:
    """Devuelve los inicios de hueco validos (local-aware) para un servicio en una fecha.

    Respeta horarios de apertura, bloqueos, citas existentes y el buffer de limpieza.
    """
    tz = tz or get_timezone(session)
    servicio = _servicio_activo(session, servicio_id)
    dur = dt.timedelta(minutes=servicio.duracion_min)
    buf = dt.timedelta(minutes=servicio.buffer_min)
    paso = dt.timedelta(minutes=PASO_MIN)

    franjas = list(
        session.scalars(
            select(Horario)
            .where(Horario.dia_semana == fecha.weekday())
            .order_by(Horario.hora_inicio)
        ).all()
    )
    if not franjas:
        return []

    # Rango del dia en local -> UTC para consultar la DB.
    dia_inicio_local = dt.datetime.combine(fecha, dt.time.min, tzinfo=tz)
    dia_fin_local = dia_inicio_local + dt.timedelta(days=1)
    desde_utc = dia_inicio_local.astimezone(dt.timezone.utc)
    hasta_utc = dia_fin_local.astimezone(dt.timezone.utc)

    reservados = [_reservado(c) for c in _citas_del_rango(session, desde_utc, hasta_utc)]
    bloqueos = [(b.inicio, b.fin) for b in _bloqueos_del_rango(session, desde_utc, hasta_utc)]
    ahora_utc = _utcnow()

    huecos: list[dt.datetime] = []
    for franja in franjas:
        franja_ini = dt.datetime.combine(fecha, franja.hora_inicio, tzinfo=tz).astimezone(dt.timezone.utc)
        franja_fin = dt.datetime.combine(fecha, franja.hora_fin, tzinfo=tz).astimezone(dt.timezone.utc)

        s = franja_ini
        while s + dur <= franja_fin:
            appt_ini, appt_fin = s, s + dur
            reserva_fin = s + dur + buf  # incluye buffer propio para separar de la siguiente cita

            es_pasado = appt_ini <= ahora_utc
            choca_cita = any(_overlap(appt_ini, reserva_fin, r0, r1) for r0, r1 in reservados)
            choca_bloqueo = any(_overlap(appt_ini, appt_fin, b0, b1) for b0, b1 in bloqueos)

            if not (es_pasado or choca_cita or choca_bloqueo):
                huecos.append(s.astimezone(tz))
            s += paso

    return huecos


def _slot_libre(
    session: Session,
    servicio: Servicio,
    inicio_utc: dt.datetime,
    fin_utc: dt.datetime,
    tz: ZoneInfo,
    excluir_cita_id: int | None = None,
) -> bool:
    """Revalida que [inicio, fin] sea un hueco valido (horario, bloqueos, citas)."""
    fecha_local = inicio_utc.astimezone(tz).date()
    buf = dt.timedelta(minutes=servicio.buffer_min)

    # 1) Dentro de alguna franja de apertura del dia.
    franjas = list(
        session.scalars(
            select(Horario).where(Horario.dia_semana == fecha_local.weekday())
        ).all()
    )
    dentro = False
    for franja in franjas:
        f_ini = dt.datetime.combine(fecha_local, franja.hora_inicio, tzinfo=tz).astimezone(dt.timezone.utc)
        f_fin = dt.datetime.combine(fecha_local, franja.hora_fin, tzinfo=tz).astimezone(dt.timezone.utc)
        if f_ini <= inicio_utc and fin_utc <= f_fin:
            dentro = True
            break
    if not dentro:
        return False

    # 2) No solapa bloqueos.
    for b in _bloqueos_del_rango(session, inicio_utc, fin_utc):
        if _overlap(inicio_utc, fin_utc, b.inicio, b.fin):
            return False

    # 3) No solapa citas existentes (ampliadas con buffer en ambos lados).
    for cita in _citas_del_rango(session, inicio_utc - buf, fin_utc + buf):
        if excluir_cita_id is not None and cita.id == excluir_cita_id:
            continue
        r0, r1 = _reservado(cita)
        if _overlap(inicio_utc, fin_utc + buf, r0, r1):
            return False

    return True


def _resolver_cliente(session: Session, telefono: str, nombre: str | None) -> Cliente:
    cliente = session.scalar(select(Cliente).where(Cliente.telefono == telefono))
    if cliente is None:
        cliente = Cliente(telefono=telefono, nombre=nombre)
        session.add(cliente)
        session.flush()
    elif nombre and not cliente.nombre:
        cliente.nombre = nombre
    return cliente


def _parse_inicio(inicio_iso: str, tz: ZoneInfo) -> dt.datetime:
    """Parsea el ISO recibido del agente a UTC-aware. Si es naive, asume hora local."""
    try:
        parsed = dt.datetime.fromisoformat(inicio_iso)
    except ValueError as exc:
        raise SlotNoDisponible(f"Fecha/hora invalida: {inicio_iso!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=tz)
    return parsed.astimezone(dt.timezone.utc)


def _validar_hueco(
    session: Session, servicio_id: int, inicio_iso: str
) -> tuple[Servicio, dt.datetime, dt.datetime]:
    """Valida (sin escribir) que el hueco este libre. Usado por `crear_cita` y por el
    modo sombra (`simular_crear_cita`, §12 v2) para revisar disponibilidad en seco."""
    tz = get_timezone(session)
    servicio = _servicio_activo(session, servicio_id)
    inicio_utc = _parse_inicio(inicio_iso, tz)
    fin_utc = inicio_utc + dt.timedelta(minutes=servicio.duracion_min)

    if inicio_utc <= _utcnow():
        raise SlotNoDisponible("Esa hora ya ha pasado.")
    if not _slot_libre(session, servicio, inicio_utc, fin_utc, tz):
        raise SlotNoDisponible("Ese hueco ya no esta disponible.")
    return servicio, inicio_utc, fin_utc


def simular_crear_cita(session: Session, servicio_id: int, inicio_iso: str) -> dict:
    """Version de solo lectura de `crear_cita` para el modo sombra (§12 v2):
    valida el hueco pero no escribe en BD ni en Calendar."""
    tz = get_timezone(session)
    _servicio, inicio_utc, _fin_utc = _validar_hueco(session, servicio_id, inicio_iso)
    return {"inicio": inicio_utc.astimezone(tz).strftime("%Y-%m-%d %H:%M")}


def crear_cita(
    session: Session,
    telefono: str,
    servicio_id: int,
    inicio_iso: str,
    nombre: str | None = None,
) -> Cita:
    """Crea una cita revalidando el hueco dentro de la transaccion (anti doble reserva).

    En PostgreSQL el constraint EXCLUDE es la red de seguridad definitiva; en SQLite
    la revalidacion + serializacion de escrituras cubre el caso secuencial.
    Tras insertar, sincroniza con Google Calendar (best-effort).
    """
    servicio, inicio_utc, fin_utc = _validar_hueco(session, servicio_id, inicio_iso)
    cliente = _resolver_cliente(session, telefono, nombre)

    cita = Cita(
        cliente_id=cliente.id,
        servicio_id=servicio.id,
        inicio=inicio_utc,
        fin=fin_utc,
        estado=ESTADO_CONFIRMADA,
    )
    session.add(cita)
    try:
        session.flush()  # dispara el EXCLUDE constraint en Postgres si hay carrera
    except IntegrityError as exc:
        session.rollback()
        log.warning("Conflicto al crear cita (solape detectado por la DB): %s", exc)
        raise SlotNoDisponible("Ese hueco acaba de ser reservado por otra persona.") from exc

    # Sincronizacion con Calendar: best-effort, no debe tumbar la reserva.
    try:
        resumen = f"{servicio.nombre} - {cliente.nombre or telefono}"
        cita.gcal_event_id = calendar_gcal.create_event(
            summary=resumen, start=inicio_utc, end=fin_utc, description=f"Tel: {telefono}"
        )
    except Exception as exc:  # noqa: BLE001 - loguear y marcar para reintento
        log.error("No se pudo sincronizar la cita %s con Calendar: %s", cita.id, exc)
        cita.gcal_event_id = None

    session.commit()
    log.info("Cita %s creada para %s (%s)", cita.id, telefono, inicio_utc.isoformat())
    return cita


def _localizar_cita_cancelable(
    session: Session,
    cita_id: int | None = None,
    telefono: str | None = None,
    fecha: dt.date | None = None,
) -> Cita:
    """Busca y valida (sin mutar) la cita a cancelar. Identificable por id o por
    (telefono + fecha). Solo citas futuras y, si se pasa telefono, del propio cliente."""
    tz = get_timezone(session)
    cita: Cita | None = None

    if cita_id is not None:
        cita = session.get(Cita, cita_id)
        if cita is not None and telefono and cita.cliente.telefono != telefono:
            raise CitaNoEncontrada("Esa cita no pertenece a este telefono.")
    elif telefono and fecha is not None:
        dia_ini = dt.datetime.combine(fecha, dt.time.min, tzinfo=tz).astimezone(dt.timezone.utc)
        dia_fin = dia_ini + dt.timedelta(days=1)
        cita = session.scalar(
            select(Cita)
            .join(Cliente)
            .where(
                Cliente.telefono == telefono,
                Cita.estado == ESTADO_CONFIRMADA,
                Cita.inicio >= dia_ini,
                Cita.inicio < dia_fin,
            )
            .order_by(Cita.inicio)
        )
    else:
        raise CitaNoEncontrada("Indica el id de la cita o el telefono y la fecha.")

    if cita is None:
        raise CitaNoEncontrada("No se encontro una cita que cancelar.")
    if cita.estado == ESTADO_CANCELADA:
        raise CitaNoEncontrada("Esa cita ya estaba cancelada.")
    if cita.inicio <= _utcnow():
        raise SlotNoDisponible("Solo se pueden cancelar citas futuras.")
    return cita


def simular_cancelar_cita(
    session: Session,
    cita_id: int | None = None,
    telefono: str | None = None,
    fecha: dt.date | None = None,
) -> Cita:
    """Version de solo lectura de `cancelar_cita` para el modo sombra (§12 v2):
    localiza y valida la cita pero no la cancela de verdad."""
    return _localizar_cita_cancelable(session, cita_id=cita_id, telefono=telefono, fecha=fecha)


def cancelar_cita(
    session: Session,
    cita_id: int | None = None,
    telefono: str | None = None,
    fecha: dt.date | None = None,
) -> Cita:
    """Cancela una cita futura. Identificable por id o por (telefono + fecha).

    Solo citas futuras y, si se pasa telefono, del propio cliente.
    """
    cita = _localizar_cita_cancelable(session, cita_id=cita_id, telefono=telefono, fecha=fecha)

    cita.estado = ESTADO_CANCELADA
    try:
        calendar_gcal.delete_event(cita.gcal_event_id)
    except Exception as exc:  # noqa: BLE001
        log.error("No se pudo borrar el evento de Calendar de la cita %s: %s", cita.id, exc)

    session.commit()
    log.info("Cita %s cancelada", cita.id)
    return cita


def listar_citas(
    session: Session,
    desde: dt.datetime | None = None,
    hasta: dt.datetime | None = None,
    estado: str | None = None,
) -> list[Cita]:
    """Lista citas con filtros opcionales (para el panel)."""
    stmt = select(Cita)
    if desde is not None:
        stmt = stmt.where(Cita.inicio >= desde)
    if hasta is not None:
        stmt = stmt.where(Cita.inicio < hasta)
    if estado is not None:
        stmt = stmt.where(Cita.estado == estado)
    return list(session.scalars(stmt.order_by(Cita.inicio)).all())


def actualizar_cita(
    session: Session,
    cita_id: int,
    estado: str | None = None,
    notas: str | None = None,
    nuevo_inicio_iso: str | None = None,
) -> Cita:
    """Cambia estado/notas y/o reprograma una cita (panel). Sincroniza Calendar."""
    cita = session.get(Cita, cita_id)
    if cita is None:
        raise CitaNoEncontrada("Cita no encontrada.")
    tz = get_timezone(session)

    if estado is not None:
        if estado not in ESTADOS_VALIDOS:
            raise AgendaError(f"Estado invalido: {estado}")
        cita.estado = estado
        if estado == ESTADO_CANCELADA:
            try:
                calendar_gcal.delete_event(cita.gcal_event_id)
            except Exception as exc:  # noqa: BLE001
                log.error("No se pudo borrar el evento de la cita %s: %s", cita.id, exc)
            cita.gcal_event_id = None
            cita.recordatorio = RECORDATORIO_NO_APLICA

    if notas is not None:
        cita.notas = notas

    if nuevo_inicio_iso is not None:
        servicio = cita.servicio
        nuevo_inicio = _parse_inicio(nuevo_inicio_iso, tz)
        nuevo_fin = nuevo_inicio + dt.timedelta(minutes=servicio.duracion_min)
        if nuevo_inicio <= _utcnow():
            raise SlotNoDisponible("La nueva hora ya ha pasado.")
        if not _slot_libre(session, servicio, nuevo_inicio, nuevo_fin, tz, excluir_cita_id=cita.id):
            raise SlotNoDisponible("La nueva hora no esta disponible.")
        cita.inicio = nuevo_inicio
        cita.fin = nuevo_fin
        cita.recordatorio = RECORDATORIO_PENDIENTE  # rearmar recordatorio tras cambio de hora
        try:
            resumen = f"{servicio.nombre} - {cita.cliente.nombre or cita.cliente.telefono}"
            cita.gcal_event_id = calendar_gcal.update_event(
                cita.gcal_event_id, resumen, nuevo_inicio, nuevo_fin
            )
        except Exception as exc:  # noqa: BLE001
            log.error("No se pudo actualizar el evento de la cita %s: %s", cita.id, exc)

    session.commit()
    return cita


def listar_servicios_activos(session: Session) -> list[Servicio]:
    return list(session.scalars(select(Servicio).where(Servicio.activo.is_(True)).order_by(Servicio.id)).all())


def horario_texto(session: Session) -> str:
    """Resumen legible del horario semanal para inyectar en el system prompt."""
    dias = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
    franjas = list(session.scalars(select(Horario).order_by(Horario.dia_semana, Horario.hora_inicio)).all())
    if not franjas:
        return "Sin horario configurado."
    por_dia: dict[int, list[str]] = {}
    for f in franjas:
        por_dia.setdefault(f.dia_semana, []).append(
            f"{f.hora_inicio.strftime('%H:%M')}-{f.hora_fin.strftime('%H:%M')}"
        )
    return "; ".join(f"{dias[d]}: {', '.join(tramos)}" for d, tramos in sorted(por_dia.items()))
