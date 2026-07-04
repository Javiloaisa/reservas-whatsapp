"""Pruebas del flujo por evento (§4 v2): modo sombra, modo humano y clasificador.

No requieren ANTHROPIC_API_KEY: sin ella, `clasificador.clasificar` degrada a
"cita" y `agente.procesar_mensaje` a un eco, asi que los tests monkeypatchean el
clasificador cuando necesitan un caso "no_cita"/"duda" concreto.
"""

from __future__ import annotations

import datetime as dt
import uuid

import pytest

from app.db import SessionLocal
from app.models import CLASIFICACION_NO_CITA, ROL_BOT, ROL_PODOLOGO, Cliente, LogSombra, Mensaje
from app.services import agenda, clasificador, pipeline, whatsapp
from app.services.config_repo import get_config, set_config
from app.services.whatsapp import EcoSaliente, MensajeEntrante


def _telefono() -> str:
    return "34600" + uuid.uuid4().hex[:6]


@pytest.fixture()
def session():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


@pytest.fixture()
def restaurar_config(session):
    """Restaura las claves de config que un test modifique."""
    originales = {k: get_config(session, k) for k in ("bot_activo",)}
    yield
    for clave, valor in originales.items():
        if valor is not None:
            set_config(session, clave, valor)
    session.commit()


def _no_debe_enviar(monkeypatch: pytest.MonkeyPatch) -> None:
    def _falla(*_a, **_kw):
        raise AssertionError("no deberia enviarse nada por WhatsApp en este caso")

    monkeypatch.setattr(whatsapp, "send_text", _falla)


# --------------------------------------------------------------------------- #
#  Modo sombra global (bot_activo = false)
# --------------------------------------------------------------------------- #
def test_modo_sombra_no_envia_y_registra_log_sombra(
    session, restaurar_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_config(session, "bot_activo", "false")
    session.commit()
    _no_debe_enviar(monkeypatch)

    telefono = _telefono()
    evento = MensajeEntrante(
        telefono=telefono, nombre_perfil="Cliente Test", texto="Quiero pedir cita",
        message_id=f"wamid.{uuid.uuid4().hex}", timestamp=None,
    )
    pipeline.procesar_evento(evento)

    cliente = session.query(Cliente).filter_by(telefono=telefono).first()
    assert cliente is not None
    logs = session.query(LogSombra).filter_by(cliente_id=cliente.id).all()
    assert len(logs) == 1
    assert logs[0].clasificacion == "cita"
    assert logs[0].respuesta_no_enviada is not None  # el eco stub del agente


def test_modo_sombra_no_cita_no_llama_al_agente(
    session, restaurar_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_config(session, "bot_activo", "false")
    session.commit()
    _no_debe_enviar(monkeypatch)
    monkeypatch.setattr(clasificador, "clasificar", lambda *_a, **_kw: CLASIFICACION_NO_CITA)

    telefono = _telefono()
    evento = MensajeEntrante(
        telefono=telefono, nombre_perfil=None, texto="Me duele el pie",
        message_id=f"wamid.{uuid.uuid4().hex}", timestamp=None,
    )
    pipeline.procesar_evento(evento)

    cliente = session.query(Cliente).filter_by(telefono=telefono).first()
    logs = session.query(LogSombra).filter_by(cliente_id=cliente.id).all()
    assert len(logs) == 1
    assert logs[0].clasificacion == CLASIFICACION_NO_CITA
    assert logs[0].respuesta_no_enviada is None


# --------------------------------------------------------------------------- #
#  Clasificador: no_cita/duda => silencio (bot activo, sin modo humano)
# --------------------------------------------------------------------------- #
def test_clasificador_no_cita_silencia(
    session, restaurar_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_config(session, "bot_activo", "true")
    session.commit()
    _no_debe_enviar(monkeypatch)
    monkeypatch.setattr(clasificador, "clasificar", lambda *_a, **_kw: CLASIFICACION_NO_CITA)

    telefono = _telefono()
    evento = MensajeEntrante(
        telefono=telefono, nombre_perfil=None, texto="Cuanto cuesta un analisis de sangre",
        message_id=f"wamid.{uuid.uuid4().hex}", timestamp=None,
    )
    pipeline.procesar_evento(evento)

    cliente = session.query(Cliente).filter_by(telefono=telefono).first()
    mensajes = session.query(Mensaje).filter_by(cliente_id=cliente.id).all()
    assert len(mensajes) == 1
    assert mensajes[0].clasificacion == CLASIFICACION_NO_CITA


# --------------------------------------------------------------------------- #
#  Modo humano (§5 v2): eco del podologo activa/reactiva, y silencia al bot
# --------------------------------------------------------------------------- #
def test_eco_activa_modo_humano(session, restaurar_config) -> None:
    telefono = _telefono()
    eco = EcoSaliente(
        telefono_destino=telefono, texto="Ya te digo yo por aqui",
        message_id=f"wamid.{uuid.uuid4().hex}", timestamp=None,
    )
    pipeline.procesar_evento(eco)

    cliente = session.query(Cliente).filter_by(telefono=telefono).first()
    assert cliente is not None
    assert cliente.modo_humano_hasta is not None
    assert cliente.modo_humano_hasta > dt.datetime.now(dt.timezone.utc)

    mensaje = session.query(Mensaje).filter_by(cliente_id=cliente.id).one()
    assert mensaje.rol == ROL_PODOLOGO


def test_palabra_clave_reactiva_el_bot(session, restaurar_config) -> None:
    telefono = _telefono()
    pipeline.procesar_evento(
        EcoSaliente(telefono_destino=telefono, texto="hola", message_id=None, timestamp=None)
    )
    cliente = session.query(Cliente).filter_by(telefono=telefono).first()
    assert cliente.modo_humano_hasta is not None

    pipeline.procesar_evento(
        EcoSaliente(telefono_destino=telefono, texto="#bot", message_id=None, timestamp=None)
    )
    session.refresh(cliente)
    assert cliente.modo_humano_hasta is None


def test_eco_propio_no_activa_modo_humano(session, restaurar_config) -> None:
    telefono = _telefono()
    cliente = Cliente(telefono=telefono)
    session.add(cliente)
    session.flush()
    session.add(Mensaje(cliente_id=cliente.id, rol=ROL_BOT, contenido="Tu cita es el lunes"))
    session.commit()

    pipeline.procesar_evento(
        EcoSaliente(
            telefono_destino=telefono, texto="Tu cita es el lunes", message_id=None, timestamp=None
        )
    )
    session.refresh(cliente)
    assert cliente.modo_humano_hasta is None


def test_modo_humano_silencia_al_cliente(
    session, restaurar_config, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_config(session, "bot_activo", "true")
    session.commit()
    _no_debe_enviar(monkeypatch)

    telefono = _telefono()
    cliente = Cliente(
        telefono=telefono,
        modo_humano_hasta=dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1),
    )
    session.add(cliente)
    session.commit()

    evento = MensajeEntrante(
        telefono=telefono, nombre_perfil=None, texto="Quiero pedir cita",
        message_id=f"wamid.{uuid.uuid4().hex}", timestamp=None,
    )
    pipeline.procesar_evento(evento)

    mensajes = session.query(Mensaje).filter_by(cliente_id=cliente.id).all()
    assert len(mensajes) == 1
    assert mensajes[0].clasificacion is None


# --------------------------------------------------------------------------- #
#  Herramientas en seco (agenda.simular_*) usadas por el modo sombra
# --------------------------------------------------------------------------- #
def _proximo_hueco(session) -> dt.datetime:
    hoy = dt.date.today()
    for offset in range(1, 15):
        fecha = hoy + dt.timedelta(days=offset)
        huecos = agenda.huecos_libres(session, fecha, servicio_id=1)
        if huecos:
            return huecos[0]
    raise AssertionError("no se encontro ningun hueco libre en las proximas 2 semanas")


def test_simular_crear_cita_no_escribe_en_bd(session) -> None:
    hueco = _proximo_hueco(session)
    antes = session.query(agenda.Cita).count()

    info = agenda.simular_crear_cita(session, servicio_id=1, inicio_iso=hueco.isoformat())

    assert "inicio" in info
    assert session.query(agenda.Cita).count() == antes


def test_simular_cancelar_cita_no_cancela(session) -> None:
    hueco = _proximo_hueco(session)
    telefono = _telefono()
    cita = agenda.crear_cita(session, telefono=telefono, servicio_id=1, inicio_iso=hueco.isoformat())

    resultado = agenda.simular_cancelar_cita(session, cita_id=cita.id)

    assert resultado.id == cita.id
    session.refresh(cita)
    assert cita.estado == "confirmada"


# --------------------------------------------------------------------------- #
#  Historial para la API de Claude: roles mapeados a user/assistant
# --------------------------------------------------------------------------- #
def test_historial_mapea_podologo_manual_a_assistant(session) -> None:
    """Regresion: un eco del podologo en el historial rompia al agente con
    'Unexpected role podologo_manual' (la API solo acepta user/assistant)."""
    from app.services import agente

    cliente = Cliente(telefono=_telefono())
    session.add(cliente)
    session.flush()
    session.add(Mensaje(cliente_id=cliente.id, rol=ROL_PODOLOGO, contenido="Saludo automatico"))
    session.add(Mensaje(cliente_id=cliente.id, rol="user", contenido="Quiero cita"))
    session.add(Mensaje(cliente_id=cliente.id, rol=ROL_PODOLOGO, contenido="Ahora te atiendo"))
    session.add(Mensaje(cliente_id=cliente.id, rol=ROL_BOT, contenido="Claro, dime el dia"))
    session.commit()

    historial = agente._historial(session, cliente.id)

    assert all(m["role"] in ("user", "assistant") for m in historial)
    assert historial[0]["role"] == "user"  # la API exige empezar con user
    assert [m["role"] for m in historial] == ["user", "assistant", "assistant"]
