"""Esquemas Pydantic de entrada/salida de la API.

En fases 1-4 solo se necesita el del endpoint de prueba local. El payload del
webhook de Meta se parsea de forma defensiva en `routers/webhook.py` (su forma
varia entre tipos de evento).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SimulateRequest(BaseModel):
    """Simula un mensaje entrante de WhatsApp para pruebas locales (sin Meta)."""

    telefono: str = Field(..., description="Telefono en formato internacional sin '+'")
    texto: str = Field(..., description="Texto del mensaje del cliente")


class SimulateResponse(BaseModel):
    respuesta: str
