"""API del panel (§10 del CLAUDE.md). Todo bajo /api, protegido por sesion admin
(excepto login). Toda mutacion de cita sincroniza Google Calendar via services/agenda.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import SESSION_KEY, get_db, require_admin
from app.models import (
    ESTADO_COMPLETADA,
    ESTADO_NO_SHOW,
    Bloqueo,
    Cita,
    Cliente,
    Horario,
    Mensaje,
    Servicio,
    UsuarioAdmin,
)
from app.schemas import (
    BloqueoIn,
    BloqueoOut,
    CitaCreate,
    CitaOut,
    CitaUpdate,
    HorarioIn,
    HorarioOut,
    LoginRequest,
    MensajeOut,
    ServicioIn,
    ServicioOut,
    ServicioUpdate,
    StatsOut,
)
from app.security import verify_password
from app.services import agenda
from app.services.config_repo import all_config, set_config
from app.services.stats import resumen_estadisticas

router = APIRouter(prefix="/api")


# --------------------------------------------------------------------------- #
#  Auth
# --------------------------------------------------------------------------- #
@router.post("/login")
def login(req: LoginRequest, request: Request, db: Session = Depends(get_db)) -> dict[str, str]:
    admin = db.scalar(select(UsuarioAdmin).where(UsuarioAdmin.email == req.email))
    if admin is None or not verify_password(req.password, admin.password_hash):
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    request.session[SESSION_KEY] = admin.id
    return {"ok": "true", "email": admin.email}


@router.post("/logout")
def logout(request: Request) -> dict[str, str]:
    request.session.clear()
    return {"ok": "true"}


# --------------------------------------------------------------------------- #
#  Citas
# --------------------------------------------------------------------------- #
def _cita_out(c: Cita) -> CitaOut:
    return CitaOut(
        id=c.id,
        inicio=c.inicio,
        fin=c.fin,
        estado=c.estado,
        recordatorio=c.recordatorio,
        notas=c.notas,
        gcal_event_id=c.gcal_event_id,
        servicio_id=c.servicio_id,
        servicio_nombre=c.servicio.nombre,
        cliente_id=c.cliente_id,
        cliente_nombre=c.cliente.nombre,
        cliente_telefono=c.cliente.telefono,
    )


@router.get("/citas", response_model=list[CitaOut])
def listar_citas(
    desde: dt.datetime | None = None,
    hasta: dt.datetime | None = None,
    estado: str | None = None,
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin),
) -> list[CitaOut]:
    citas = agenda.listar_citas(db, desde=desde, hasta=hasta, estado=estado)
    return [_cita_out(c) for c in citas]


@router.post("/citas", response_model=CitaOut, status_code=201)
def crear_cita(
    body: CitaCreate,
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin),
) -> CitaOut:
    try:
        cita = agenda.crear_cita(
            db,
            telefono=body.telefono,
            servicio_id=body.servicio_id,
            inicio_iso=body.inicio_iso,
            nombre=body.nombre,
        )
    except agenda.AgendaError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _cita_out(cita)


@router.patch("/citas/{cita_id}", response_model=CitaOut)
def actualizar_cita(
    cita_id: int,
    body: CitaUpdate,
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin),
) -> CitaOut:
    try:
        cita = agenda.actualizar_cita(
            db,
            cita_id,
            estado=body.estado,
            notas=body.notas,
            nuevo_inicio_iso=body.nuevo_inicio_iso,
        )
    except agenda.CitaNoEncontrada as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except agenda.AgendaError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _cita_out(cita)


@router.delete("/citas/{cita_id}", response_model=CitaOut)
def cancelar_cita(
    cita_id: int,
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin),
) -> CitaOut:
    try:
        cita = agenda.cancelar_cita(db, cita_id=cita_id)
    except agenda.CitaNoEncontrada as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except agenda.AgendaError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _cita_out(cita)


# --------------------------------------------------------------------------- #
#  Servicios (DELETE = baja logica para preservar integridad con citas)
# --------------------------------------------------------------------------- #
@router.get("/servicios", response_model=list[ServicioOut])
def listar_servicios(
    incluir_inactivos: bool = False,
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin),
) -> list[Servicio]:
    stmt = select(Servicio).order_by(Servicio.id)
    if not incluir_inactivos:
        stmt = stmt.where(Servicio.activo.is_(True))
    return list(db.scalars(stmt).all())


@router.post("/servicios", response_model=ServicioOut, status_code=201)
def crear_servicio(
    body: ServicioIn, db: Session = Depends(get_db), _: UsuarioAdmin = Depends(require_admin)
) -> Servicio:
    servicio = Servicio(**body.model_dump())
    db.add(servicio)
    db.commit()
    db.refresh(servicio)
    return servicio


@router.patch("/servicios/{servicio_id}", response_model=ServicioOut)
def actualizar_servicio(
    servicio_id: int,
    body: ServicioUpdate,
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin),
) -> Servicio:
    servicio = db.get(Servicio, servicio_id)
    if servicio is None:
        raise HTTPException(status_code=404, detail="Servicio no encontrado")
    for campo, valor in body.model_dump(exclude_unset=True).items():
        setattr(servicio, campo, valor)
    db.commit()
    db.refresh(servicio)
    return servicio


@router.delete("/servicios/{servicio_id}")
def borrar_servicio(
    servicio_id: int, db: Session = Depends(get_db), _: UsuarioAdmin = Depends(require_admin)
) -> dict[str, str]:
    servicio = db.get(Servicio, servicio_id)
    if servicio is None:
        raise HTTPException(status_code=404, detail="Servicio no encontrado")
    servicio.activo = False  # baja logica
    db.commit()
    return {"ok": "true"}


# --------------------------------------------------------------------------- #
#  Horarios
# --------------------------------------------------------------------------- #
@router.get("/horarios", response_model=list[HorarioOut])
def listar_horarios(
    db: Session = Depends(get_db), _: UsuarioAdmin = Depends(require_admin)
) -> list[Horario]:
    return list(db.scalars(select(Horario).order_by(Horario.dia_semana, Horario.hora_inicio)).all())


@router.post("/horarios", response_model=HorarioOut, status_code=201)
def crear_horario(
    body: HorarioIn, db: Session = Depends(get_db), _: UsuarioAdmin = Depends(require_admin)
) -> Horario:
    if body.hora_fin <= body.hora_inicio:
        raise HTTPException(status_code=422, detail="hora_fin debe ser posterior a hora_inicio")
    horario = Horario(**body.model_dump())
    db.add(horario)
    db.commit()
    db.refresh(horario)
    return horario


@router.delete("/horarios/{horario_id}")
def borrar_horario(
    horario_id: int, db: Session = Depends(get_db), _: UsuarioAdmin = Depends(require_admin)
) -> dict[str, str]:
    horario = db.get(Horario, horario_id)
    if horario is None:
        raise HTTPException(status_code=404, detail="Horario no encontrado")
    db.delete(horario)
    db.commit()
    return {"ok": "true"}


# --------------------------------------------------------------------------- #
#  Bloqueos
# --------------------------------------------------------------------------- #
@router.get("/bloqueos", response_model=list[BloqueoOut])
def listar_bloqueos(
    db: Session = Depends(get_db), _: UsuarioAdmin = Depends(require_admin)
) -> list[Bloqueo]:
    return list(db.scalars(select(Bloqueo).order_by(Bloqueo.inicio)).all())


@router.post("/bloqueos", response_model=BloqueoOut, status_code=201)
def crear_bloqueo(
    body: BloqueoIn, db: Session = Depends(get_db), _: UsuarioAdmin = Depends(require_admin)
) -> Bloqueo:
    if body.fin <= body.inicio:
        raise HTTPException(status_code=422, detail="fin debe ser posterior a inicio")
    bloqueo = Bloqueo(**body.model_dump())
    db.add(bloqueo)
    db.commit()
    db.refresh(bloqueo)
    return bloqueo


@router.delete("/bloqueos/{bloqueo_id}")
def borrar_bloqueo(
    bloqueo_id: int, db: Session = Depends(get_db), _: UsuarioAdmin = Depends(require_admin)
) -> dict[str, str]:
    bloqueo = db.get(Bloqueo, bloqueo_id)
    if bloqueo is None:
        raise HTTPException(status_code=404, detail="Bloqueo no encontrado")
    db.delete(bloqueo)
    db.commit()
    return {"ok": "true"}


# --------------------------------------------------------------------------- #
#  Conversaciones
# --------------------------------------------------------------------------- #
@router.get("/clientes/{cliente_id}/conversacion", response_model=list[MensajeOut])
def conversacion(
    cliente_id: int,
    limite: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin),
) -> list[Mensaje]:
    if db.get(Cliente, cliente_id) is None:
        raise HTTPException(status_code=404, detail="Cliente no encontrado")
    stmt = (
        select(Mensaje)
        .where(Mensaje.cliente_id == cliente_id)
        .order_by(Mensaje.creado_en.desc(), Mensaje.id.desc())
        .limit(limite)
    )
    mensajes = list(db.scalars(stmt).all())
    mensajes.reverse()
    return mensajes


# --------------------------------------------------------------------------- #
#  Config
# --------------------------------------------------------------------------- #
@router.get("/config")
def leer_config(
    db: Session = Depends(get_db), _: UsuarioAdmin = Depends(require_admin)
) -> dict[str, str]:
    return all_config(db)


@router.patch("/config")
def editar_config(
    body: dict[str, str],
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin),
) -> dict[str, str]:
    for clave, valor in body.items():
        set_config(db, clave, str(valor))
    db.commit()
    return all_config(db)


# --------------------------------------------------------------------------- #
#  Estadisticas
# --------------------------------------------------------------------------- #
@router.get("/stats", response_model=StatsOut)
def stats(
    periodo: int = Query(30, ge=1, le=365, description="Dias hacia atras"),
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin),
) -> StatsOut:
    return StatsOut(**resumen_estadisticas(db, periodo))
