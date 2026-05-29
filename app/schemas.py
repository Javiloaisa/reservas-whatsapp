"""Esquemas Pydantic de entrada/salida de la API."""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


# --- Prueba local (sin Meta) ---
class SimulateRequest(BaseModel):
    telefono: str = Field(..., description="Telefono en formato internacional sin '+'")
    texto: str = Field(..., description="Texto del mensaje del cliente")


class SimulateResponse(BaseModel):
    respuesta: str


# --- Auth ---
class LoginRequest(BaseModel):
    email: str
    password: str


# --- Servicios ---
class ServicioIn(BaseModel):
    nombre: str
    duracion_min: int
    buffer_min: int = 0
    precio: Decimal | None = None
    activo: bool = True


class ServicioUpdate(BaseModel):
    nombre: str | None = None
    duracion_min: int | None = None
    buffer_min: int | None = None
    precio: Decimal | None = None
    activo: bool | None = None


class ServicioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    nombre: str
    duracion_min: int
    buffer_min: int
    precio: Decimal | None
    activo: bool


# --- Horarios ---
class HorarioIn(BaseModel):
    dia_semana: int = Field(..., ge=0, le=6, description="0=lunes ... 6=domingo")
    hora_inicio: dt.time
    hora_fin: dt.time


class HorarioOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    dia_semana: int
    hora_inicio: dt.time
    hora_fin: dt.time


# --- Bloqueos ---
class BloqueoIn(BaseModel):
    inicio: dt.datetime
    fin: dt.datetime
    motivo: str | None = None


class BloqueoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    inicio: dt.datetime
    fin: dt.datetime
    motivo: str | None


# --- Citas ---
class CitaCreate(BaseModel):
    telefono: str
    nombre: str | None = None
    servicio_id: int
    inicio_iso: str


class CitaUpdate(BaseModel):
    estado: str | None = None
    notas: str | None = None
    nuevo_inicio_iso: str | None = None


class CitaOut(BaseModel):
    id: int
    inicio: dt.datetime
    fin: dt.datetime
    estado: str
    recordatorio: str
    notas: str | None
    gcal_event_id: str | None
    servicio_id: int
    servicio_nombre: str
    cliente_id: int
    cliente_nombre: str | None
    cliente_telefono: str


# --- Mensajes (conversacion) ---
class MensajeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    rol: str
    contenido: str
    creado_en: dt.datetime


# --- Stats ---
class StatsOut(BaseModel):
    periodo_dias: int
    total: int
    por_estado: dict[str, int]
    servicios_top: list[dict[str, object]]
    tasa_no_show: float
