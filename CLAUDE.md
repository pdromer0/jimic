@AGENTS.md

## Específico de Claude Code

- Modo inicial `plan` y estilo `Explanatory` (`.claude/settings.json`).
- Hooks: `PostToolUse` formatea con ruff cada `.py` editado; `Stop` ejecuta la N2
  (`.claude/hooks/stop.sh`) y no hace nada en modo plan; `SessionStart` da rama, etiqueta, hito
  en curso, estado y los disparadores cumplidos de los módulos inactivos. Sin `jq`, salen con 1.
- Skills, solo a mano: `/tarea-jimic N` (issue → PR), `/sincronizar N` (tras la fusión, N es el PR)
  y `/cerrar-hito N` (issue de cierre).
- Con el plan aprobado haces `git push -u origin <rama>` de tu rama y abres el PR con
  `scripts/gh.sh abrir-pr` sin pedir confirmación: aprobar el plan cuenta como ese permiso.
  El push forzado, el push a `main` y la fusión están denegados en `.claude/settings.json`.
- GitHub, solo con `scripts/gh.sh`; cualquier otro `gh` pide permiso.
- Las especificaciones grandes se leen por secciones (`grep -n '^#'` y rangos de líneas).
- Al abrir el PR, recuerda a Pedro que haga `/clear` antes de la siguiente tarea.
