-- 0010: el cierre del día (tarea 1.16). Sin texto de Pedro: cuentas, momentos e ids.

-- La cifra definitiva de «plan cumplido» (H1-14, decisión 1.16-b): `planned` tareas en el plan y
-- `fulfilled` marcadas en Todoist, calculadas la mañana siguiente (`settled_at`). NULL mientras
-- tanto. SQLite admite añadir columnas con su CHECK a una tabla STRICT.
ALTER TABLE day_plans ADD COLUMN planned INTEGER CHECK (planned >= 0);
ALTER TABLE day_plans ADD COLUMN fulfilled INTEGER CHECK (fulfilled >= 0 AND fulfilled <= planned);
ALTER TABLE day_plans ADD COLUMN settled_at TEXT
    CHECK (settled_at GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]+00:00');

-- Una fila por tarea y día: el plan enviado dos veces, o creado después con sus botones, no
-- duplica la tarea.
CREATE UNIQUE INDEX plan_items_by_task ON plan_items (day, todoist_task_id);
