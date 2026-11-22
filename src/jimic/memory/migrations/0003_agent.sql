-- 0003: lo que el latido pasa al agente y la conversación de cada día (tarea 1.10).
-- Ninguna de las dos guarda contenido: intenciones, identificadores y momentos.

-- Cada plan o cierre que el latido pasa al agente (decisión 1.8-b y el primer añadido de la
-- 1.10): cuenta como atendido hoy para el tope por intención, pero no en el cupo de mensajes, que
-- solo mira `notices`. Retención: 90 días, como `decisions`.
CREATE TABLE handovers (
    id INTEGER PRIMARY KEY,
    handed_at TEXT NOT NULL
        CHECK (handed_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'),
    intent TEXT NOT NULL
) STRICT;

-- La conversación de cada día local (decisión 1.10-a): el identificador de la sesión del SDK
-- (NULL hasta la primera respuesta), lo último que escribió Pedro (1.10-c) y si la sesión ya leyó
-- contenido externo (el calendario o las tareas), para la regla de origen de la 1.12.
-- Retención: 7 días, como las transcripciones del agente (decisión 23).
CREATE TABLE conversations (
    day TEXT PRIMARY KEY CHECK (day GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
    session_id TEXT,
    last_message_at TEXT NOT NULL
        CHECK (last_message_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00'),
    read_external INTEGER NOT NULL DEFAULT 0 CHECK (read_external IN (0, 1))
) STRICT;
