-- 0007: el plan del día y la reanudación tras los toques (tarea 1.14).

-- Una ejecución del agente sin mensaje de Pedro (el plan que pasa el latido, la reanudación tras
-- un toque) necesita la fila del día: guarda la sesión y la marca de contenido externo, y sin ella
-- la guardia deniega las lecturas. Esa fila no puede inventar que Pedro escribió, así que
-- `last_message_at` admite NULL («hoy aún no ha escrito»); `MAX()` ignora los NULL. SQLite no
-- cambia un NOT NULL: la tabla se rehace con sus filas.
CREATE TABLE conversations_new (
    day TEXT PRIMARY KEY CHECK (day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    session_id TEXT,
    last_message_at TEXT
        CHECK (last_message_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'),
    read_external INTEGER NOT NULL DEFAULT 0 CHECK (read_external IN (0, 1)),
    onboarding_suggested INTEGER NOT NULL DEFAULT 0 CHECK (onboarding_suggested IN (0, 1))
) STRICT;
INSERT INTO conversations_new (day, session_id, last_message_at, read_external,
                               onboarding_suggested)
    SELECT day, session_id, last_message_at, read_external, onboarding_suggested
    FROM conversations;
DROP TABLE conversations;
ALTER TABLE conversations_new RENAME TO conversations;
