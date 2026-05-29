"""Punto de entrada FastAPI. Monta el router del webhook.

Fases posteriores montaran aqui los routers /api (panel) y /admin (UI).
"""

from __future__ import annotations

from fastapi import FastAPI

from app import logconf
from app.config import settings
from app.routers import webhook

logconf.setup()

app = FastAPI(title="Agente Podologo", version="0.1.0")
app.include_router(webhook.router)


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
