# 0003. Comprobación de tipos con pyright

- **Estado:** Aceptada
- **Fecha:** 2026-11-17

## Contexto

El núcleo del método trae ruff y pytest, sin comprobación de tipos. En JimiC, las ramas que casi
nunca se ejecutan (la API caída, APEX sin datos) son las que garantizan la degradación, y un test
no siempre las cubre. Criterio de empresa (decisión 41). Desviación del núcleo: **añade** una
pieza (ADR [0001](0001-adoptamos-el-metodo-edsim.md)).

## Decisión

**pyright** en modo estándar (decisión 46), con `reportUnnecessaryTypeIgnoreComment = "error"`, en
la N1 (hook local de pre-commit, `uv run --frozen pyright`), la N3 y el CI. Cada silencio
(`# type: ignore` o `Any`) lleva el código del error y su motivo, y pyright avisa de los que sobran
(regla en `docs/proceso/tarea.md`). Las librerías sin tipos (`google-api-python-client`) quedan
encerradas en las herramientas propias, que declaran lo que devuelven.

## Alternativas consideradas

- Pyrefly: estable y rápido, pero menos extendido y algo menos completo. Tendría sentido si pyright
  se volviera lento con el tamaño del código.
- mypy: el más lento y el que menos cubre. Tendría sentido si una librería clave solo trajera un
  plugin de mypy.
- ty: en beta. Tendría sentido cuando sea estable.
- Ninguno (lo que trae el núcleo): los errores de tipos llegarían tarde, por un test o en
  producción.

## Consecuencias

- Avisa al agente antes que los tests y hace seguros los cambios grandes.
- Cada silencio queda justificado y a la vista.
- Un hook más en cada commit: `uv run --frozen pyright` tarda unos 5 s (4,75 s medidos el
  18-nov-2026, con el código de la 6b), sobre todo el arranque de Node.

## Qué cambió al implementarla

- Fase 2 (102abff): `pyright[nodejs]` en lugar de `pyright`. La rueda de PyPI no trae Node.js y,
  sin `node` en el `PATH`, lo descargaba con `nodeenv` en la primera ejecución, fuera de
  `uv.lock`; con el extra, `nodejs-wheel-binaries` queda fijado y es el mismo en local y en el CI
  (`arranque.md`, incidencias de la fase 2).
- Fase 4 (PR #2, 04066d4): `Bash(uv run pyright*)` en el `allow` de `.claude/settings.json` y la
  regla de los silencios en `tarea.md`.
