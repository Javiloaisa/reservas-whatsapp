"""Punto de entrada FastAPI. Monta el webhook, la API del panel (/api) y la UI (/admin)."""

from __future__ import annotations

from fastapi import FastAPI
from starlette.middleware.sessions import SessionMiddleware

from app import logconf
from app.config import settings
from app.routers import admin, api, webhook

logconf.setup()

app = FastAPI(title="Agente Podologo", version="0.1.0")
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
        "anthropic": settings.anthropic_enabled,
        "whatsapp": settings.whatsapp_enabled,
        "google_calendar": settings.gcal_enabled,
        "timezone": settings.timezone,
    }
