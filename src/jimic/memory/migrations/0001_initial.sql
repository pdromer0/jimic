-- 0001: las ocho tablas del hito 1 (7.3 del diseño y «SQLite (H1-16)» de la especificación).
-- Momentos: texto ISO 8601 en UTC al segundo (jimic.clock.to_db). Días: 'YYYY-MM-DD' locales.
-- Sin IF NOT EXISTS: si esta migración se repitiera, debe fallar a la vista.

-- Cada decisión del latido. Retención: 90 días.
CREATE TABLE decisions (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    speak INTEGER NOT NULL CHECK (speak IN (0, 1)),
    intent TEXT,
    reason TEXT NOT NULL
) STRICT;

-- Cada aviso y su desenlace. Retención: 1 año.
CREATE TABLE notices (
    id INTEGER PRIMARY KEY,
    sent_at TEXT NOT NULL,
    intent TEXT NOT NULL,
    telegram_message_id INTEGER,
    outcome TEXT NOT NULL DEFAULT 'pending'
        CHECK (outcome IN ('pending', 'answered', 'ignored', 'button')),
    button TEXT,
    outcome_at TEXT
) STRICT;

-- Acciones pendientes de botón, con caducidad. Retención: 30 días.
CREATE TABLE approvals (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL CHECK (json_valid(payload)),
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'approved', 'rejected', 'expired')),
    resolved_at TEXT
) STRICT;

-- Cada cambio de memoria. Retención: siempre.
CREATE TABLE changes (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    file TEXT NOT NULL,
    before TEXT,
    after TEXT,
    source_message TEXT NOT NULL,
    reverted_at TEXT
) STRICT;

-- El plan confirmado de cada día. Retención: 1 año.
CREATE TABLE day_plans (
    day TEXT PRIMARY KEY,
    confirmed_at TEXT NOT NULL,
    plan TEXT NOT NULL CHECK (json_valid(plan))
) STRICT;

-- La valoración del día, del 1 al 5. Retención: 1 año.
CREATE TABLE day_ratings (
    day TEXT PRIMARY KEY,
    rating INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    rated_at TEXT NOT NULL
) STRICT;

-- Cada tarea del plan confirmado (decisiones 62 y 68). Retención: 1 año.
CREATE TABLE plan_items (
    id INTEGER PRIMARY KEY,
    day TEXT NOT NULL REFERENCES day_plans (day) ON DELETE CASCADE,
    todoist_task_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('done', 'will_do', 'pending', 'moved')),
    scheduled_at TEXT,
    marked_at TEXT,
    marked_early INTEGER NOT NULL DEFAULT 0 CHECK (marked_early IN (0, 1))
) STRICT;

-- La foto de cada tarea borrada, para /deshacer (decisión 56, H1-2). Retención: 30 días.
-- approval_id sin clave ajena: approvals se purga a su propio ritmo.
CREATE TABLE deleted_tasks (
    id INTEGER PRIMARY KEY,
    deleted_at TEXT NOT NULL,
    todoist_task_id TEXT NOT NULL,
    content TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    due TEXT,
    duration TEXT,
    project_id TEXT NOT NULL,
    labels TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(labels)),
    priority INTEGER NOT NULL,
    reason TEXT NOT NULL,
    approval_id INTEGER
) STRICT;
