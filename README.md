# Agente WhatsApp — Clínica de podología

Bot de WhatsApp que atiende a clientes y reserva citas conversando en lenguaje natural
(Anthropic Claude + tool use), con la base de datos como **fuente de verdad** y Google
Calendar como espejo de salida. Backend en FastAPI.

Este repositorio cubre las **fases 1–4** del plan (`CLAUDE.md` §15): base de datos, webhook
de WhatsApp, agente conversacional y agenda con reserva/cancelación de citas. Las fases 5–8
(avisos programados, API y UI del panel, endurecimiento) están **pendientes**.

## Estado de las integraciones

| Integración | Estado | Comportamiento sin credenciales |
|---|---|---|
| Anthropic (Claude) | Requiere `ANTHROPIC_API_KEY` | Sin clave, el bot responde en **modo eco** |
| WhatsApp Cloud API | Opcional en dev | **Modo stub**: registra el envío en consola en vez de llamar a Meta |
| Google Calendar | Opcional en dev | **Modo stub**: registra el evento en consola; la cita vive igual en la DB |

Los modos stub se activan/desactivan solos según haya o no credenciales en `.env`
(ver `app/config.py`: `whatsapp_enabled`, `gcal_enabled`).

## Requisitos

- Python 3.11+
- En desarrollo: **SQLite** (sin instalar nada). En producción: **PostgreSQL** (necesario para
  el constraint anti-solape nativo `EXCLUDE`/`btree_gist`).

## Arranque local (Windows / PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# Configuración: copia el ejemplo y pon tu ANTHROPIC_API_KEY
Copy-Item .env.example .env   # luego edita .env

# Base de datos (SQLite por defecto) + datos iniciales
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m scripts.seed
```

### Probar el bot por terminal (sin Meta ni Google)

Con `ANTHROPIC_API_KEY` puesta en `.env`, conversa con el bot real (reserva citas de verdad
en la DB local; WhatsApp y Calendar quedan en stub):

```powershell
.\.venv\Scripts\python.exe -m scripts.chat_local
# escribe, p.ej.: "¿qué servicios tenéis?", "quiero quiropodia el lunes", ...
```

### Levantar el servidor (webhook + API)

```powershell
.\.venv\Scripts\uvicorn.exe app.main:app --reload
```

- `GET /health` — estado del servicio e integraciones.
- `GET /webhook` — verificación de Meta (`hub.challenge`).
- `POST /webhook` — recepción de mensajes (responde 200 rápido, procesa en background).
- `POST /dev/simulate` — inyecta un mensaje en el mismo pipeline sin pasar por Meta:
  `{"telefono": "34600000000", "texto": "hola"}` → devuelve la respuesta del bot.

## Verificación (criterios de aceptación cubiertos en fases 1–4)

- **Solo huecos reales**: `consultar_disponibilidad` respeta horarios, bloqueos, buffer y citas
  existentes (verificado: jornada L–V 9–14/16–20 con servicio de 30 min → 34 huecos; un bloqueo
  de tarde los elimina).
- **No hay doble reserva**: revalidación dentro de la transacción + `EXCLUDE` en PostgreSQL.
  En SQLite, la revalidación cubre el caso secuencial (SQLite serializa escrituras).
- **Idempotencia del webhook**: deduplicación por `wa_message_id` (reintentos de Meta no crean
  citas ni mensajes duplicados).
- **`bot_activo=false`** detiene las respuestas automáticas (mensaje de atención manual) sin caídas.
- **Toda cita creada/cancelada** intenta sincronizarse con Calendar (registrado en stub).

## Decisiones aplicadas (de `CLAUDE.md` §16)

- **DB**: SQLite en desarrollo, PostgreSQL en producción (código agnóstico vía `DATABASE_URL`).
  El anti-solape es un `EXCLUDE` con `btree_gist` que la migración aplica **solo** en PostgreSQL;
  en SQLite se valida en `app/services/agenda.py`.
- **Modelo Claude**: `claude-sonnet-4-6` por defecto (rápido y económico), configurable en
  `.env`/`config.modelo_claude`. Verificar el ID vigente en ejecución.
- **Zona horaria**: `Europe/Madrid` (`config.timezone`); todo se almacena en UTC-aware
  (ver `UTCDateTime` en `app/db.py`).
- **Servicios/horario (placeholder)**: Quiropodia 30/0, Estudio biomecánico 45/0, Uña encarnada
  40/0, Revisión 20/0; horario L–V 9:00–14:00 y 16:00–20:00. Editar en `scripts/seed.py` o en DB.
- **Scheduler y panel** (fases 5–8): pendientes. Recomendado: cron del sistema y panel Jinja2+HTMX.

## Estructura

```
app/
  main.py            FastAPI: monta el router del webhook
  config.py          settings desde .env (+ flags de integraciones)
  db.py              engine/sesión/Base + UTCDateTime
  models.py          modelos SQLAlchemy (todas las tablas de §4)
  schemas.py         Pydantic (entrada/salida API)
  routers/webhook.py GET/POST /webhook + /dev/simulate
  services/
    agente.py        Claude + bucle de tool use + historial
    agenda.py        disponibilidad, crear/cancelar cita (regla de negocio)
    calendar_gcal.py Google Calendar (stub si no hay credenciales)
    whatsapp.py      envío de texto/plantillas (stub si no hay credenciales)
    config_repo.py   acceso a la tabla config + zona horaria
alembic/             migraciones (EXCLUDE condicional a PostgreSQL)
scripts/
  seed.py            carga inicial idempotente
  chat_local.py      REPL de prueba contra el pipeline del agente
```

## Despliegue (resumen, `CLAUDE.md` §13 — fase futura)

1. VPS Ubuntu: `python3-venv`, `nginx`, `certbot`, `postgresql`, `libpq-dev`.
2. PostgreSQL: crear rol/base; el `EXCLUDE` (con `btree_gist`) lo crea la migración; `alembic upgrade head`; `python -m scripts.seed`.
   - Driver: descomentar `psycopg[binary]` en `requirements.txt` y usar `DATABASE_URL=postgresql+psycopg://...`.
3. TLS: Nginx reverse proxy a `127.0.0.1:8000` + `certbot --nginx`.
4. Proceso: `systemd` ejecutando `uvicorn app.main:app` (ver `CLAUDE.md` §13 para la unidad).
5. Meta: registrar webhook `https://<dominio>/webhook`, suscribir el campo `messages`, crear y aprobar las plantillas.
6. Google: activar Calendar API, cuenta de servicio, compartir el calendario con su `client_email`.
