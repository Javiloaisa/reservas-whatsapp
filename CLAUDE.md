# CLAUDE.md — Agente de citas por WhatsApp para clínica de podología (v2, coexistencia)

> Especificación técnica para implementación con Claude Code. Define arquitectura, esquema de datos, contratos, lógica del agente y criterios de aceptación. Asume VPS Ubuntu (Hetzner) ya operativo y conocimientos básicos de despliegue. Las decisiones abiertas se marcan como **[DECISIÓN]**.
>
> **Cambio principal respecto a v1 (mayo 2026):** el bot NO usa un número dedicado. Se conecta al número actual del podólogo mediante **WhatsApp Coexistence** (app WhatsApp Business + Cloud API en el mismo número), a través del BSP **YCloud** como capa de conexión. El podólogo sigue usando su app con normalidad; el bot solo responde mensajes de citas.

---

## 1. Objetivo

Construir un sistema que:

1. Escuche todos los mensajes entrantes del número de WhatsApp Business del podólogo (vía coexistencia + YCloud).
2. **Responda únicamente a mensajes relacionados con citas** (pedir, cambiar, cancelar, consultar disponibilidad). Ante cualquier otro tema o ante la duda: **silencio total** — el podólogo responde manualmente desde su app.
3. Gestione la reserva conversando en lenguaje natural (español) y agende en Google Calendar respetando servicios, horarios y bloqueos.
4. Envíe al podólogo un resumen diario de citas por WhatsApp.
5. Recuerde al cliente su cita 24 h antes (plantilla aprobada por Meta).
6. Arranque en **modo sombra** (clasifica y registra sin enviar nada) hasta activación manual.

**Fase 1 = solo bot + resumen diario + recordatorios.** El panel de administración web queda para fase 2 (no construir ahora, pero el esquema de datos ya lo contempla).

---

## 2. Arquitectura

```
Cliente WhatsApp ───► Número del podólogo (coexistencia)
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
     App WhatsApp Business      YCloud (BSP / Cloud API)
     (móvil del podólogo,             │ webhook entrante
      responde lo no-citas)           ▼
                          FastAPI (VPS Hetzner)
                          https://podologo-api.comboilabs.com/webhook
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
              PostgreSQL        Claude (Anthropic    Google Calendar
              (estado, citas,    API, tool use)      (service account)
               mensajes, logs)
                                      │
                                      ▼
                          Respuesta vía API de YCloud
                          (solo si intención = cita y bot activo)
```

### Stack

- Python 3.11+, FastAPI, uvicorn.
- PostgreSQL + SQLAlchemy + Alembic.
- Anthropic API (`claude-sonnet-4-6`) con tool use.
- **YCloud** como capa WhatsApp (webhook entrante + API de envío). Ver §8.
- Google Calendar API vía service account.
- Nginx + Let's Encrypt en el VPS Hetzner existente (compartido con `crypto-agent`; servicios systemd independientes).
- Dominio: `podologo-api.comboilabs.com` (CNAME/A hacia el VPS). No usar DuckDNS.
- Scheduler: cron del sistema para resumen diario y recordatorios.

### Estructura del repo

```
agente-podologo/
├── CLAUDE.md
├── prompts/
│   ├── system_agente.md        # prompt del agente conversacional
│   └── system_clasificador.md  # prompt del clasificador de intención
├── app/
│   ├── main.py                 # FastAPI, rutas webhook
│   ├── models.py               # SQLAlchemy
│   ├── services/
│   │   ├── whatsapp.py         # interfaz abstracta del proveedor
│   │   ├── whatsapp_ycloud.py  # implementación YCloud
│   │   ├── clasificador.py     # intención citas / no-citas
│   │   ├── agente.py           # loop Claude + tool use
│   │   ├── agenda.py           # lógica de disponibilidad
│   │   └── calendar.py         # Google Calendar
│   ├── jobs/
│   │   ├── resumen_diario.py
│   │   └── recordatorios.py
│   └── seed.py
├── alembic/
├── .env.example
└── deploy/ (nginx conf, systemd units)
```

---

## 3. Capa WhatsApp: abstracción de proveedor (services/whatsapp.py)

**Requisito de diseño clave:** todo el sistema habla con una interfaz abstracta, no con YCloud directamente, para poder migrar a 360dialog o Cloud API directa sin tocar el resto.

```python
class WhatsAppProvider(Protocol):
    def parse_webhook(self, payload: dict) -> MensajeEntrante | EchoSaliente | Otro
    def enviar_texto(self, telefono: str, texto: str) -> str          # devuelve message_id
    def enviar_plantilla(self, telefono: str, plantilla: str, variables: dict) -> str
```

`MensajeEntrante`: telefono, nombre_perfil, texto, message_id, timestamp.
`EchoSaliente`: mensaje que el PODÓLOGO envió desde su app (llega por webhook como eco). Campos: telefono_destino, texto, timestamp.

Implementación fase 1: `whatsapp_ycloud.py` contra la API v2 de YCloud (docs: docs.ycloud.com). Autenticación por API key en header. Verificar la firma del webhook si YCloud la ofrece; si no, validar por token secreto en la URL del webhook.

---

## 4. Flujo por mensaje entrante

1. Parsear payload → si es `EchoSaliente`, registrar en `mensajes` (rol=`podologo_manual`) y activar **modo humano** para ese cliente (ver §5). Fin.
2. Si es `MensajeEntrante`:
   a. Resolver/crear `cliente` por teléfono.
   b. Si `config.bot_activo == false` (modo sombra global): ejecutar clasificador + agente en seco, registrar todo en `mensajes` y `log_sombra` con la respuesta QUE SE HABRÍA enviado, **no enviar nada**. Fin.
   c. Si el cliente está en **modo humano** (§5): registrar mensaje, no responder. Fin.
   d. **Clasificador de intención** (§6): ¿es sobre citas?
      - NO o DUDA → registrar con etiqueta `no_cita`, silencio. Fin.
      - SÍ → continuar.
   e. Cargar últimos N=20 mensajes del cliente como historial.
   f. Llamar a Claude (system = prompts/system_agente.md + servicios y horarios inyectados desde BD) con tools (§7). Loop de tool use hasta respuesta final.
   g. Guardar mensajes (user y assistant) en `mensajes`.
   h. Enviar respuesta final vía `WhatsAppProvider.enviar_texto`.

---

## 5. Regla de no-interferencia con el podólogo ("modo humano")

Objetivo: el bot jamás pisa una conversación que el podólogo esté llevando en persona.

- Cuando llega un `EchoSaliente` hacia un teléfono X, se marca `clientes.modo_humano_hasta = now() + intervalo` para X.
- **[DECISIÓN]** intervalo por defecto: **4 horas** (configurable en `config`).
- Excepción: si el eco coincide exactamente con un texto que el propio bot acaba de enviar (mismo texto, <60 s), es el eco de nuestro propio envío → ignorar, no activar modo humano. Deduplicar por `message_id` cuando YCloud lo permita.
- Mientras `modo_humano_hasta > now()`, el bot no responde a ese cliente aunque pida cita. El mensaje queda registrado.
- El podólogo puede reactivar el bot para un cliente antes de tiempo escribiendo la palabra clave `#bot` en el chat (detectada vía eco). **[DECISIÓN]** confirmar palabra clave con el podólogo.

---

## 6. Clasificador de intención (services/clasificador.py)

- Llamada rápida a Claude (mismo modelo, `max_tokens` bajo) con `prompts/system_clasificador.md`.
- Entrada: el mensaje nuevo + los últimos 5 mensajes de contexto (una conversación de cita en curso debe seguir clasificando como CITA aunque el mensaje suelto sea ambiguo, p. ej. "mejor a las 10").
- Salida estricta JSON: `{"intencion": "cita" | "no_cita" | "duda"}`.
- Política: solo `"cita"` pasa al agente. `"no_cita"` y `"duda"` → silencio.
- Registrar siempre la clasificación en `mensajes.clasificacion` para auditoría y para revisar el modo sombra.
- Casos que SON cita: pedir/reservar, cambiar, cancelar, preguntar disponibilidad u horarios de atención, confirmar asistencia, preguntar duración o precio de un servicio con intención de reservar.
- Casos que NO son cita: consultas clínicas ("me duele el pie"), resultados, facturas, proveedores, temas personales, audios (fase 1 no transcribe: los audios se clasifican como `duda` → silencio).

---

## 7. Agente conversacional (services/agente.py) y herramientas

Las herramientas son la **única** vía por la que el agente modifica estado. La validación vive en `services/agenda.py`, no en el prompt.

| Herramienta | Entrada | Acción | Validaciones |
|---|---|---|---|
| `listar_servicios` | — | Servicios activos con duración y precio | — |
| `consultar_disponibilidad` | `fecha` (YYYY-MM-DD), `servicio_id` | Huecos libres ese día para ese servicio | Respeta `horarios`, `bloqueos`, `citas` y `buffer_min` |
| `crear_cita` | `nombre`, `servicio_id`, `inicio_iso` | Crea cita en BD + evento en Calendar | Revalida hueco en transacción atómica |
| `cancelar_cita` | `cita_id` o (`telefono`+`fecha`) | Marca cancelada + borra evento Calendar | Solo citas futuras del propio cliente |

Pautas del system prompt del agente (prompts/system_agente.md, ya existente de v1 — reutilizar):

- Rol: asistente de la clínica **Jesús García Podòleg** por WhatsApp. Tono amable, conciso, español siempre.
- Inyectar dinámicamente servicios y horarios desde BD (el texto fijo del prompt es solo respaldo).
- Confirmar servicio + día + hora antes de crear. Pedir nombre si no se conoce.
- Nunca prometer un hueco sin `consultar_disponibilidad`. Zona horaria `Europe/Madrid`.
- Si el cliente pregunta algo fuera de citas a mitad de conversación: responder solo la parte de cita e indicar que para lo demás le atenderá el podólogo en este mismo chat.
- Mensajes cortos, sin markdown pesado (es WhatsApp).

---

## 8. Integración YCloud (services/whatsapp_ycloud.py)

- **Alta (manual, no código):** cuenta gratuita en ycloud.com → producto "WhatsApp Business App Coexistence" → Embedded Signup → el podólogo escanea el QR desde su app (Ajustes → Dispositivos vinculados / notificación de Meta) y acepta compartir historial. Requisitos: app WhatsApp Business ≥ 2.24.17, Meta Business Manager del podólogo (crearlo si no existe; añadir a Comboi como admin).
- **Webhook entrante:** configurar en el panel de YCloud apuntando a `https://podologo-api.comboilabs.com/webhook`. Suscribirse a mensajes entrantes y a ecos/estados de salida si están disponibles.
- **Envío:** `POST https://api.ycloud.com/v2/whatsapp/messages` (texto libre dentro de ventana de 24 h) y endpoint de plantillas para los recordatorios. API key en `.env` (`YCLOUD_API_KEY`).
- Los mensajes de sesión (respuestas dentro de la ventana de 24 h abierta por el cliente) se facturan a tarifa Meta de categoría servicio (mínima). Los recordatorios 24 h son plantilla de categoría *utility* (tarifa Meta, sin markup de YCloud).
- **Límite de coexistencia:** ~5 msg/s. Irrelevante para este volumen, pero no hacer envíos en ráfaga.
- Manejar idempotencia: deduplicar webhooks por `message_id` (tabla `mensajes.message_id_proveedor` con unique).

### Restricciones operativas de la coexistencia (documentar para el podólogo)

- Debe abrir la app WhatsApp Business al menos una vez cada 13 días.
- No desinstalar la app ni eliminar la cuenta (rompe la conexión).
- Difusiones y mensajes temporales/de una sola vez dejan de funcionar en el número.
- Los mensajes de GRUPOS no llegan al webhook: el bot no los ve (confirmado con el podólogo que no gestiona citas por grupos — verificar antes del alta).
- Los dispositivos vinculados se desvinculan durante el alta; revincular después los compatibles (WhatsApp para Windows no es compatible).

---

## 9. Lógica de disponibilidad (services/agenda.py)

`huecos_libres(fecha, servicio)`:

1. Franjas de `horarios` para el `dia_semana` de `fecha`.
2. Restar `bloqueos` que solapen.
3. Restar `citas` no canceladas del día, ampliadas con `buffer_min`.
4. Generar slots de `duracion_min + buffer_min` con paso de 15 min.
5. Devolver inicios válidos futuros (nunca ofrecer huecos pasados; margen mínimo de antelación configurable, por defecto 2 h).

`crear_cita(...)`:

- Reverificar el hueco dentro de una transacción (`SELECT ... FOR UPDATE` sobre las citas del día, o exclusion constraint con `tstzrange` + `btree_gist` — preferida la constraint).
- Insertar en `citas`, crear evento en Calendar, guardar `gcal_event_id`. Si Calendar falla: revertir la cita o marcarla `pendiente_sync` y reintentar por job (no dejar estados inconsistentes silenciosos).

---

## 10. Esquema de datos (PostgreSQL)

Tablas (campos principales; Claude Code completa tipos e índices):

- `clientes`: id, telefono (unique), nombre, modo_humano_hasta, creado_en.
- `mensajes`: id, cliente_id, rol (`cliente` | `bot` | `podologo_manual`), texto, clasificacion, message_id_proveedor (unique, nullable), creado_en.
- `servicios`: id, nombre, duracion_min, buffer_min, precio (nullable), activo.
- `horarios`: id, dia_semana (0–6), hora_inicio, hora_fin.
- `bloqueos`: id, inicio, fin, motivo.
- `citas`: id, cliente_id, servicio_id, inicio, fin, estado (`activa` | `cancelada` | `pendiente_sync`), gcal_event_id, creado_en. Exclusion constraint anti-solape sobre citas activas.
- `config`: clave/valor — `bot_activo` (bool, default false = modo sombra), `timezone` (`Europe/Madrid`), `direccion_clinica` (placeholder), `intervalo_modo_humano_horas` (4), `antelacion_minima_horas` (2), `telefono_podologo` (para el resumen diario).
- `log_sombra`: id, cliente_id, mensaje_entrante, clasificacion, respuesta_no_enviada, creado_en.

---

## 11. Jobs programados (cron)

- `resumen_diario.py` — L–V a las 08:00 Europe/Madrid: mensaje al `telefono_podologo` con las citas del día (hora, nombre, servicio). Se envía como texto libre si hay ventana abierta; si no, plantilla `resumen_diario` aprobada. **[DECISIÓN]** alternativa cero-coste: enviar el resumen por Telegram (patrón ya conocido de otros proyectos) — confirmar preferencia del podólogo.
- `recordatorios.py` — cada hora: buscar citas activas cuyo inicio esté entre 23 h y 24 h vista sin recordatorio enviado; enviar plantilla `recordatorio_cita` (variables: nombre, servicio, fecha, hora) y marcar enviado.
- Plantillas a redactar y aprobar en Meta vía YCloud antes de activar producción. **[DECISIÓN]** textos definitivos.

---

## 12. Modo sombra y activación

- `config.bot_activo = false` por defecto. En sombra, TODO el pipeline corre (clasificación + agente + herramientas de solo lectura) pero: no se envía nada por WhatsApp y `crear_cita`/`cancelar_cita` se ejecutan en seco (log, sin escribir en BD ni Calendar).
- Revisión con el podólogo tras ~1 semana usando `log_sombra` (query o CSV export simple).
- Activación: `UPDATE config SET valor='true' WHERE clave='bot_activo';` — documentar el comando en el README.

---

## 13. Seed (app/seed.py) — datos reales

Clínica: **Jesús García Podòleg** · Zona horaria `Europe/Madrid` · Dirección: **[pendiente]**.

### Servicios

| Nombre | duracion_min | buffer_min |
|---|---|---|
| Primera visita | 45 | 0 |
| Quiropodia | 45 | 0 |
| Exploración biomecánica | 60 | 0 |
| Exploración biomecánica + análisis de la carrera | 90 | 0 |
| Entrega de resultados | 30 | 0 |
| Revisión soportes plantares | 15 | 0 |
| Revisión quiropodia | 15 | 0 |
| Vendaje deportivo | 15 | 0 |
| Cura papiloma | 15 | 0 |
| Silicona simple | 15 | 0 |
| Silicona complicada | 30 | 0 |
| Reconstrucción ungueal | 40 | 0 |
| Ortonixia | 30 | 0 |

`precio` nulo hasta que el podólogo lo aporte.

### Horarios (dia_semana: 0=lunes … 6=domingo)

| dia_semana | hora_inicio | hora_fin |
|---|---|---|
| 0 (lunes) | 09:00 | 13:30 |
| 1 (martes) | 09:00 | 13:30 |
| 1 (martes) | 15:00 | 20:00 |
| 2 (miércoles) | 09:00 | 13:30 |
| 2 (miércoles) | 15:00 | 20:00 |
| 3 (jueves) | 09:00 | 15:00 |
| 4 (viernes) | 09:00 | 15:00 |

Sábado y domingo: cerrado (sin filas).

---

## 14. Despliegue

- VPS Hetzner existente (comparte máquina con `crypto-agent`; aislar en usuario de sistema y unit systemd propios: `agente-podologo.service`).
- Nginx: server block para `podologo-api.comboilabs.com` → proxy a `127.0.0.1:8001` (el 8000 puede estar ocupado — verificar). Certbot para HTTPS.
- DNS: registro A/CNAME de `podologo-api.comboilabs.com` al VPS (hacerlo en el panel del dominio de comboilabs.com).
- `.env`: `YCLOUD_API_KEY`, `YCLOUD_WEBHOOK_SECRET`, `ANTHROPIC_API_KEY`, `DATABASE_URL`, `GOOGLE_SERVICE_ACCOUNT_JSON` (ruta), `GCAL_CALENDAR_ID`.
- Logs a journald; nivel INFO con los payloads de webhook en DEBUG (sin volcar datos personales a INFO).
- RGPD: los mensajes contienen datos personales y potencialmente de salud. Minimizar retención (**[DECISIÓN]** purga de `mensajes` > 12 meses vía job), acceso restringido al VPS, y añadir cláusula informativa: primer mensaje del bot a un cliente nuevo debe incluir una línea breve de "asistente automático de citas" (transparencia sobre IA + tratamiento de datos por la clínica).

---

## 15. Fases de construcción y criterios de aceptación

**Fase A — esqueleto:** FastAPI + webhook YCloud verificado + BD migrada + seed cargado. → Un mensaje de prueba queda registrado en `mensajes`.

**Fase B — clasificador + modo sombra:** → Mensajes de cita vs no-cita quedan bien etiquetados en `log_sombra`; nada se envía.

**Fase C — agente completo en sombra:** tool use + agenda + Calendar en seco. → `log_sombra` muestra conversaciones completas coherentes con huecos reales.

**Fase D — activación controlada:** `bot_activo=true`, pruebas con los teléfonos de los socios. → Reserva real creada en Calendar y visible en la app del podólogo como mensaje del número.

**Fase E — jobs:** resumen diario + recordatorios con plantillas aprobadas. → Recordatorio recibido 24 h antes de una cita de prueba.

**Fase F (futuro, no construir):** panel admin, transcripción de audios, métricas.

---

## 16. Decisiones abiertas [DECISIÓN]

1. Intervalo de modo humano (default 4 h) y palabra clave de reactivación (`#bot`).
2. Resumen diario por WhatsApp vs Telegram.
3. Textos definitivos de las plantillas (`recordatorio_cita`, `resumen_diario` si aplica) para aprobación en Meta.
4. Precios de los servicios y dirección de la clínica.
5. Política de retención de mensajes (default: purga a 12 meses).
6. Puerto interno del servicio (default 8001; verificar colisiones en el VPS).
