"""Pruebas del endurecimiento de la fase 8: firma de webhook, gating de /dev/simulate
y /docs, y los chequeos de arranque en produccion.

No invocan al agente (no requieren ANTHROPIC_API_KEY ni gastan tokens): usan payloads
de webhook sin mensajes y comprueban el registro de rutas en vez de ejecutarlas.
"""

from __future__ import annotations

import hashlib
import hmac

import pytest
from starlette.testclient import TestClient

from app.config import settings
from app.main import _SECRET_KEY_INSEGURO, _verificar_config_produccion, app
from app.routers import webhook

client = TestClient(app)


def _firmar(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# --- Firma HMAC del webhook --------------------------------------------------
def test_firma_valida_acepta_la_correcta(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_app_secret", "s3cr3t")
    body = b'{"entry": []}'
    assert webhook._firma_valida(body, _firmar(body, "s3cr3t"))


def test_firma_valida_rechaza_incorrecta_o_ausente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "whatsapp_app_secret", "s3cr3t")
    body = b'{"entry": []}'
    assert not webhook._firma_valida(body, "sha256=deadbeef")
    assert not webhook._firma_valida(body, None)
    assert not webhook._firma_valida(body, "sin-prefijo")


def _modo_meta(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fuerza el proveedor Meta (sin YCloud), independientemente del .env local."""
    monkeypatch.setattr(settings, "ycloud_api_key", "")
    monkeypatch.setattr(settings, "ycloud_webhook_secret", "")


def test_webhook_rechaza_firma_invalida(monkeypatch: pytest.MonkeyPatch) -> None:
    _modo_meta(monkeypatch)
    monkeypatch.setattr(settings, "whatsapp_app_secret", "s3cr3t")
    r = client.post(
        "/webhook", content=b'{"entry": []}', headers={"X-Hub-Signature-256": "sha256=bad"}
    )
    assert r.status_code == 403


def test_webhook_acepta_firma_valida(monkeypatch: pytest.MonkeyPatch) -> None:
    _modo_meta(monkeypatch)
    monkeypatch.setattr(settings, "whatsapp_app_secret", "s3cr3t")
    body = b'{"entry": []}'
    r = client.post(
        "/webhook", content=body, headers={"X-Hub-Signature-256": _firmar(body, "s3cr3t")}
    )
    assert r.status_code == 200
    assert r.json()["status"] == "received"


def test_webhook_sin_app_secret_no_exige_firma(monkeypatch: pytest.MonkeyPatch) -> None:
    _modo_meta(monkeypatch)
    monkeypatch.setattr(settings, "whatsapp_app_secret", "")
    r = client.post("/webhook", json={"entry": []})
    assert r.status_code == 200


# --- Token del webhook de YCloud (§8 v2) -------------------------------------
def test_webhook_ycloud_rechaza_token_invalido(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ycloud_api_key", "k")
    monkeypatch.setattr(settings, "ycloud_webhook_secret", "tok3n")
    assert client.post("/webhook", json={"type": "x"}).status_code == 403
    assert client.post("/webhook?token=malo", json={"type": "x"}).status_code == 403


def test_webhook_ycloud_acepta_token_valido(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ycloud_api_key", "k")
    monkeypatch.setattr(settings, "ycloud_webhook_secret", "tok3n")
    r = client.post("/webhook?token=tok3n", json={"type": "x"})
    assert r.status_code == 200


# --- Gating de endpoints de desarrollo --------------------------------------
def test_dev_simulate_y_docs_expuestos_en_debug() -> None:
    assert settings.debug, "los tests asumen DEBUG=true (valor por defecto)"
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/dev/simulate" in paths
    assert client.get("/docs").status_code == 200


# --- Chequeos de arranque en produccion -------------------------------------
def test_guardrail_aborta_con_secret_por_defecto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "debug", False)
    monkeypatch.setattr(settings, "secret_key", _SECRET_KEY_INSEGURO)
    with pytest.raises(RuntimeError):
        _verificar_config_produccion()


def test_guardrail_no_aborta_en_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "debug", True)
    _verificar_config_produccion()  # no debe lanzar


# --- El panel exige sesion ---------------------------------------------------
def test_admin_redirige_a_login_sin_sesion() -> None:
    r = TestClient(app, follow_redirects=False).get("/admin/agenda")
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/login"
