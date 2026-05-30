"""Carga de configuracion desde `.env` (ver §12 del CLAUDE.md).

Toda la configuracion de secretos vive aqui; nunca se hardcodean tokens ni IDs.
Los flags `whatsapp_enabled` / `gcal_enabled` deciden si esas integraciones
operan de verdad o en "modo stub" (registran en consola), segun haya o no
credenciales en el entorno.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- App ---
    app_base_url: str = "http://localhost:8000"
    secret_key: str = "dev-insecure-secret-key"
    database_url: str = "sqlite:///./agente.db"
    timezone: str = "Europe/Madrid"
    # Modo desarrollo: expone /docs y /dev/simulate y relaja los chequeos de
    # arranque. En PRODUCCION debe ponerse DEBUG=false en el .env.
    debug: bool = True

    # --- Anthropic ---
    anthropic_api_key: str = ""
    claude_model: str = "claude-sonnet-4-6"

    # --- WhatsApp Cloud API ---
    whatsapp_token: str = ""
    whatsapp_phone_id: str = ""
    whatsapp_verify_token: str = "token-de-verificacion"
    # App Secret de Meta: firma HMAC-SHA256 de los webhooks (X-Hub-Signature-256).
    # Si se configura, se rechaza todo POST /webhook sin firma valida.
    whatsapp_app_secret: str = ""
    whatsapp_template_lang: str = "es"
    graph_api_version: str = "v21.0"

    # --- Google Calendar ---
    google_credentials_file: str = ""
    google_calendar_id: str = ""

    # --- Admin del panel (fases posteriores) ---
    admin_email: str = "admin@example.com"
    admin_password: str = "cambia-esta-contrasena"

    @property
    def whatsapp_enabled(self) -> bool:
        """True si hay credenciales para enviar por la Cloud API real."""
        return bool(self.whatsapp_token and self.whatsapp_phone_id)

    @property
    def gcal_enabled(self) -> bool:
        """True si hay credenciales para sincronizar con Google Calendar real."""
        return bool(self.google_credentials_file and self.google_calendar_id)

    @property
    def anthropic_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def webhook_signature_required(self) -> bool:
        """True si hay App Secret => se valida la firma de los webhooks de Meta."""
        return bool(self.whatsapp_app_secret)


@lru_cache
def get_settings() -> Settings:
    """Settings cacheado (un unico parseo del entorno por proceso)."""
    return Settings()


settings = get_settings()
