# Política de privacidad de JimiC

> Esta URL está fijada en la pantalla de consentimiento de Google: no se mueve ni se renombra.

## Qué es JimiC

JimiC es un agente de uso personal para una sola persona, la que lo ejecuta en su propio equipo.
No es un servicio público: no hay cuentas de otros usuarios ni datos de terceros.

## Qué datos de Google lee

Solo el calendario, en **solo lectura** (permiso `calendar.readonly`). De cada evento lee el título,
las horas y si el usuario es el organizador. **No lee la descripción** ni los asistentes. JimiC no
puede crear, cambiar ni borrar nada en el calendario.

## Dónde se procesan los datos

- **En el equipo de quien ejecuta JimiC**: el PC o, más adelante, un servidor propio.
- Para decidir y redactar, el contenido necesario pasa a la **API de Anthropic**, bajo sus
  condiciones comerciales: no entrena modelos con él.
- No se vende, no se comparte con terceros y no se usa para publicidad ni para entrenar modelos.

## Qué se guarda y cuánto tiempo

- Los ficheros personales y la base de datos SQLite, solo en el equipo local y fuera del repositorio.
- La base de datos conserva cada tipo de registro un plazo limitado (de 30 días a un año, según
  la tabla; las decisiones del latido, 90 días). Las transcripciones locales del agente se borran
  a los 7 días.
- Los registros técnicos (logs) no llevan contenido: ni títulos de eventos ni mensajes.

## Cómo retirar el permiso y borrar los datos

- Para retirar el permiso: <https://myaccount.google.com/permissions>.
- Para borrar los datos locales: eliminar las carpetas de JimiC en `~/.config` y `~/.local/share`.

## Contacto

Los issues de este repositorio: <https://github.com/pdromer0/jimic/issues>.

## Cumplimiento

El uso que JimiC hace de la información recibida de las API de Google se ajusta a la
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
incluidos los requisitos de uso limitado (*Limited Use*).
