# Política de privacidad de JimiC

> Esta URL está fijada en la pantalla de consentimiento de Google: no se mueve ni se renombra.

## Qué es JimiC

JimiC es un agente de uso personal para una sola persona, la que lo ejecuta en su propio equipo.
No es un servicio público: no hay cuentas de otros usuarios ni datos de terceros.

## Qué datos de Google lee

Solo el calendario, en **solo lectura** (permiso `calendar.readonly`). De cada evento lee el título,
las horas, si dura todo el día, si el usuario es el organizador y si el usuario rechazó el evento:
Google envía la respuesta de cada invitado, sin su nombre ni su correo, y JimiC solo usa la del
usuario. **No lee la descripción**, la ubicación ni los correos de los asistentes. De la lista de calendarios lee también **el nombre de cada calendario visible, solo para reconocer y saltar el que crea Todoist**; no lo guarda. JimiC no puede crear, cambiar ni borrar nada en el calendario.

## Dónde se procesan los datos

- **En el equipo de quien ejecuta JimiC**: el PC o, más adelante, un servidor propio.
- Para decidir y redactar, el contenido necesario pasa a la **API de Anthropic**, bajo sus
  condiciones comerciales: no entrena modelos con él.
- No se vende, no se comparte con terceros y no se usa para publicidad ni para entrenar modelos.

## Qué se guarda y cuánto tiempo

- Los ficheros personales y la base de datos SQLite, solo en el equipo local y fuera del repositorio.
- El permiso que el usuario concede a Google se guarda en un fichero local (`google_token.json`,
  legible solo por su propietario), junto a los ficheros de configuración, hasta que se borre.
- La base de datos conserva la mayoría de los registros un plazo limitado (de 30 días a un año,
  según la tabla; las decisiones del latido, 90 días). La excepción es el historial de cambios de
  memoria (`changes`), que se guarda siempre, hasta que el usuario lo borre.
- **La conversación con el agente.** El motor del agente guarda la transcripción de cada día
  (los mensajes, las respuestas y lo que leyó de las fuentes) en una carpeta propia de JimiC, dentro
  de sus datos (`agent/`), legible solo por su propietario. Todo lo que el motor escribe ahí se
  borra a los 7 días, salvo los pocos ficheros que necesita para funcionar, que no llevan contenido
  (identificadores, contadores y ajustes). La base guarda, también 7 días, el identificador de la
  sesión de cada día, la hora del último mensaje del usuario y si la sesión leyó el calendario o las
  tareas; sin texto.
- **Lo que propone el agente.** Un plan (crear, mover o borrar tareas, con la foto de cada tarea
  que mueve o borra: título, fecha y proyecto) o un cambio de memoria se guarda como propuesta en la
  base, con el identificador del mensaje de Telegram que la enseña, sin ejecutarse, hasta que el
  usuario la apruebe; caduca al acabar el día y se borra a los 30 días. Lo que se hace al momento
  con [Deshacer] guarda igual, también 30 días, lo que hace falta para deshacerlo: la tarea creada
  (título, fecha y proyecto) o la movida y su fecha anterior.
- **Las tareas borradas.** Antes de borrar una tarea, JimiC guarda su foto completa (título,
  descripción, fecha, duración, proyecto, etiquetas, prioridad), el motivo del borrado y la
  aprobación, para poder recrearla. Se borra a los 30 días. Todoist no tiene papelera: sin esa
  foto, lo borrado no se podría recuperar.
- **Las ideas** que el usuario pide anotar van, una por línea y con su fecha, a `ideas.md`, junto a
  los ficheros personales; JimiC solo añade líneas, nunca reescribe el fichero.
- Cuando el latido pasa al agente el plan o el cierre del día, la base anota la intención y la
  hora (90 días), sin texto.
- La base guarda también el **gasto en el modelo** (por llamada: el día, el modelo, cuántos tokens
  y cuántos dólares; ni el texto enviado ni el recibido) y el **registro de los avisos del
  sistema** (el tipo, como «API caída», un código como `calendar:auth` y la hora; sin texto). Los
  dos, un año.
- Los registros técnicos (logs) no llevan contenido: ni títulos de eventos ni mensajes.

## Cómo retirar el permiso y borrar los datos

- Para retirar el permiso: <https://myaccount.google.com/permissions>.
- Para borrar los datos locales: eliminar las carpetas de JimiC en `~/.config` y `~/.local/share`
  (`jimic` y, si existe, la copia de pruebas `jimic-dev`). Ahí están también el fichero del
  cliente de Google y el permiso guardado: borrarlas y retirar el permiso son dos pasos distintos.

## Contacto

Los issues de este repositorio: <https://github.com/pdromer0/jimic/issues>.

## Cumplimiento

El uso que JimiC hace de la información recibida de las API de Google se ajusta a la
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
incluidos los requisitos de uso limitado (*Limited Use*).
