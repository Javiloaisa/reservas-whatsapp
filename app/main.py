"""Punto de entrada FastAPI. Monta el webhook, la API del panel (/api) y la UI (/admin)."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app import logconf
from app.config import settings
from app.routers import admin, api, webhook

logconf.setup()
log = logging.getLogger("main")

_SECRET_KEY_INSEGURO = "dev-insecure-secret-key"
_ADMIN_PASSWORD_INSEGURO = "cambia-esta-contrasena"


def _verificar_config_produccion() -> None:
    """Chequeos de arranque para no desplegar con valores de desarrollo.

    Con DEBUG=false (produccion): un SECRET_KEY por defecto permitiria falsificar
    sesiones del panel, asi que se aborta el arranque. El resto son avisos.
    """
    if settings.debug:
        return
    if settings.secret_key == _SECRET_KEY_INSEGURO:
        raise RuntimeError(
            "SECRET_KEY tiene el valor de desarrollo en produccion (DEBUG=false). "
            "Genera una cadena larga y aleatoria en el .env antes de desplegar."
        )
    if settings.admin_password == _ADMIN_PASSWORD_INSEGURO:
        log.critical("ADMIN_PASSWORD sigue siendo el de ejemplo; cambialo cuanto antes.")
    if not settings.webhook_signature_required:
        log.warning("WHATSAPP_APP_SECRET vacio: los webhooks de Meta no se verifican por firma.")


_verificar_config_produccion()

# En produccion no se exponen la documentacion interactiva ni los esquemas.
app = FastAPI(
    title="Agente Podologo",
    version="1.0.0",
    docs_url="/docs" if settings.debug else None,
    redoc_url="/redoc" if settings.debug else None,
    openapi_url="/openapi.json" if settings.debug else None,
)
# Sesion del panel en cookie firmada (login admin). 8 h de validez.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    session_cookie="agente_session",
    max_age=8 * 60 * 60,
    same_site="lax",
    https_only=settings.app_base_url.startswith("https"),
)
app.include_router(webhook.router)
app.include_router(api.router)
app.include_router(admin.router)


@app.get("/health")
async def health() -> dict[str, object]:
    """Estado del servicio y de las integraciones (util para diagnostico)."""
    return {
        "status": "ok",
        "debug": settings.debug,
        "anthropic": settings.anthropic_enabled,
        "whatsapp": settings.whatsapp_enabled,
        "webhook_firma": settings.webhook_signature_required,
        "google_calendar": settings.gcal_enabled,
        "timezone": settings.timezone,
    }
