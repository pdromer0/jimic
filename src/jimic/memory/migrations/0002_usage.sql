-- 0002: el contador de gasto y el registro de los avisos del sistema (tarea 1.8).
-- Ninguna de las dos guarda contenido: ni texto de mensajes ni de prompts.

-- Una fila por llamada al modelo: tokens y dólares. Retención: 1 año.
CREATE TABLE model_usage (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL CHECK (day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
    output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
    cache_read_tokens INTEGER NOT NULL CHECK (cache_read_tokens >= 0),
    cache_write_tokens INTEGER NOT NULL CHECK (cache_write_tokens >= 0),
    usd REAL NOT NULL CHECK (usd >= 0)
) STRICT;

-- Los avisos del sistema (decisión 1.8-c): como mucho uno por tipo y periodo (el día local, o el
-- mes para el presupuesto). Sin texto: el tipo, códigos (`calendar:auth`,
-- `apex:unexpected:KeyError`) y los momentos.
-- `sent_at` es NULL mientras espera a que acabe el silencio. Retención: 1 año.
CREATE TABLE system_notices (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('api_down', 'source_down', 'budget')),
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
