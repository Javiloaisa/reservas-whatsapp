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

## 2. `resumen_diario`

- **Nombre**: `resumen_diario`
- **Categoría**: Utility (utilidad)
- **Idioma**: Spanish (es)
- **Variables**: `{{1}}` = resumen de la agenda en una línea

**Cuerpo:**

```
Buenos días. Agenda de hoy en la clínica: {{1}}
```

**Valor de ejemplo para la revisión:**
- {{1}}: `3 citas: 09:00 Quiropodia (María López); 10:30 Primera visita (Juan Ruiz); 16:00 Ortonixia (Ana Gil)`

> Las variables de plantilla no admiten saltos de línea: el resumen va en una
> sola línea, separado por punto y coma (así lo genera `avisos._resumen_texto`).

---

## Notas

- **Categoría Utility**: son notificaciones transaccionales (no marketing), la
  tarifa Meta más baja. No añadir texto promocional o Meta puede reclasificarla.
- Tras la aprobación, activar el crontab (`deploy/crontab.example`) y rellenar
  `config.podologo_whatsapp` (panel → Ajustes) para el resumen diario.
- Si Meta rechaza una plantilla (poco probable con estos textos), el motivo
  aparece en el panel; normalmente basta reformular y reenviar.
