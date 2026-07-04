"""Orquestacion del agente: Claude (Anthropic API) + tool use (§6 del CLAUDE.md).

El agente conversa con el cliente y modifica estado UNICAMENTE a traves de las
herramientas, que delegan en `services/agenda.py`. Nunca escribe en DB ni en
Calendar desde el prompt.

Fases cubiertas:
- Fase 3: conversacion con historial persistido.
- Fase 4: tool use (disponibilidad, crear, cancelar) con sincronizacion a Calendar.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import ROL_BOT, ROL_CLIENTE, ROL_PODOLOGO, Cliente, Mensaje
from app.services import agenda
from app.services.config_repo import get_timezone, modelo_claude

log = logging.getLogger("agente")

HISTORIAL_MAX = 20
MAX_ITERACIONES_TOOLS = 6
MAX_TOKENS = 1024

DIAS_ES = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


# --------------------------------------------------------------------------- #
#  Definicion de herramientas expuestas a Claude
# --------------------------------------------------------------------------- #
TOOLS: list[dict[str, Any]] = [
    {
        "name": "listar_servicios",
        "description": "Lista los servicios activos de la clinica con su duracion y precio.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "consultar_disponibilidad",
        "description": (
            "Devuelve los huecos libres de un servicio en una fecha concreta. "
            "Usar SIEMPRE antes de proponer u ofrecer una hora; nunca inventar disponibilidad."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "fecha": {"type": "string", "description": "Fecha en formato YYYY-MM-DD"},
                "servicio_id": {"type": "integer", "description": "Id del servicio"},
            },
            "required": ["fecha", "servicio_id"],
        },
    },
    {
        "name": "crear_cita",
        "description": (
            "Reserva una cita. Llamar solo tras confirmar con el cliente servicio, dia y hora, "
            "y conocer su nombre. Usar el 'inicio_iso' exacto devuelto por consultar_disponibilidad."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "nombre": {"type": "string", "description": "Nombre del cliente"},
                "servicio_id": {"type": "integer"},
                "inicio_iso": {
                    "type": "string",
                    "description": "Inicio de la cita en ISO 8601 (el devuelto por consultar_disponibilidad)",
                },
            },
            "required": ["nombre", "servicio_id", "inicio_iso"],
        },
    },
    {
        "name": "cancelar_cita",
        "description": (
            "Cancela una cita futura del propio cliente. Indicar el id de la cita, o bien la fecha "
            "(YYYY-MM-DD) de la cita a cancelar."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "cita_id": {"type": "integer"},
                "fecha": {"type": "string", "description": "Fecha de la cita en formato YYYY-MM-DD"},
            },
        },
    },
]


@lru_cache
def _client():  # noqa: ANN202
    import anthropic

    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


# --------------------------------------------------------------------------- #
#  System prompt dinamico (plantilla en prompts/system_agente.md, §7 v2)
# --------------------------------------------------------------------------- #
_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

# RGPD (§14 v2): el primer mensaje a un cliente nuevo debe indicar que es un
# asistente automatico.
_NOTA_NUEVO_CLIENTE = (
    "Es la primera vez que hablas con este cliente: en tu primer mensaje presentate "
    "brevemente como el asistente automático de citas de la clínica."
)


@lru_cache
def _plantilla_prompt() -> str:
    return (_PROMPTS_DIR / "system_agente.md").read_text(encoding="utf-8")


def _system_prompt(session: Session, cliente: Cliente, es_nuevo: bool = False) -> str:
    tz = get_timezone(session)
    hoy = dt.datetime.now(tz)
    servicios = agenda.listar_servicios_activos(session)
    lineas_serv = [
        f"- [id {s.id}] {s.nombre}: {s.duracion_min} min"
        + (f", {s.precio} EUR" if s.precio is not None else "")
        for s in servicios
    ]
    reemplazos = {
        "[[FECHA_HORA]]": hoy.strftime("%Y-%m-%d %H:%M"),
        "[[DIA_SEMANA]]": DIAS_ES[hoy.weekday()],
        "[[TIMEZONE]]": tz.key,
        "[[NOMBRE_CLIENTE]]": cliente.nombre or "(desconocido todavia)",
        "[[NOTA_NUEVO_CLIENTE]]": _NOTA_NUEVO_CLIENTE if es_nuevo else "",
        "[[SERVICIOS]]": "\n".join(lineas_serv),
        "[[HORARIO]]": agenda.horario_texto(session),
    }
    prompt = _plantilla_prompt()
    for marca, valor in reemplazos.items():
        prompt = prompt.replace(marca, valor)
    return prompt


# --------------------------------------------------------------------------- #
#  Ejecucion de herramientas (unica via de mutacion de estado)
# --------------------------------------------------------------------------- #
def _ejecutar_tool(
    session: Session,
    telefono: str,
    nombre_tool: str,
    args: dict[str, Any],
    dry_run: bool = False,
) -> tuple[str, bool]:
    """Ejecuta una herramienta y devuelve (contenido_para_el_modelo, es_error).

    En `dry_run` (modo sombra, §12 v2), `crear_cita`/`cancelar_cita` solo validan:
    no escriben en BD ni en Calendar.
    """
    tz = get_timezone(session)
    try:
        if nombre_tool == "listar_servicios":
            servicios = agenda.listar_servicios_activos(session)
            data = [
                {
                    "id": s.id,
                    "nombre": s.nombre,
                    "duracion_min": s.duracion_min,
                    "precio": float(s.precio) if s.precio is not None else None,
                }
                for s in servicios
            ]
            return json.dumps(data, ensure_ascii=False), False

        if nombre_tool == "consultar_disponibilidad":
            fecha = dt.date.fromisoformat(args["fecha"])
            huecos = agenda.huecos_libres(session, fecha, int(args["servicio_id"]), tz=tz)
            data = {
                "fecha": args["fecha"],
                "huecos": [
                    {"hora": h.strftime("%H:%M"), "inicio_iso": h.isoformat()}
                    for h in huecos[:24]
                ],
                "total": len(huecos),
            }
            return json.dumps(data, ensure_ascii=False), False

        if nombre_tool == "crear_cita":
            if dry_run:
                info = agenda.simular_crear_cita(
                    session,
                    servicio_id=int(args["servicio_id"]),
                    inicio_iso=args["inicio_iso"],
                )
                return json.dumps({"ok": True, "simulado": True, **info}, ensure_ascii=False), False
            cita = agenda.crear_cita(
                session,
                telefono=telefono,
                servicio_id=int(args["servicio_id"]),
                inicio_iso=args["inicio_iso"],
                nombre=args.get("nombre"),
            )
            inicio_local = cita.inicio.astimezone(tz)
            return (
                json.dumps(
                    {
                        "ok": True,
                        "cita_id": cita.id,
                        "inicio": inicio_local.strftime("%Y-%m-%d %H:%M"),
                    },
                    ensure_ascii=False,
                ),
                False,
            )

        if nombre_tool == "cancelar_cita":
            fecha = dt.date.fromisoformat(args["fecha"]) if args.get("fecha") else None
            if dry_run:
                cita = agenda.simular_cancelar_cita(
                    session, cita_id=args.get("cita_id"), telefono=telefono, fecha=fecha
                )
                return (
                    json.dumps({"ok": True, "simulado": True, "cita_id": cita.id}, ensure_ascii=False),
                    False,
                )
            cita = agenda.cancelar_cita(
                session,
                cita_id=args.get("cita_id"),
                telefono=telefono,
                fecha=fecha,
            )
            return json.dumps({"ok": True, "cita_id": cita.id, "estado": cita.estado}, ensure_ascii=False), False

        return f"Herramienta desconocida: {nombre_tool}", True

    except agenda.AgendaError as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), True
    except (KeyError, ValueError) as exc:
        log.warning("Argumentos invalidos para %s: %s", nombre_tool, exc)
        return json.dumps({"ok": False, "error": f"Argumentos invalidos: {exc}"}, ensure_ascii=False), True


# --------------------------------------------------------------------------- #
#  Bucle de conversacion con Claude
# --------------------------------------------------------------------------- #
def _run_agent(
    session: Session,
    telefono: str,
    system: str,
    messages: list[dict[str, Any]],
    dry_run: bool = False,
) -> str:
    client = _client()
    modelo = modelo_claude(session)

    # Prompt caching: cachea bloque system y herramientas (estables durante el bucle).
    system_blocks = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    tools = [dict(t) for t in TOOLS]
    tools[-1] = {**tools[-1], "cache_control": {"type": "ephemeral"}}

    for _ in range(MAX_ITERACIONES_TOOLS):
        resp = client.messages.create(
            model=modelo,
            max_tokens=MAX_TOKENS,
            system=system_blocks,
            tools=tools,
            messages=messages,
        )

        if resp.stop_reason != "tool_use":
            return _texto_de(resp.content)

        # Adjuntar el turno del asistente (con sus bloques tool_use) tal cual.
        messages.append({"role": "assistant", "content": resp.content})

        tool_results = []
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                contenido, es_error = _ejecutar_tool(
                    session, telefono, block.name, dict(block.input), dry_run=dry_run
                )
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": contenido,
                        "is_error": es_error,
                    }
                )
        messages.append({"role": "user", "content": tool_results})

    log.warning("Se alcanzo el limite de iteraciones de tool use para %s", telefono)
    return "Perdona, estoy teniendo problemas para completar la gestion. Puedes intentarlo de nuevo en un momento?"


def _texto_de(content: list[Any]) -> str:
    partes = [b.text for b in content if getattr(b, "type", None) == "text"]
    return "\n".join(p for p in partes if p).strip() or "Perdona, no te he entendido. Puedes repetirlo?"


# --------------------------------------------------------------------------- #
#  API publica
# --------------------------------------------------------------------------- #
def resolver_cliente(session: Session, telefono: str, nombre: str | None = None) -> Cliente:
    """Busca el cliente por telefono o lo crea. Rellena `nombre` si aun no se conocia."""
    cliente = session.scalar(select(Cliente).where(Cliente.telefono == telefono))
    if cliente is None:
        cliente = Cliente(telefono=telefono, nombre=nombre)
        session.add(cliente)
        session.flush()
    elif nombre and not cliente.nombre:
        cliente.nombre = nombre
    return cliente


# La API de Claude solo acepta roles user/assistant: lo que el podologo escribe a
# mano desde su app (rol podologo_manual) se presenta como turno del asistente,
# porque para el cliente es una respuesta de la clinica.
_ROL_API = {ROL_CLIENTE: "user", ROL_BOT: "assistant", ROL_PODOLOGO: "assistant"}


def _historial(session: Session, cliente_id: int) -> list[dict[str, str]]:
    stmt = (
        select(Mensaje)
        .where(Mensaje.cliente_id == cliente_id)
        .order_by(Mensaje.creado_en.desc(), Mensaje.id.desc())
        .limit(HISTORIAL_MAX)
    )
    recientes = list(session.scalars(stmt).all())
    recientes.reverse()
    mensajes = [
        {"role": _ROL_API.get(m.rol, "user"), "content": m.contenido} for m in recientes
    ]
    # La API exige que el primer mensaje sea del usuario; se descartan turnos
    # iniciales del asistente (p. ej. un saludo automatico previo al historial).
    while mensajes and mensajes[0]["role"] != "user":
        mensajes.pop(0)
    return mensajes


def procesar_mensaje(
    session: Session,
    telefono: str,
    texto: str,
    wa_message_id: str | None = None,
    clasificacion: str | None = None,
    dry_run: bool = False,
) -> str:
    """Procesa un mensaje ya clasificado como cita y devuelve la respuesta al cliente.

    La deduplicacion de eventos y las decisiones de modo sombra / modo humano /
    clasificador viven en `services/pipeline.py` (§4 v2); esta funcion asume que
    ya se decidio que el agente debe responder. En `dry_run` (modo sombra, §12 v2)
    el agente corre igual pero las herramientas de escritura se ejecutan en seco.
    """
    cliente = resolver_cliente(session, telefono)

    session.add(
        Mensaje(
            cliente_id=cliente.id,
            rol=ROL_CLIENTE,
            contenido=texto,
            clasificacion=clasificacion,
            message_id_proveedor=wa_message_id,
        )
    )
    session.flush()
    messages = _historial(session, cliente.id)

    # Sin API key: degradar a eco/bienvenida (util en fase 2 antes de tener clave).
    if not settings.anthropic_enabled:
        respuesta = f"(eco) {texto}"
    else:
        # Cliente nuevo = este es su primer mensaje registrado (transparencia RGPD).
        system = _system_prompt(session, cliente, es_nuevo=len(messages) <= 1)
        respuesta = _run_agent(session, telefono, system, messages, dry_run=dry_run)

    session.add(Mensaje(cliente_id=cliente.id, rol=ROL_BOT, contenido=respuesta))
    session.commit()
    return respuesta
