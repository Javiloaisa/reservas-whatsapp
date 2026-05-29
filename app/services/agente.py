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
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Cliente, Mensaje
from app.services import agenda
from app.services.config_repo import bot_activo, get_timezone, modelo_claude

log = logging.getLogger("agente")

HISTORIAL_MAX = 20
MAX_ITERACIONES_TOOLS = 6
MAX_TOKENS = 1024

DIAS_ES = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]

MENSAJE_ATENCION_MANUAL = (
    "Gracias por tu mensaje. En este momento te atendera una persona del equipo. "
    "Te responderemos lo antes posible."
)


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
#  System prompt dinamico
# --------------------------------------------------------------------------- #
def _system_prompt(session: Session, cliente: Cliente) -> str:
    tz = get_timezone(session)
    hoy = dt.datetime.now(tz)
    servicios = agenda.listar_servicios_activos(session)
    lineas_serv = [
        f"- [id {s.id}] {s.nombre}: {s.duracion_min} min"
        + (f", {s.precio} EUR" if s.precio is not None else "")
        for s in servicios
    ]
    nombre = cliente.nombre or "(desconocido todavia)"

    return (
        "Eres el asistente virtual de una clinica de podologia que atiende por WhatsApp. "
        "Tono amable, cercano y conciso (mensajes cortos, es WhatsApp).\n\n"
        f"Fecha y hora actual: {hoy.strftime('%Y-%m-%d %H:%M')} "
        f"({DIAS_ES[hoy.weekday()]}). Zona horaria: {tz.key}.\n"
        f"Nombre del cliente: {nombre}. Telefono ya conocido (no lo pidas).\n\n"
        "Servicios:\n" + "\n".join(lineas_serv) + "\n\n"
        f"Horario de apertura: {agenda.horario_texto(session)}\n\n"
        "Reglas:\n"
        "- Usa SIEMPRE consultar_disponibilidad antes de ofrecer u ofertar una hora. "
        "Nunca prometas un hueco sin haberlo verificado con la herramienta.\n"
        "- Antes de crear una cita, confirma explicitamente con el cliente: servicio, dia y hora.\n"
        "- Si no conoces el nombre del cliente, pidelo antes de reservar.\n"
        "- Para reservar, pasa a crear_cita el 'inicio_iso' EXACTO que devolvio consultar_disponibilidad.\n"
        "- Maneja y muestra siempre las horas en hora local. Interpreta 'manana', 'el viernes', etc. "
        "respecto a la fecha actual indicada arriba.\n"
        "- Si no hay huecos, discuplate y ofrece otro dia u hora.\n"
        "- No respondas a temas ajenos a la clinica; reconduce con amabilidad."
    )


# --------------------------------------------------------------------------- #
#  Ejecucion de herramientas (unica via de mutacion de estado)
# --------------------------------------------------------------------------- #
def _ejecutar_tool(session: Session, telefono: str, nombre_tool: str, args: dict[str, Any]) -> tuple[str, bool]:
    """Ejecuta una herramienta y devuelve (contenido_para_el_modelo, es_error)."""
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
def _run_agent(session: Session, telefono: str, system: str, messages: list[dict[str, Any]]) -> str:
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
                contenido, es_error = _ejecutar_tool(session, telefono, block.name, dict(block.input))
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
def _resolver_cliente(session: Session, telefono: str) -> Cliente:
    cliente = session.scalar(select(Cliente).where(Cliente.telefono == telefono))
    if cliente is None:
        cliente = Cliente(telefono=telefono)
        session.add(cliente)
        session.flush()
    return cliente


def _historial(session: Session, cliente_id: int) -> list[dict[str, str]]:
    stmt = (
        select(Mensaje)
        .where(Mensaje.cliente_id == cliente_id)
        .order_by(Mensaje.creado_en.desc(), Mensaje.id.desc())
        .limit(HISTORIAL_MAX)
    )
    recientes = list(session.scalars(stmt).all())
    recientes.reverse()
    return [{"role": m.rol, "content": m.contenido} for m in recientes]


def procesar_mensaje(
    session: Session,
    telefono: str,
    texto: str,
    wa_message_id: str | None = None,
) -> str:
    """Procesa un mensaje entrante y devuelve la respuesta a enviar al cliente.

    La deduplicacion por `wa_message_id` se hace en el webhook antes de llamar aqui.
    """
    cliente = _resolver_cliente(session, telefono)

    # Bot pausado: atencion manual (sin caidas).
    if not bot_activo(session):
        session.add(Mensaje(cliente_id=cliente.id, rol="user", contenido=texto))
        session.commit()
        return MENSAJE_ATENCION_MANUAL

    # Persistir el mensaje del usuario y construir historial (lo incluye).
    session.add(Mensaje(cliente_id=cliente.id, rol="user", contenido=texto))
    session.flush()
    messages = _historial(session, cliente.id)

    # Sin API key: degradar a eco/bienvenida (util en fase 2 antes de tener clave).
    if not settings.anthropic_enabled:
        respuesta = f"(eco) {texto}"
    else:
        system = _system_prompt(session, cliente)
        respuesta = _run_agent(session, telefono, system, messages)

    session.add(Mensaje(cliente_id=cliente.id, rol="assistant", contenido=respuesta))
    session.commit()
    return respuesta
