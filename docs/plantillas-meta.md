# Plantillas de WhatsApp (Meta) — textos para aprobación

Los avisos programados (§11) son mensajes iniciados por el negocio fuera de la
ventana de 24 h, así que Meta exige **plantillas pre-aprobadas**. Se crean desde
el panel de YCloud (WhatsApp accounts → **Manage templates** → Create template)
y Meta las revisa (suele tardar de minutos a horas).

Los **nombres** y **variables** deben ser EXACTAMENTE estos (el código los usa
así en `app/services/avisos.py`):

---

## 1. `recordatorio_cita`

- **Nombre**: `recordatorio_cita`
- **Categoría**: Utility (utilidad)
- **Idioma**: Spanish (es)
- **Variables**: `{{1}}` = nombre del cliente · `{{2}}` = servicio · `{{3}}` = hora

**Cuerpo:**

```
Hola {{1}}, te recordamos tu cita de {{2}} mañana a las {{3}} en Jesús García Podòleg.

Si necesitas cambiar o cancelar la cita, responde a este mensaje.
```

**Valores de ejemplo para la revisión de Meta** (los pide el formulario):
- {{1}}: `María`
- {{2}}: `Quiropodia`
- {{3}}: `10:30`

> El job envía el recordatorio entre 23 y 25 h antes de la cita, por lo que
> "mañana" siempre es correcto con el horario de la clínica (9:00–20:00).

---

## 2. Resumen diario → **por Telegram, no por WhatsApp**

El resumen de fin de día al podólogo **ya no usa una plantilla de Meta**. Va por
**Telegram** (decisión del usuario 2026-07-07): WhatsApp no permite escribirse al
mismo número desde el que se opera (coexistencia), así que el podólogo no puede
recibirse a sí mismo el resumen por WhatsApp.

> Quedó obsoleta la antigua plantilla `resumen_dia`. No hace falta crearla ni
> aprobarla en Meta. Si ya estaba dada de alta, se puede ignorar/borrar.

Configuración (nada que aprobar en Meta):

1. Crear un bot de Telegram con **@BotFather** → copiar el token a `TELEGRAM_TOKEN`
   en el `.env`.
2. El podólogo abre un chat con ese bot y le escribe una vez; obtener el `chat_id`
   (p. ej. `https://api.telegram.org/bot<TOKEN>/getUpdates`) y ponerlo en
   `TELEGRAM_CHAT_ID` (o en el panel → **Ajustes → Chat de Telegram del podólogo**).

El texto lo genera `avisos._resumen_texto` (multilínea, sin restricciones de plantilla).

---

## Notas

- **Categoría Utility** (recordatorio): notificación transaccional (no marketing),
  la tarifa Meta más baja. No añadir texto promocional o Meta puede reclasificarla.
- Tras aprobar `recordatorio_cita`, activar el crontab (`deploy/crontab.example`).
  Para el resumen basta con `TELEGRAM_TOKEN` + `TELEGRAM_CHAT_ID` (sin paso por Meta).
- Si Meta rechaza la plantilla de recordatorio (poco probable con este texto), el
  motivo aparece en el panel; normalmente basta reformular y reenviar.
