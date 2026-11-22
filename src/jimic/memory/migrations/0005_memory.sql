-- 0005: la memoria adaptativa (tarea 1.12).

-- Cada cambio de memoria guarda el parche exacto: en `position` (en caracteres) se quitó `before`
-- y se puso `after`. También guarda la huella del fichero entero antes y después: deshacer exige
-- que el fichero siga exactamente como lo dejó el cambio. Una reversión es un cambio más, con
-- `revert_of`; el cambio revertido gana `reverted_at`. Sin purga: se guarda siempre.
ALTER TABLE changes ADD COLUMN operation TEXT
    CHECK (operation IN ('add', 'replace', 'remove', 'undo'));
ALTER TABLE changes ADD COLUMN position INTEGER CHECK (position >= 0);
ALTER TABLE changes ADD COLUMN file_before_sha TEXT;
ALTER TABLE changes ADD COLUMN file_after_sha TEXT;
ALTER TABLE changes ADD COLUMN revert_of INTEGER;
ALTER TABLE changes ADD COLUMN approval_id INTEGER;

-- Un borrado que /deshacer recreó (con su ID nuevo), o que ya no se ofrece porque la tarea sigue
-- en Todoist (`restored_task_id` NULL).
ALTER TABLE deleted_tasks ADD COLUMN restored_at TEXT
    CHECK (restored_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00');
ALTER TABLE deleted_tasks ADD COLUMN restored_task_id TEXT;

-- Dos clases nuevas de aviso del sistema: un YAML personal que ya no vale (sigue la última versión
-- buena) y un fichero personal recortado en el prompt del agente. SQLite no cambia un CHECK: la
-- tabla se rehace con sus filas.
CREATE TABLE system_notices_new (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL
        CHECK (kind IN ('api_down', 'source_down', 'budget', 'config_invalid', 'memory_clipped')),
    period TEXT NOT NULL
        CHECK (period GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'
            OR period GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    detail TEXT NOT NULL DEFAULT '' CHECK (detail NOT GLOB '*[^a-zA-Z0-9_:,]*'),
    detected_at TEXT NOT NULL
        CHECK (detected_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'),
    sent_at TEXT
        CHECK (sent_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'),
    UNIQUE (kind, period)
) STRICT;
INSERT INTO system_notices_new (id, kind, period, detail, detected_at, sent_at)
    SELECT id, kind, period, detail, detected_at, sent_at FROM system_notices;
DROP TABLE system_notices;
ALTER TABLE system_notices_new RENAME TO system_notices;
