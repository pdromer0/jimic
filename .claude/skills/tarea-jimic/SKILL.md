---
name: tarea-jimic
description: Lleva una tarea de JimiC desde su issue de GitHub hasta el pull request, siguiendo docs/proceso/tarea.md.
argument-hint: "N [notas efímeras de esta sesión]"
disable-model-invocation: true
---

# /tarea-jimic

Argumentos: `$ARGUMENTS`. El primer número es el issue N; el resto, lo efímero de esta sesión.
Si no hay número, para y pide N.

**Toda la tarea:** sigue [docs/proceso/tarea.md](../../../docs/proceso/tarea.md), que tiene las
reglas (GitHub solo con `scripts/gh.sh`, niveles de tests, git). Léelo entero ahora, junto con
los ficheros de `docs/proceso/pasos/` si existe. Esta skill solo añade lo propio de Claude Code.

## En modo plan

0. Si la sesión no está en modo plan, pide a Pedro que lo active (Shift+Tab) y espera:
   `ExitPlanMode` necesita el fichero de plan.
1. Arranque y plan según `tarea.md` §1 y §2. Al principio del plan: la rama y el nivel de
   `/code-review` (`medium`, salvo que un paso de módulo diga otro).
2. Presenta el plan.

## Con el plan aprobado

1. Anota la hora (`date '+%Y-%m-%d %H:%M'`).
2. `git switch main && git pull --ff-only`; después `git switch <rama>` o, si no existe,
   `git switch -c <rama>`.
3. Implementación, revisión, N3, anexo y PR según `tarea.md` §3 a §6. `/code-review` se lanza con
   la herramienta Skill (skill `code-review`, argumentos `<nivel> main...<rama>`). La N3, con
   `run_in_background`, mientras escribes el anexo y la descripción del PR.
4. Mensaje final: el enlace del PR y el recordatorio de `/clear`.

## Ajustes de la revisión

`tarea.md` §7.
