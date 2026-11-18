---
name: sincronizar
description: Tras la fusión de un PR de JimiC, comprueba que está fusionado, actualiza main, borra la rama local y avisa de lo pendiente.
argument-hint: "N del PR"
disable-model-invocation: true
---

# /sincronizar

Argumentos: `$ARGUMENTS`. N es el número del **PR** (`/tarea-jimic` lleva el del issue). Si no hay
número y estás en una rama, pregúntalo a Pedro.

1. Ejecuta `scripts/sincronizar.sh N`. Hace todas las comprobaciones y nunca hace push.
2. Según su código de salida:
   - 0: resume su salida. Si dice `DEPENDENCIAS`, pide permiso y ejecuta `uv sync`. Si un issue
     que cierra el PR sigue abierto, avisa; lo decide Pedro.
   - 2 (árbol sucio), 3 (sin fusionar), 4 (la fusión no está en main) o 5 (la rama tiene commits
     que no entraron): para y enseña el mensaje. No intentes arreglarlo.
3. Si el script avisa de que arrancaste en otra rama, recuérdaselo a Pedro.
