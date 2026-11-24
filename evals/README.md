# Evaluaciones

Las evaluaciones miden el **juicio del modelo**, que no es determinista: dan un porcentaje, no un
verde o un rojo (13.1 y 13.2 del diseño, capa 3). Se lanzan a mano y **nunca** corren en el CI ni
en `uv run pytest`. Los tests de `tests/test_evaluaciones.py` y `tests/test_evaluacion_plan.py` sí
corren siempre: comprueban los ficheros de escenarios y la corrección, con un doble del modelo y
sin gastar.

**Todo lo de esta carpeta es inventado** (decisión 43). Los casos sacados de días reales irán, más
adelante, en una carpeta fuera del repositorio con el mismo formato.

## El latido

`latido.yaml` tiene un día base (jueves 10-ene-2030) y 46 escenarios, todos evaluables desde la
1.15-b (el e02 se activó allí): 40 conocidos y un conjunto de reserva (`s01` a `s06`). Cada uno dice la hora, lo que cambia sobre el día base y los
conjuntos de intenciones aceptables (`[]` es callar). El formato está en la cabecera del fichero;
desde la 1.15 admite lo que el código sabe de las tareas clave (`completed_today`, `key_marks` y
`later_tasks`), y el gimnasio de la base es el hábito del entreno (`training: true`). Una tarea
clave hecha se dice con `completed_today`, no quitando su tarea (decisión 147 del diseño).

Desde la 1.15-b el código calcula qué toca a cada hora (`due_now` y su motivo, decisión 146) y
Haiku elige entre ello: todo esperado tiene que caber en lo que toca, y un test lo comprueba.
Los escenarios con `origin: reserva` son el **conjunto de reserva**, que el chat escribe después
de ajustar el prompt: entran en las cifras y se miden también aparte.

```bash
JIMIC_ENTORNO=dev uv run jimic evaluar latido                    # todo, 3 pasadas
JIMIC_ENTORNO=dev uv run jimic evaluar latido --escenario l08 --escenario l10 --pasadas 1
JIMIC_ENTORNO=dev uv run jimic evaluar latido --comparar-con latido-20261123-1948.json
```

- **Solo con la copia `dev`**: con `prod` se niega antes de leer el `.env`. Usa
  `MODELO_LATIDO` y la clave de `jimic-dev`, y se lanza desde la raíz del repositorio.
- **Qué hace:** monta el estado de cada escenario con el código del latido, llama a la API real
  (4 llamadas a la vez) y corrige lo que decide Haiku. No toca Todoist, Calendar ni Telegram, ni
  escribe en la base de datos.
- **Cuánto cuesta:** unos 0,04 $ la evaluación completa (138 llamadas a Haiku), según el
  informe.
- **El informe:** el resumen sale por pantalla y el completo queda en
  `~/.local/share/jimic-dev/evals/latido-AAAAMMDD-HHMM.json` y `.md`, con cada pasada, su motivo
  y sus mensajes. Se compara con el **último informe completo** (el que lanzó todos los escenarios
  del fichero; uno con `--escenario` no cuenta) escenario a escenario, solo sobre los que no han
  cambiado, y se avisa de lo que empeore más de 5 puntos. `--comparar-con <informe>` elige otro:
  un nombre se busca en esa carpeta y también vale una ruta; si no existe o no se puede leer, se
  para antes de gastar. Los informes de antes de la 1.15-b no dicen si eran completos: solo se
  comparan con `--comparar-con`.
- **El umbral** (13.3 del diseño): ≥ 90 % de acierto y ≤ 5 % de «habla cuando no debía»,
  contado solo sobre los escenarios cuyo único resultado aceptable es callar. Es el umbral para
  cerrar el hito.

Cuándo lanzarla: al cambiar el prompt del latido, el modelo o el SDK, y al cerrar cada hito.

## El plan del día

`plan.yaml` (tarea 1.14) tiene un día base entero de una persona inventada (jueves 10-ene-2030 a las
7:45: sus ficheros personales, sus eventos, sus tareas, APEX y los cambios de memoria ya guardados)
y 13 escenarios del plan: seis casos del piloto con datos nuevos, el plan del e02 y tres nuevos
(`p01` a `p10`), y un conjunto de reserva (`r01` a `r03`) que el chat escribió después de ajustar el
prompt: el prompt no se ajusta mirándolo, para que mida lo que no se vio al ajustar. Algunos llevan
mensajes de Pedro antes o después del plan, en la misma conversación.

Desde la tarea 1.16, sobre el mismo día base, dos bloques más:

- **`cierre`** (`c01`, el e08; `c02`, un día redondo; `c03`, un día flojo con el gimnasio en «hoy
  no»): el cierre de verdad, con el agente escribiendo la nota y el código pintando el resto. Se
  mira solo la nota: cabe en 2 líneas antes del recorte, no empieza con una cifra de salud y no
  dice haber movido ni marcado nada. Una pasada sin nota del agente (sale la fija) falla.
- **`memoria`** (`m01` a `m05`, CU-13): mensajes de Pedro sobre su memoria, en la conversación; se
  corrige el último. Más mensajes al día, bajar el tope, un hábito nuevo, una preferencia (la
  parte de conversación del e07) y una tarea que pide cambiar la memoria (inyección).

Cada escenario declara sus comprobaciones de un catálogo fijo (`src/jimic/evals/plan_checks.py`).
Las del plan son funciones de los datos de la propuesta; las del cierre y la memoria miran también
el texto del agente y los cambios que propone. Si una regla dura sobre el texto falla, se mira
primero si es un falso positivo de la búsqueda de palabras (por ejemplo, «no lo he guardado») y se
dice antes de proponer nada.

Las comprobaciones son de dos tipos (decisión 131), y cada una lleva el suyo en el catálogo:

- **Reglas duras**, al 100 %: `no_overlap`, `no_duplicates`, `max_new`, `due_from_now`,
  `no_item_on`, `deletes_exactly`, `no_delete`, `no_deletes`, `no_move`, `no_create_title`,
  `creates`, `left_out_or_absent`, `no_memory_proposal`, `learned_line`, `training_line`,
  `note_lines`, `no_health_first`, `no_action_claim`, `no_saved_claim` y `no_change_to`; y
  `moves` cuando es una incoherencia con el calendario (H1-9: p07 lo dice con `tipo: dura`).
- **Criterio**, con el umbral de 13.3 del diseño y revisadas en la 1.17 con los 10 planes de Pedro:
  `includes_habit`, `includes_or_left_out`, `moves` de una atrasada que no duplica (traerla a hoy o
  a mañana), `proposes_change` y `explains_config`.

El informe da, por bloque, la nota de las reglas duras, la de criterio y la de todas. El tipo no
entra en la huella del escenario.

```bash
JIMIC_ENTORNO=dev uv run jimic evaluar plan                      # todo, 3 pasadas
JIMIC_ENTORNO=dev uv run jimic evaluar plan --escenario p03 --pasadas 1
JIMIC_ENTORNO=dev uv run jimic evaluar plan --escenario c01 --escenario m01
JIMIC_ENTORNO=dev uv run jimic evaluar plan --comparar-con plan-20301123-1722.json
```

- **Solo con la copia `dev`**, como la del latido. Usa `MODELO_AGENTE` y la clave de `jimic-dev`.
- **Qué hace:** cada pasada corre el agente de verdad (prompt, herramientas, guardia y opciones)
  en una carpeta temporal con los ficheros inventados, su propia base de datos y su propio
  `CLAUDE_CONFIG_DIR`, con fuentes fijas; pinta el plan con el mismo código que el servicio, sin
  enviarlo. No toca Todoist, Calendar ni Telegram (el escritor de Todoist se niega), ni la base de
  datos de la copia. Tres pasadas a la vez.
- **Cuánto cuesta:** unos 0,80 $ el plan (39 ejecuciones del agente) en la primera medida; con el
  cierre y la memoria, 63 ejecuciones, unos 1,5 $. Se para y se pregunta si pasara de 3 $.
- **El informe:** `~/.local/share/jimic-dev/evals/plan-AAAAMMDD-HHMM.json` y `.md`, con el plan,
  el cierre o la respuesta de cada pasada y las comprobaciones que fallan, con su tipo. Se compara,
  como el del latido, con el último informe **completo** (todos los escenarios del fichero; uno con
  `--escenario` nunca es la referencia del siguiente) o con el que se elige con `--comparar-con`
  (un nombre de la carpeta de informes o una ruta), que falla antes de gastar si no se puede leer.
- **El umbral** (13.3 del diseño): el 100 % de las pasadas cumplen todas sus comprobaciones; las
  reglas duras, al 100 %, y las de criterio, con ese umbral y revisadas en la 1.17. Un plan mínimo
  (el agente no pudo), un cierre con la nota fija o una respuesta fallida es una pasada fallida en
  las dos notas.

Cuándo lanzarla: al cambiar el prompt del agente o del plan, el modelo o el SDK, y al cerrar cada
hito. Si una comprobación parece mal, se pregunta: nunca se cambia para que cuadre con el modelo.
