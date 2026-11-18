# Diario del hito 0 — Entorno

Lo escribe el agente al cerrar el hito, con lo que dejaron
[arranque.md](../proceso/arranque.md) y el anexo de [hito-0.md](../specs/hito-0.md). Las
reflexiones, al final, son de Pedro.

## 2026-11-17 · Fases 0 y 1 — Preguntas y máquina

**Qué se hizo:** en el chat, el 17-nov-2026 (la comprobación de la máquina terminó el 18), las
respuestas de la fase 0 (decisiones 41 a 54 del diseño) y la comprobación de la máquina: WSL2,
`.wslconfig` con 7 GB, git, jq, gh, uv y Claude Code.

**Qué aprendí:** las desviaciones del núcleo **añaden** piezas (repositorio público, pyright,
gitleaks) y cada una lleva su ADR; ninguna toca las nueve decisiones de fondo.

**Qué costó o falló:**
- Cursor activaba el entorno de EDsim (`(edsim)`), porque la ventana era la de ese proyecto.
  Solución: una ventana propia para JimiC.
- Docker Desktop comparte el techo de 7 GB de WSL2. Solución: pausar la pila de EDsim mientras se
  trabaja en JimiC.
- `pull.ff` y `fetch.prune` no estaban configurados. Solución: los comandos de la sección 2.2 del
  núcleo.

**Preguntas abiertas:** ninguna.

**Enlaces:** [arranque.md](../proceso/arranque.md), fases 0 y 1.

## 2026-11-18 · Fase 2 — Repositorio, paquete y calidad local

**Qué se hizo:** `uv init --package` en la carpeta ya creada, Python 3.14, ruff, pytest con
`pytest-timeout`, pre-commit con pyright y gitleaks, `scripts/n3.sh` y el repositorio público.

**Qué aprendí:** una comprobación deliberada solo vale si el fallo cae dentro de lo que la
herramienta revisa y para el hook esperado. Una herramienta de PyPI puede descargar cosas fuera de
`uv.lock` en su primera ejecución.

**Qué costó o falló:**
- `pyright` de PyPI no trae Node.js y lo bajaba con `nodeenv`, fuera del lock. Solución:
  `pyright[nodejs]`.
- La primera vez, pre-commit compila gitleaks con Go: unos 7 minutos.
- La N3 antes del primer `git add` salió en verde sin revisar nada (0 s). Causa: `--all-files`
  solo ve los ficheros de git.

**Preguntas abiertas:** ninguna.

**Enlaces:** commit 102abff; ADR [0003](../decisiones/0003-pyright.md) y
[0004](../decisiones/0004-gitleaks.md).

## 2026-11-18 · Fase 3 — CI y protección de `main`

**Qué se hizo:** un job de CI con pre-commit y pytest, las acciones fijadas por SHA, una caché de
pre-commit, el ruleset «Proteger main», CodeQL y los ajustes de seguridad de GitHub.

**Qué aprendí:** el CI repite la N3 en una máquina limpia y es el check obligatorio; un fallo
deliberado lo pone en rojo con pre-commit en verde. Los ajustes de GitHub se comprueban leyéndolos
por la API, no se dan por puestos.

**Qué costó o falló:**
- Sin caché, el CI compilaba gitleaks en cada ejecución (31 s de pre-commit). Solución:
  `actions/cache` con la clave del hash de `.pre-commit-config.yaml` (1 s).
- La aprobación de los PR de fuera estaba en el valor por defecto. Solución: cambiada por la API,
  con permiso.

**Preguntas abiertas:** ninguna.

**Enlaces:** PR #1.

## 2026-11-18 · Fase 4 — Instrucciones, Claude Code y documentación

**Qué se hizo:** el núcleo instalado (`AGENTS.md`, `CLAUDE.md`, hooks, skills y `docs/`),
`diseno.md` publicable, las reglas de JimiC en `tarea.md`, `SECURITY.md` y `gitleaks-historia`.

**Qué aprendí:** un hook en verde no dice qué revisó. En la N3 y el CI se mira cuánto revisó, no
solo el «Passed».

**Qué costó o falló:** gitleaks no revisaba nada en la N3 ni en el CI. Causa: el hook oficial usa
`--staged`, y allí no hay nada preparado. Solución: un segundo hook, `gitleaks-historia`, que
revisa toda la historia con `--verbose`.

**Preguntas abiertas:** ninguna.

**Enlaces:** PR #2.

## 2026-11-18 · Fase 5 — GitHub como gestor del trabajo

**Qué se hizo:** las plantillas de issues y de PR con el aviso «Nada personal», `config.yml` sin
issues en blanco y con enlace al informe privado, y las etiquetas y milestones de
`github-inicial.sh`.

**Qué aprendí:** las plantillas solo actúan en la web; un issue o un PR creados por la API no las
aplican.

**Qué costó o falló:** fuera de un terminal, `gh label create` no imprime nada. Solución: comprobar
las etiquetas leyéndolas por la API.

**Preguntas abiertas:** ninguna.

**Enlaces:** PR #3.

## 2026-11-18 · Tarea 6a — Prueba en seco reducida

**Qué se hizo:** solo `/tarea-jimic` sobre la 6b con la 6a abierta. Se paró tras
`scripts/gh.sh bloqueado-por 5` y nombró la #4, sin plan, rama ni commits.

**Qué aprendí:** las dependencias nativas de GitHub son la única fuente de los bloqueos; el cuerpo
del issue solo guarda su motivo.

**Qué costó o falló:** nada. Las otras pruebas en seco se omitieron por decisión de Pedro.

**Preguntas abiertas:** ninguna.

**Enlaces:** issue #4.

## 2026-11-18 · Tarea 6b — La orden `jimic doctor`

**Qué se hizo:** `src/jimic/doctor.py` (`diagnose` y `render`) y la orden en la CLI, con tres tests
y dos versiones incorrectas que los ponen en rojo.

**Qué aprendí:** un monkeypatch solo llega al código si este llama a la función a través del
módulo (`platform.python_version`), no con `from platform import`. Un valor fijado en un test no
debe poder coincidir con uno real.

**Qué costó o falló:** la versión fijada en los tests era `3.14.0`, que podía ser un intérprete
real. La revisión lo vio; se cambió a `9.9.9`.

**Preguntas abiertas:** si `jimic` sin subcomando debe imprimir la ayuda (backlog).

**Enlaces:** issue #5, PR #7.

## 2026-11-18 · Tarea 7 — Cierre del hito 0

**Qué se hizo:** ADR 0001 a 0004, el balance con `disparadores.sh --github`, este diario, las
lecciones y la portada.

**Qué aprendí:** al cerrar, cada cifra sale de un script. Dos pruebas de la lista de aceptación (el
formato parado y el GH013) no tenían registro escrito y se añadieron a `arranque.md`.

**Qué costó o falló:** las fuentes no coincidían en la numeración de los ADR; manda la columna ADR
del registro, y se aclaró el README de `decisiones/`.

**Preguntas abiertas:** ninguna.

**Enlaces:** issue #6, PR #8.

## Pedro sabe explicar

Guía para repasar los conceptos del criterio de aceptación. Pedro confirmó que sabe explicarlos
el 18-nov-2026, con el repaso del chat (11 de 11).

- **WSL**: un Linux real (Ubuntu) dentro de Windows, en una máquina virtual ligera. JimiC vive en
  `~/proyectos/jimic`, no en `/mnt/c`, y `.wslconfig` le pone un techo de memoria (7 GB) para que
  un test desbocado no cuelgue Windows.
- **uv**: gestiona la versión de Python, el entorno y las dependencias. `uv.lock` fija cada versión
  exacta; `uv sync --offline --frozen` rehace el mismo entorno sin red, y el CI usa
  `uv sync --locked`.
- **ruff**: linter y formateador en una sola herramienta. Va en pre-commit con `uv run`, así que
  solo hay una versión, la de `uv.lock`; el hook `PostToolUse` formatea cada `.py` que edita el
  agente.
- **pytest y su límite de tiempo**: pytest ejecuta los tests; `pytest-timeout` corta cualquier
  test a los 60 s, para que uno que no termina falle en lugar de colgar la máquina. Uno legítimo
  más largo se marca con `@pytest.mark.timeout(N)`, midiendo antes.
- **pre-commit**: ejecuta los hooks (ruff, pyright, gitleaks y los básicos) antes de cada commit y
  lo para si alguno falla. En la N3 y el CI corre sobre todos los ficheros.
- **GitHub Flow**: `main` siempre está bien; cada tarea va en su rama, se abre un PR, el CI lo
  valida y Pedro lo fusiona con *squash*. La rama se borra al fusionar.
- **Actions**: el CI de GitHub. En cada push a un PR o a `main` repite la N3 en una máquina limpia
  (`.github/workflows/ci.yml`), con las acciones fijadas por SHA.
- **El ruleset**: las reglas de `main`: nada de push directo (GH013), solo por PR y con *squash*,
  el check `tests` obligatorio, sin borrar la rama ni reescribir su historia, y CodeQL sin
  alertas «High or higher».
- **El modo plan**: Claude Code solo lee y escribe el plan; no toca nada hasta que Pedro lo
  aprueba. Aprobar el plan cuenta también como permiso para empujar la rama y abrir el PR.
- **Los hooks**: comandos que Claude Code lanza en momentos fijos. `PostToolUse` formatea,
  `Stop` ejecuta la N2 al final de cada turno (salvo en modo plan) y `SessionStart` da la rama, el
  hito y los disparadores.
- **Los niveles de tests**: N0, el formato al editar; N1, pre-commit y los tests del área antes de
  cada commit; N2, los rápidos al final de cada turno; N3, todo una vez antes del push; el CI, lo
  mismo en GitHub.

## Reflexiones de Pedro
