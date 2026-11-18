# Hito 0 — Entorno

> **Estado:** en curso.

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
| 7 | Cierre del hito 0 | |

## Criterios de aceptación

Los de la lista de aceptación del núcleo del método, y «Pedro sabe explicar» WSL, uv, ruff,
pytest y su límite de tiempo, pre-commit, GitHub Flow, Actions, el ruleset, el modo plan, los
hooks y los niveles de tests.

## Balance del hito

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
