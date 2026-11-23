-- 0009: las tareas clave (tarea 1.15). Sin texto de Pedro: días, momentos, ids y una marca.

-- Lo que el código sabe de cada tarea clave de un día local. `going`: [Voy ahora] (la pausa
-- cuenta desde `marked_at`); `done`: [Ya entrené] o [Hecho]; `not_today`: [Hoy no]; `seen`: el
-- latido vio abierta la tarea de hoy de Todoist con ese id (el salto de la decisión 1.15-j, una
-- fila por tarea y día). [Deshacer] borra la fila. Retención: 1 año (la revisión semanal).
CREATE TABLE key_task_marks (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL CHECK (day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    intent TEXT NOT NULL,
    mark TEXT NOT NULL CHECK (mark IN ('going', 'done', 'not_today', 'seen')),
    marked_at TEXT NOT NULL
        CHECK (marked_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'),
    todoist_task_id TEXT
) STRICT;
CREATE INDEX key_task_marks_by_day ON key_task_marks (day);
CREATE UNIQUE INDEX key_task_marks_seen ON key_task_marks (day, intent, todoist_task_id)
    WHERE mark = 'seen';

-- Las tareas que JimiC movió cada día local (tras un botón o un mensaje de Pedro, o un
-- [Deshacer]): un movimiento suyo no es el salto de una tarea clave. Retención: 30 días.
CREATE TABLE task_moves (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL CHECK (day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    todoist_task_id TEXT NOT NULL,
    moved_at TEXT NOT NULL
        CHECK (moved_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00')
) STRICT;
CREATE INDEX task_moves_by_day ON task_moves (day);
