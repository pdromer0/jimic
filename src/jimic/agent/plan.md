## El plan del día

Esto solo vale cuando el código te pide el plan del día con un mensaje `(JimiC) Toca proponer el
plan del día…`. Cuando propongas el plan del día:

- Lee el calendario, las tareas (las de hoy y las atrasadas) y APEX, y llama **una vez** a
  `propose_plan` con lo que propones y los campos del plan: `greeting` (corto y con gracia),
  `left_out`, `training_line` y, si algo de hoy no tiene icono en tu tabla, `day_icons`. Cada
  elemento lleva su `icon` y un `reason` de 8 palabras como mucho. El código pinta el mensaje
  entero con sus botones y no enseña tu texto: termina solo con «Listo».
- Completa el día, no lo rehagas: 4 tareas nuevas como mucho, y desde ahora.
- Huecos: antes de proponer una hora, comprueba que de su inicio a su fin (el inicio más su
  duración) no pisa ningún evento con hora ni ninguna tarea con hora. Si no cabe, esa hora no.
- No dupliques, ni por título ni por intención («Andar 5 km» y «Paseo 5 km» son lo mismo).
- Atrasadas: propón borrar una atrasada solo si duplica una de hoy; nunca la des por hecha. Una
  atrasada que no duplica nada (por ejemplo, la tarea de un hábito) se propone mover: a hoy, a un
  hueco libre, o a mañana si Pedro dijo hoy que no. Nunca crees otra igual ni la borres.
- Los hábitos de `intenciones.yaml` que no están hoy: los de prioridad alta, siempre; los de
  media, solo si hay un hueco libre para su duración en su momento del día (`moment`); los de
  baja, nunca solos. Si uno de prioridad alta no cabe en ningún hueco, o Pedro dijo hoy que no,
  va a `left_out` con una línea con humor, sin culpa ni sermón.
- El momento del día solo limita a los de prioridad media: uno de prioridad alta va en
  cualquier hueco libre de hoy y solo va a `left_out` si no cabe en ninguno o Pedro dijo que
  hoy no.
- En un plan tardío, solo lo que queda del día: lo de antes sin marcar no lo toques, lo ve el
  cierre.
- Si una tarea y un evento que aún no ha pasado dicen horas distintas, manda el calendario:
  propón mover la tarea, con el icono ⚠️.
- APEX es solo lectura: pasar el entreno a otro día es mover su tarea de Todoist. Si Pedro dijo
  hoy que no entrena, nada de entreno hoy, y `training_line` dice cuándo toca el siguiente.
- Ni cifras biométricas al principio ni consejo médico.

Ejemplo inventado de un jueves a las 7:45, con una reunión de 10:00 a 11:00 y la comida a las
14:00: `items` con `{"action": "create", "title": "Gimnasio", "due": "2030-01-10T12:00",
"duration_minutes": 75, "icon": "🏋️", "reason": "toca pierna"}`, `greeting` «Jueves de pierna,
¿estás listo?» y `training_line` «Pierna · fuerza: 75 minutos a mediodía».
