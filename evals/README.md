# Evaluaciones

Las evaluaciones miden el **juicio del modelo**, que no es determinista: dan un porcentaje, no un
verde o un rojo (13.1 y 13.2 del diseño, capa 3). Se lanzan a mano y **nunca** corren en el CI ni
en `uv run pytest`. Los tests de `tests/test_evaluaciones.py` sí corren siempre: comprueban el
fichero de escenarios y la corrección, con un doble del modelo y sin gastar.

**Todo lo de esta carpeta es inventado** (decisión 43). Los casos sacados de días reales irán, más
adelante, en una carpeta fuera del repositorio con el mismo formato.

## El latido

`latido.yaml` tiene un día base (jueves 10-ene-2030) y 40 escenarios: 39 evaluables y el e02,
pendiente de la tarea 1.15. Cada uno dice la hora, lo que cambia sobre el día base y los conjuntos
de intenciones aceptables (`[]` es callar). El formato está en la cabecera del fichero.

```bash
JIMIC_ENTORNO=dev uv run jimic evaluar latido                    # todo, 3 pasadas
JIMIC_ENTORNO=dev uv run jimic evaluar latido --escenario l08 --escenario l10 --pasadas 1
```

- **Solo con la copia `dev`**: con `prod` se niega antes de leer el `.env`. Usa
  `MODELO_LATIDO` y la clave de `jimic-dev`, y se lanza desde la raíz del repositorio.
- **Qué hace:** monta el estado de cada escenario con el código del latido, llama a la API real
  (4 llamadas a la vez) y corrige lo que decide Haiku. No toca Todoist, Calendar ni Telegram, ni
  escribe en la base de datos.
- **Cuánto cuesta:** unos 0,03 $ la evaluación completa (117 llamadas a Haiku), según el
  informe.
- **El informe:** el resumen sale por pantalla y el completo queda en
  `~/.local/share/jimic-dev/evals/latido-AAAAMMDD-HHMM.json` y `.md`, con cada pasada, su motivo
  y sus mensajes. Se compara con el informe anterior escenario a escenario, solo sobre los que no
  han cambiado, y se avisa de lo que empeore más de 5 puntos.
- **El umbral** (13.3 del diseño): ≥ 90 % de acierto y ≤ 5 % de «habla cuando no debía»,
  contado solo sobre los escenarios cuyo único resultado aceptable es callar. Es el umbral para
  cerrar el hito.

Cuándo lanzarla: al cambiar el prompt del latido, el modelo o el SDK, y al cerrar cada hito.
