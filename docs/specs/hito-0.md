# Hito 0 — Entorno

> **Estado:** cerrado (v0.0-hito0).

Al terminar, JimiC tiene la máquina lista, el paquete con su CLI y sus tests, el CI con
`main` protegida, los agentes con sus instrucciones, GitHub preparado y una tarea hecha de punta a
punta con el método EDsim (núcleo, sin módulos). El detalle de cada fase está en la guía del
método; aquí queda lo que pasó.

## Tareas

| Fase | Contenido | PR |
| --- | --- | --- |
| 0 y 1 | Preguntas y máquina (chat) | — |
| 2 | Repositorio, paquete y calidad local | Commit inicial en `main` (102abff) |
| 3 | CI y ruleset | #1 |
| 4 | Agentes: instrucciones, Claude Code y documentación | #2 |
| 5 | GitHub: etiquetas, milestones y plantillas | #3 |
| 6 | Primera tarea de punta a punta: 6a, prueba en seco reducida (#4); 6b, `jimic doctor` (#5) | #7 |
| 7 | Cierre del hito 0 (#6): ADR 0001 a 0004, balance, diario, lecciones y portada | #8 |

## Criterios de aceptación

Los de la lista de aceptación del núcleo del método, y «Pedro sabe explicar» WSL, uv, ruff,
pytest y su límite de tiempo, pre-commit, GitHub Flow, Actions, el ruleset, el modo plan, los
hooks y los niveles de tests.

## Balance del hito

**Qué se entregó.** El núcleo del método EDsim v2
(ADR [0001](../decisiones/0001-adoptamos-el-metodo-edsim.md)) con sus tres desviaciones
([0002](../decisiones/0002-repositorio-publico.md), [0003](../decisiones/0003-pyright.md) y
[0004](../decisiones/0004-gitleaks.md)), y `jimic doctor` como primera tarea de punta a punta.
Las fases 0 y 1, en el chat (17 y 18-nov-2026); de la 2 a la 6b, el 18-nov-2026, del primer
commit (102abff, 11:26) a la fusión de la 6b (40685fe, 14:05): **2 h 39 min**. El detalle de
cada fase está en [arranque.md](../proceso/arranque.md).

| Fase | Qué quedó | Dónde | PR (*squash*) | Commits de rama | Desde la fusión anterior | Abierto → fusión |
| --- | --- | --- | --- | --- | --- | --- |
| 2 | Paquete con su CLI, tests de humo, pre-commit (ruff, pyright y gitleaks) y `n3.sh` | `pyproject.toml`, `.pre-commit-config.yaml`, `scripts/` | — (102abff) | 1 | — | — |
| 3 | CI de un job, ruleset, CodeQL y ajustes de GitHub | `.github/workflows/ci.yml` | #1 (4948329) | 4 | 23 min | 9 min |
| 4 | Instrucciones, hooks, skills, documentación, `SECURITY.md` y `gitleaks-historia` | `AGENTS.md`, `CLAUDE.md`, `.claude/`, `docs/` | #2 (04066d4) | 5 | 92 min | 9 min |
| 5 | Plantillas, etiquetas y milestones | `.github/ISSUE_TEMPLATE/`, `.github/pull_request_template.md` | #3 (fc29a79) | 4 | 15 min | 4 min |
| 6a | Prueba en seco reducida | Issue #4 y `arranque.md` | — | — | — | — |
| 6b | `jimic doctor` | `src/jimic/doctor.py`, `tests/test_doctor.py` | #7 (40685fe) | 7 | 28 min | 4 min |
| 7 | ADR, balance, diario, lecciones y portada | `docs/`, `AGENTS.md`, `README.md` | #8 | Ver el anexo | — | — |

Cifras de un script sobre `git log` (fecha del commit, +01:00) y la API de GitHub (PR cerrados y
sus commits), el 18-nov-2026. Los 7 commits del PR #7 son los 6 del anexo más el del propio anexo
(abb502c). Solo el PR #1 recibió commits después de abrirse (3): la comprobación deliberada del CI,
en rojo y en verde. N3 en verde: 8 ejecuciones, entre 0 y 6 s; la de 0 s es el verde que no
probaba nada de la fase 2.

**Disparadores** (`scripts/disparadores.sh --github`, 18-nov-2026; las diez preguntas que imprime
después, respondidas en la tabla siguiente):

```text
== Disparadores cumplidos (módulos inactivos)
ninguno
== Datos
rápida en verde: mediana 0 s
última N3 en verde: 4 s
tests: 5
issues abiertos: 1
PR de solo documentación en los 20 últimos: 0; CI de un PR: mediana 19 s
```

**Respuestas a las preguntas del cierre.**

| Módulo | Respuesta |
| --- | --- |
| `mutante` | No: la 6b tuvo 2 versiones incorrectas y el árbol quedó limpio |
| `revision-reforzada` | No: no ha llegado a `main` ningún bug |
| `claude-en-pr` | No: ningún PR necesitó ajustes tras abrirse (los 3 commits del #1 son la comprobación deliberada) |
| `adr-y-releases` | **Sí**: las decisiones 43, 46 y 49 son difíciles de revertir, y el H1 trae más (12.4 del diseño) |
| `metricas-pr` | **Sí**: empieza el hito 1 |
| `guardia` | No: las escrituras en GitHub pasan por `scripts/gh.sh` y piden permiso |
| `dobles-con-tope` | No: aún no hay dobles; la 6b solo usa `monkeypatch` |
| `comodidad-local` | No (respuesta de Pedro) |
| `migracion-docs` | No: lo que queda en Drive es privado a propósito (12.4 del diseño) |
| `diagnostico` | No: las instrucciones se instalaron en la fase 4 (#2) y no cambiaron después; vigilar en el H1 |

**Propuestas, sin implantar.** Cada una será una tarea de proceso (serie P), un módulo por tarea:

- **`tablero`**: tarea de proceso **P1**, al planificar el H1, por decisión de Pedro. Su disparador
  (más de 10 issues abiertos, o dos PR abiertos a la vez) se cumplirá al crear los issues del H1.
- **`adr-y-releases`**: se propone activarlo; las ADR candidatas del producto están en la sección
  12.4 del diseño.
- **`metricas-pr`** y **`revisor-plan`**: entran al empezar el H1 (decisión 47 y 12.4 del diseño).
  El de `revisor-plan` se cumple con la primera tarea `critica`.

**Lista de aceptación del núcleo** (sección 10 de la guía del método), repasada con Pedro:

- [x] `wsl -l -v` con VERSION 2; `.wslconfig` con `memory`; Cursor abre `~/proyectos/jimic` en
  WSL. Fase 1 de `arranque.md`; Cursor, confirmado por Pedro.
- [x] `gh auth status`, `uv --version`, `claude --version` y `claude doctor` responden. Fase 1;
  `claude doctor`, confirmado por Pedro.
- [x] `uv run jimic --version` funciona (`jimic 0.1.0`); los tests pasan (`5 passed`, dos de
  humo); cada test tiene límite de tiempo (`pytest-timeout`, 60 s).
- [x] pre-commit paró un commit mal formateado; ruff tiene una sola versión (la de `uv.lock`).
  Comprobación deliberada de la fase 2, que se paró solo en `ruff format`; vista por Pedro y por el
  chat, registrada en las incidencias de la fase 2.
- [x] El CI se ejecuta en cada PR, mostró un rojo y un verde (fase 3, PR #1), y `tests` es el
  check obligatorio de `main`.
- [x] Un push directo a `main` es rechazado (GH013, comprobado por Pedro; tabla de la fase 3); solo
  *squash*; las ramas se borran solas.
- [x] `AGENTS.md` (69 líneas) y `CLAUDE.md` (16), de menos de 80 líneas cada uno.
- [x] En una sesión nueva: tres hooks, estilo `Explanatory`, `gh pr merge` denegado; el `Stop`
  probado a mano (fase 4; confirmado por Pedro).
- [x] `docs/` con índice, diseño, hito 0, ADR 0001, diario, lecciones, `tarea.md`, `modulos.md` y
  `arranque.md`; enlaces sin romper (script del anexo, tarea 7).
- [x] Etiquetas, milestones y plantillas en `main` (fase 5, PR #3).
- [x] La prueba en seco paró donde debía, **reducida** (#4: solo `/tarea-jimic` con la dependencia
  abierta); la 6b recorrió el ciclo hasta `/sincronizar` (no quedan ni la rama local ni la remota).
- [x] `scripts/disparadores.sh --github`, en este balance.
- [x] La etiqueta `v0.0-hito0`, creada por Pedro tras la fusión (apunta a 0ca2ef3, la fusión del
  PR #8; comprobado el 18-nov-2026).
- [x] Pedro sabe explicar WSL, uv, ruff, pytest y su límite, pre-commit, GitHub Flow, Actions, el
  ruleset, el modo plan, los hooks y los niveles de tests: confirmado el 18-nov-2026 con el repaso
  del chat (11 de 11), con la guía del [diario](../diario/hito-0.md#pedro-sabe-explicar).

**Pendiente para después del hito.**

- Decidir si `jimic` sin subcomando imprime la ayuda, cuando la CLI tenga una segunda orden
  (anexo de la 6b). Destino: el backlog, si Pedro lo quiere.
- El *deny* de lectura de la carpeta de los ficheros personales en `.claude/settings.json`, cuando
  el H1 la fije (`arranque.md`, fase 4, punto 5). Destino: la tarea 1.2 o la 1.3.
- Las pruebas omitidas de la prueba en seco: con el número de un PR, `/sincronizar` con un PR
  abierto y el formulario web de las plantillas. Destino: ninguno, salvo que fallen en uso.

## Anexo: guía de referencia del hito 0

### Tarea 6b: la orden `jimic doctor`

**Qué se hizo.** Issue #5, rama `feat/h0-doctor`, PR #7. Commits: `feat(doctor)` (2a754cc),
`feat(cli)` (1bd8fd5), `docs(arranque)` (3b1c7d9), y los de la revisión: `test(doctor)`
(df01260), `refactor(doctor)` (ac2dca5) y `docs(arranque)` (7a43c99). La 6a (#4), la prueba en
seco reducida, no lleva rama. Está en la fase 6 de [arranque.md](../proceso/arranque.md).

**Decisiones del diff.**
- La lógica vive en `src/jimic/doctor.py`: `Diagnosis` (dataclass congelado), `diagnose()` y
  `render()`. La CLI solo imprime.
- `.env` se comprueba con `Path.is_file()` (`stat`) y nunca se abre. Un directorio o un enlace roto
  cuentan como «no existe».
- `platform.python_version` se llama a través del módulo para que el monkeypatch llegue al código.
- Sale con 0 siempre. `jimic` a secas sigue sin imprimir nada.

**Valores a mano** (ejecutados con `uv run python -`, sin el paquete):
- La versión real es `3.14.10`.
- Sin `.env`, `is_file` da `False`. Con un `.env` en `chmod 000`, da `True`.
- Con `builtins.open`, `io.open` y `os.open` prohibidos, `is_file` sigue dando `True`, y un
  `read_text` sí cae en el parche.
- `.env` como directorio o como enlace roto da `False`.
- Los tests fijan la versión a `9.9.9`, que no existe. Al principio era `3.14.0`; la revisión hizo
  ver que podía coincidir con un intérprete real.

**Medidas.** N3: en verde en 0:04 (pre-commit 0:03, pytest 0:00 con `5 passed`, gitleaks-historia 0:01 con 10 commits y ~239 KB). Los 3 tests de `test_doctor.py` tardan unos 0,1 s.

**Versiones incorrectas** (predicción frente a observado, repetidas sobre el código final):
- A, invertir la comprobación de `.env`: predije los 3 tests de `test_doctor.py` en rojo y el
  humo en verde. Observado: `3 failed, 2 passed`.
- B, `from platform import python_version`: predije en rojo «con .env» y «sin .env», con
  `3.14.10` frente a `9.9.9`. Observado: `2 failed, 3 passed`.

**Revisión** (`/code-review medium`). 8 hallazgos:
- Corregidos:
  - La versión fijada podía coincidir con la real.
  - La fixture tenía el nombre en español.
  - `diagnose(directory)` dejaba a `render` decir «carpeta actual» de otra carpeta: se quita el
    parámetro.
  - La redacción ambigua de «versiones».
  - La fila 5 seguía dando por pendiente el [COMPROBAR] omitido.
- Explicados:
  - El anexo aún vacío: se escribía durante la N3.
  - `.env` como directorio o como enlace roto: es una interpretación del plan.
  - `jimic` sin subcomando: queda fuera del issue.

**Pendiente.** Decidir si `jimic` sin subcomando imprime la ayuda, cuando la CLI tenga una
segunda orden. Destino: el backlog, si Pedro lo quiere.

### Tarea 7: cierre del hito 0

**Qué se hizo.** Issue #6, rama `docs/h0-cierre`, PR #8. Commits, uno por bloque: `docs(decisiones)`
(9df1f2e), `docs(hito-0)` (4597ab1), `docs(diseno)` (95bf287), `docs(diario)` (194a347),
`docs(lecciones)` (5000e90) y `docs(portada)` (14ad1e1). Los de la revisión: `docs(diseno)`
(d1d371e), `docs` (a5a6358), `docs(hito-0)` (e9863e1 y f6ee89b), `docs(diario)` (2bb8e1a) y
`docs(portada)` (d197ce5). El mensaje de e9863e1 dice que corta la última línea larga del balance;
la cortó f6ee89b.

**Decisiones del diff.**
- Los ADR 0002 a 0004 se numeran en orden, como dicen el issue, `arranque.md` y el diseño (respuesta
  de Pedro); el README de `decisiones/` aclara que el número es el de la columna ADR del registro.
- Los ADR llevan la fecha de la decisión (17-nov-2026), no la del cierre.
- El registro no gana filas: las decisiones del hito son la 41 a la 54, de la fase 0. La prueba en
  seco reducida es una decisión de proceso y queda en `arranque.md` y en el ADR 0001.
- `tablero` entra en las propuestas como P1 del H1, por decisión de Pedro al revisar el plan.
- Dos pruebas de la lista de aceptación sin registro escrito (el formato parado en la fase 2 y el
  GH013 de la fase 3) se anotan en `arranque.md` con las notas de Pedro.
- «Pedro sabe explicar» quedó sin marcar hasta la revisión del PR; Pedro lo confirmó el
  18-nov-2026 con el repaso del chat (11 de 11).
- `README.md`, vacío desde `uv init`, gana una portada mínima con la línea de la licencia.

**Valores a mano** (un script del scratchpad sobre `git log` y la API de GitHub, con lectura por
`gh api` fuera de `gh.sh`, que no da las horas de apertura; permiso de Pedro en el plan):
- Fusiones en `main`: 102abff 11:26, 4948329 11:50, 04066d4 13:22, fc29a79 13:37 y 40685fe 14:05;
  del primer commit a la fusión de la 6b, 2:39:28.
- PR: #1 (4 commits, 9 min abierto), #2 (5, 9 min), #3 (4, 4 min) y #7 (7, 4 min); solo el #1 tiene
  commits posteriores a su apertura (3).
- `AGENTS.md`, 69 líneas; `CLAUDE.md`, 16. `uv run jimic --version` da `jimic 0.1.0`; `pytest`,
  `5 passed`.
- `uv run --frozen pyright` tarda 4,75 s (para el ADR 0003).

**Medidas.** N3: en verde en 0:05 (pre-commit 0:04, pytest 0:01 con `5 passed`,
gitleaks-historia 0:00 con 17 commits y ~278 KB, sin fugas), antes de este commit del anexo.
`disparadores.sh --github` tardó más de 2 minutos la segunda vez (la primera, segundos): las
llamadas a la API iban lentas; la salida fue idéntica.

**Versiones incorrectas** (no hay código; se prueban las comprobaciones):
- Script de enlaces relativos (fichero y ancla) sobre todos los `.md` de git: predije 0 rotos;
  observado, 0. Con un fichero y un ancla rotos a propósito en `README.md`: predije 2; observado,
  `rotos: 2`; deshecho, 0.
- `session-start.sh` con el `AGENTS.md` nuevo: predije la línea nueva del hito; observado, la
  línea, no «no encontrado».

**Revisión** (`/code-review medium`). 7 hallazgos:
- Corregidos:
  - Las decisiones 50, 52 y 53 no enlazaban el ADR 0002 en el registro.
  - La etiqueta y los disparadores compartían un punto de la lista.
  - El diario fechaba las fases 0 y 1 solo el 17-nov.
  - Líneas nuevas de más de 100 columnas.
  - El enlace del hito en curso, sin el ancla de 11.1.
- Explicados:
  - La cabecera y el README dan la etiqueta por hecha: la cabecera `cerrado (v0.N-hitoN)` es el
    formato de `/cerrar-hito`, y el punto sin marcar de la lista vigila que Pedro la cree.
  - El formato y el GH013, anotados a posteriori: es el testimonio de Pedro, con sus notas, que
    pidió registrarlo; no sale de la memoria del agente.

**Pendiente.** La etiqueta `v0.0-hito0`, de Pedro. Las propuestas del balance, como tareas de
proceso al planificar el H1.

**Ajustes de la revisión** (Pedro, en el PR #8, solo documentación):
- Una cuarta lección en `lecciones.md`: lo que se ajusta en la web de GitHub se comprueba por la
  API (fases 3 y 4).
- «Pedro sabe explicar», marcado: confirmado el 18-nov-2026 con el repaso del chat (11 de 11).
