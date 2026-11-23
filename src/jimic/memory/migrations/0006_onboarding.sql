-- 0006: la sugerencia de la puesta en marcha (tarea 1.13, decisión 1.13-b).

-- Si JimiC ya sugirió hoy la puesta en marcha (o Pedro la lanzó): como mucho una vez por día
-- local, también tras un reinicio. Es un bit de la fila del día, sin texto; se purga con ella, a
-- los 7 días. El progreso de la puesta en marcha no se guarda: sale de los propios ficheros.
ALTER TABLE conversations ADD COLUMN onboarding_suggested INTEGER NOT NULL DEFAULT 0
    CHECK (onboarding_suggested IN (0, 1));
