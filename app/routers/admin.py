"""Panel de administracion (UI HTML) — fase 7.

Paginas Jinja2 + formularios que reutilizan la misma logica de negocio que la API
JSON (`services/agenda`, `services/stats`, `config_repo`). Protegido por sesion via
`require_admin_html`, que redirige a /admin/login cuando no hay sesion.

Patron POST-redirect-GET: los formularios hacen POST y se redirige (303) a la pagina
de listado, pasando mensajes de exito/error por query string (`?msg=` / `?error=`).
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.deps import SESSION_KEY, get_db, require_admin_html
from app.models import (
    Bloqueo,
    Cliente,
    Horario,
    Mensaje,
    UsuarioAdmin,
)
from app.security import verify_password
from app.services.config_repo import (
    all_config,
    get_timezone,
    set_config,
)
from app.services.config_repo import bot_activo as is_bot_activo
from app.services.stats import resumen_estadisticas

router = APIRouter(prefix="/admin", tags=["admin"])

_TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

DIAS = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]


# --------------------------------------------------------------------------- #
#  Utilidades
# --------------------------------------------------------------------------- #
def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _fmt(valor: dt.datetime, tz: ZoneInfo) -> str:
    """Formatea un datetime UTC-aware en hora local de la clinica."""
    return valor.astimezone(tz).strftime("%d/%m/%Y %H:%M")


def _parse_local(valor: str, tz: ZoneInfo) -> dt.datetime:
    """Parsea un `datetime-local` (naive, hora local) a UTC-aware."""
    return dt.datetime.fromisoformat(valor).replace(tzinfo=tz).astimezone(dt.timezone.utc)


def _redirect(path: str, *, msg: str | None = None, error: str | None = None) -> RedirectResponse:
    """Redireccion 303 (POST-redirect-GET) con flash por query string."""
    from urllib.parse import urlencode

    params = {k: v for k, v in (("msg", msg), ("error", error)) if v}
    url = f"{path}?{urlencode(params)}" if params else path
    return RedirectResponse(url, status_code=303)


# --------------------------------------------------------------------------- #
#  Auth (sin proteger)
# --------------------------------------------------------------------------- #
@router.get("/", include_in_schema=False)
def index() -> RedirectResponse:
    return RedirectResponse("/admin/conversaciones", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request, error: str | None = None) -> HTMLResponse:
    return templates.TemplateResponse(request, "login.html", {"error": error})


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
) -> RedirectResponse:
    admin = db.scalar(select(UsuarioAdmin).where(UsuarioAdmin.email == email))
    if admin is None or not verify_password(password, admin.password_hash):
        return _redirect("/admin/login", error="Credenciales incorrectas")
    request.session[SESSION_KEY] = admin.id
    return RedirectResponse("/admin/conversaciones", status_code=303)


@router.get("/logout")
def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse("/admin/login", status_code=303)


# --------------------------------------------------------------------------- #
#  Horarios y bloqueos
# --------------------------------------------------------------------------- #
@router.get("/horarios", response_class=HTMLResponse)
def horarios_page(
    request: Request,
    msg: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin_html),
) -> HTMLResponse:
    tz = get_timezone(db)
    horarios = list(
        db.scalars(select(Horario).order_by(Horario.dia_semana, Horario.hora_inicio)).all()
    )
    bloqueos = [
        {"id": b.id, "inicio_str": _fmt(b.inicio, tz), "fin_str": _fmt(b.fin, tz), "motivo": b.motivo}
        for b in db.scalars(select(Bloqueo).order_by(Bloqueo.inicio)).all()
    ]
    return templates.TemplateResponse(
        request,
        "horarios.html",
        {"horarios": horarios, "bloqueos": bloqueos, "dias": DIAS, "msg": msg, "error": error},
    )


@router.post("/horarios")
def crear_horario(
    dia_semana: int = Form(...),
    hora_inicio: str = Form(...),
    hora_fin: str = Form(...),
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin_html),
) -> RedirectResponse:
    h_ini = dt.time.fromisoformat(hora_inicio)
    h_fin = dt.time.fromisoformat(hora_fin)
    if h_fin <= h_ini:
        return _redirect("/admin/horarios", error="La hora de fin debe ser posterior a la de inicio.")
    db.add(Horario(dia_semana=dia_semana, hora_inicio=h_ini, hora_fin=h_fin))
    db.commit()
    return _redirect("/admin/horarios", msg="Franja añadida.")


@router.post("/horarios/{horario_id}/borrar")
def borrar_horario(
    horario_id: int,
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin_html),
) -> RedirectResponse:
    horario = db.get(Horario, horario_id)
    if horario is not None:
        db.delete(horario)
        db.commit()
    return _redirect("/admin/horarios", msg="Franja borrada.")


@router.post("/bloqueos")
def crear_bloqueo(
    inicio: str = Form(...),
    fin: str = Form(...),
    motivo: str = Form(""),
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin_html),
) -> RedirectResponse:
    tz = get_timezone(db)
    inicio_utc = _parse_local(inicio, tz)
    fin_utc = _parse_local(fin, tz)
    if fin_utc <= inicio_utc:
        return _redirect("/admin/horarios", error="El fin debe ser posterior al inicio.")
    db.add(Bloqueo(inicio=inicio_utc, fin=fin_utc, motivo=motivo.strip() or None))
    db.commit()
    return _redirect("/admin/horarios", msg="Bloqueo añadido.")


@router.post("/bloqueos/{bloqueo_id}/borrar")
def borrar_bloqueo(
    bloqueo_id: int,
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin_html),
) -> RedirectResponse:
    bloqueo = db.get(Bloqueo, bloqueo_id)
    if bloqueo is not None:
        db.delete(bloqueo)
        db.commit()
    return _redirect("/admin/horarios", msg="Bloqueo borrado.")


# --------------------------------------------------------------------------- #
#  Conversaciones
# --------------------------------------------------------------------------- #
@router.get("/conversaciones", response_class=HTMLResponse)
def conversaciones_page(
    request: Request,
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin_html),
) -> HTMLResponse:
    tz = get_timezone(db)
    rows = db.execute(
        select(Cliente, func.count(Mensaje.id), func.max(Mensaje.creado_en))
        .join(Mensaje, Mensaje.cliente_id == Cliente.id)
        .group_by(Cliente.id)
        .order_by(func.max(Mensaje.creado_en).desc())
    ).all()
    clientes = [
        {
            "id": cliente.id,
            "nombre": cliente.nombre,
            "telefono": cliente.telefono,
            "n_mensajes": n,
            "ultimo": _fmt(ultimo, tz) if ultimo is not None else "—",
        }
        for cliente, n, ultimo in rows
    ]
    return templates.TemplateResponse(
        request,
        "conversaciones.html",
        {"clientes": clientes, "bot_activo": is_bot_activo(db)},
    )


@router.get("/conversaciones/{cliente_id}", response_class=HTMLResponse)
def conversacion_page(
    cliente_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin_html),
) -> HTMLResponse:
    cliente = db.get(Cliente, cliente_id)
    if cliente is None:
        return _redirect("/admin/conversaciones", error="Cliente no encontrado.")
    tz = get_timezone(db)
    mensajes_db = list(
        db.scalars(
            select(Mensaje)
            .where(Mensaje.cliente_id == cliente_id)
            .order_by(Mensaje.creado_en, Mensaje.id)
        ).all()
    )
    mensajes = [
        {"rol": m.rol, "contenido": m.contenido, "fecha": _fmt(m.creado_en, tz)}
        for m in mensajes_db
    ]
    return templates.TemplateResponse(
        request,
        "conversacion.html",
        {"cliente": cliente, "mensajes": mensajes},
    )


@router.post("/bot")
def toggle_bot(
    bot_activo: str = Form(...),
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin_html),
) -> RedirectResponse:
    set_config(db, "bot_activo", "true" if bot_activo.strip().lower() == "true" else "false")
    db.commit()
    return _redirect("/admin/conversaciones")


# --------------------------------------------------------------------------- #
#  Estadisticas
# --------------------------------------------------------------------------- #
@router.get("/stats", response_class=HTMLResponse)
def stats_page(
    request: Request,
    periodo: int = Query(30, ge=1, le=365),
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin_html),
) -> HTMLResponse:
    stats = resumen_estadisticas(db, periodo)
    return templates.TemplateResponse(request, "stats.html", {"stats": stats})


# --------------------------------------------------------------------------- #
#  Ajustes
# --------------------------------------------------------------------------- #
_CLAVES_AJUSTES = ("timezone", "podologo_whatsapp", "modelo_claude", "bot_activo", "mensaje_bienvenida")


@router.get("/ajustes", response_class=HTMLResponse)
def ajustes_page(
    request: Request,
    msg: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin_html),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "ajustes.html",
        {"cfg": all_config(db), "msg": msg, "error": error},
    )


@router.post("/ajustes")
def guardar_ajustes(
    timezone: str = Form(""),
    podologo_whatsapp: str = Form(""),
    modelo_claude: str = Form(""),
    bot_activo: str = Form("true"),
    mensaje_bienvenida: str = Form(""),
    db: Session = Depends(get_db),
    _: UsuarioAdmin = Depends(require_admin_html),
) -> RedirectResponse:
    # Validar la zona horaria antes de persistir (evita romper get_timezone).
    if timezone.strip():
        try:
            ZoneInfo(timezone.strip())
        except Exception:  # noqa: BLE001
            return _redirect("/admin/ajustes", error=f"Zona horaria inválida: {timezone}")

    valores = {
        "timezone": timezone.strip(),
        "podologo_whatsapp": podologo_whatsapp.strip(),
        "modelo_claude": modelo_claude.strip(),
        "bot_activo": "true" if bot_activo.strip().lower() == "true" else "false",
        "mensaje_bienvenida": mensaje_bienvenida.strip(),
    }
    for clave, valor in valores.items():
        set_config(db, clave, valor)
    db.commit()
    return _redirect("/admin/ajustes", msg="Ajustes guardados.")
