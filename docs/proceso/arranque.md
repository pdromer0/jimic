# Arranque de JimiC

> **Estado:** documento vivo hasta cerrar el hito 0. Lo escribió el chat el 17-nov-2026 (fases 0
> y 1); el agente lo lee al empezar la fase 2 y marca aquí cada fase. Es público: no lleva nada
> personal (decisión 43 de [diseno.md](../diseno.md)).

## Respuestas de la fase 0

| # | Pregunta | Respuesta |
| --- | --- | --- |
| P1 | Nombres del proyecto, repositorio y paquete | `JimiC` · `jimic` · `jimic`; skill `tarea-jimic` (decisión 42) |
| P2 | Usuario u organización de GitHub | `pdromer0`, cuenta personal; decide y fusiona Pedro (decisión 42) |
| P3 | Repositorio público o privado; ¿datos personales? | **Público, sin nada personal.** JimiC toca datos personales, pero la configuración, los secretos, la base de datos y los casos reales viven fuera del repositorio; los tests usan datos sintéticos; nada personal en issues, PR ni logs del CI (decisión 43, ADR 0002) |
| P4 | Sistema operativo y RAM | Windows con WSL2 (Ubuntu), 16 GB; `.wslconfig` con `memory=7GB` y `swap=8GB` (decisión 44) |
| P5 | Lenguaje | Python **3.14** con uv, ruff y pytest (decisión 45), más **pyright** (decisión 46, ADR 0003) |
| P6 | Idioma de documentación, commits e interfaz | Español; el código, en inglés (decisión 42) |
| P7 | ¿Componente didáctico? | Sí: producto y aprendizaje, estilo `Explanatory`. Criterio: acercarse a lo que exigiría una empresa real (decisión 41) |
| P-15 | Revisión de las tareas críticas | El método tal cual: `revisor-plan` desde la primera tarea `critica`; `revision-reforzada` con su disparador (decisión 47) |

Las nueve decisiones de fondo se mantienen sin cambios (decisión 48). Las desviaciones **añaden**
piezas:

| Desviación | ADR |
| --- | --- |
| Repositorio público sin nada personal, con escaneo de secretos y bloqueo al hacer push de GitHub, CodeQL con bloqueo de «High or higher», aprobación del CI para PR de fuera, informe privado de vulnerabilidades con `SECURITY.md` y sin licencia por ahora (decisiones 43, 50, 52 y 53); las acciones del CI, fijadas por el SHA del commit con la versión en un comentario | 0002 |
| pyright en modo estándar en N1, N3 y CI (decisión 46) | 0003 |
| gitleaks en pre-commit (decisión 49) | 0004 |

## Variables

`<PROYECTO>` = JimiC · `<repo>` = jimic · `<paquete>` = jimic · `<dueño>` = pdromer0 ·
`<persona>` = Pedro · `<idioma>` = español · `<tarea>` = tarea-jimic

`vars.toml` va **fuera del repositorio**, en `~/proyectos/arranque-jimic/vars.toml`.

## Material de entrada

Antes de la fase 2, Pedro deja en `~/proyectos/arranque-jimic/` (fuera del repositorio):

- `metodo-edsim/`: el método, extraído de `metodo-edsim.zip`.
- `arranque.md`: este texto.
- `diseno.md`: la versión publicable del diseño (pasada de publicación del 17-nov-2026).
- `vars.toml`: las variables.

**Cómo trabaja Pedro:** crea la carpeta vacía `~/proyectos/jimic`, abre Cursor en ella (conectado a
WSL) y lanza Claude Code desde la terminal de Cursor con
`claude --permission-mode plan --add-dir ~/proyectos/arranque-jimic`, para que el agente lea el
material sin pedir permiso (en la extensión, se acepta el permiso de lectura cuando lo pida). El
código vive en `~/proyectos/jimic`, nunca en `/mnt/c`.

## Lo que cambia respecto al núcleo, fase a fase

Todo lo que no aparece aquí se hace como dice `nucleo.md`.

### Fase 2: repositorio, paquete y calidad local

0. La carpeta ya existe y es la de trabajo: `uv init --package` **dentro** de `~/proyectos/jimic`
   (sin nombre; toma `jimic` de la carpeta), en lugar de `mkdir`, `uv init --package jimic` y
   `cd`. `instalar.py` se llama con `--vars ~/proyectos/arranque-jimic/vars.toml`.
1. Python 3.14: `uv python pin 3.14` y `requires-python = ">=3.14"` en `pyproject.toml`.
2. Dependencias de desarrollo: `uv add --dev pytest pytest-timeout ruff pre-commit pyright`.
3. `pyproject.toml`, además del fragmento del núcleo:

   ```toml
   [tool.pyright]
   include = ["src", "tests"]
   pythonVersion = "3.14"
   typeCheckingMode = "standard"
   reportUnnecessaryTypeIgnoreComment = "error"
   ```

4. `.pre-commit-config.yaml`, además de lo del núcleo:
   - un hook local `pyright` (`uv run --frozen pyright`, `language: system`,
     `pass_filenames: false`, `types: [python]`): una sola versión, la de `uv.lock`, como ruff;
   - el hook oficial de **gitleaks**, con la versión verificada según `hechos-volatiles.md`.
5. **Comprobaciones deliberadas**, además de la del formato: un error de tipos (pyright para el
   commit) y una clave falsa con formato real, inventada en el momento (gitleaks para el commit).
   Las dos se deshacen.
6. Antes de crear el repositorio: `git status` sin ficheros personales ni `.env`, y gitleaks sin
   hallazgos sobre la historia. Después, `gh repo create jimic --public --source . --push`.

### Fase 3: CI y protección de `main`

**CI [AGENTE]:** como en el núcleo. pre-commit ya ejecuta pyright y gitleaks; compruébalo en la
primera ejecución (si el hook de gitleaks necesita algo que el CI no tiene, se resuelve aquí y se
anota en «Incidencias»). El CI no tiene secretos.

**Ajustes [PERSONA]**, después de los pasos 1 a 5 del núcleo:

6. *Settings → Advanced Security* (en algunas versiones de la interfaz, *Code security*) *→ CodeQL
   analysis → Set up → Default.* Esperar a su primera
   ejecución.
7. En el ruleset «Proteger main», *Require code scanning results*: herramienta **CodeQL**,
   *Security alerts* = **High or higher**, *Alerts* = None.
8. *Settings → Advanced Security*: comprobar que están activados *Secret scanning* y *Push
   protection*; activar *Dependabot alerts* (solo los avisos: ni *security updates* ni *version
   updates*, decisión 51) y *Private vulnerability reporting*.
9. *Settings → Actions → General → Approval for running fork pull request workflows from
   contributors*: **Require approval for all external contributors**.

[COMPROBAR] Un push directo a `main` se rechaza con `GH013`; el siguiente PR muestra el análisis de
CodeQL; la pestaña *Security* lista CodeQL y el escaneo de secretos.

**Al terminar la fase 3, el agente recuerda a Pedro el piloto** (sección «Piloto», más abajo).

### Fase 4: instrucciones, Claude Code y documentación

1. `docs/diseno.md` es la versión publicable de `~/proyectos/arranque-jimic/diseno.md`, tal cual:
   sustituye al esqueleto de la plantilla.
2. Reglas nuevas en `docs/proceso/tarea.md`, cada una en un solo sitio:
   - **Nada personal** en issues, PR, «Notas del chat», commits ni logs. Si hace falta un ejemplo,
     se inventa.
   - **Silencios de pyright:** cada `# type: ignore` lleva el código del error y su motivo; `Any`,
     solo con un comentario que lo justifique. Las librerías sin tipos se encierran en las
     herramientas propias, que declaran lo que devuelven.
   - **gitleaks:** en los tests, valores falsos evidentes (`"test-token-123"`); cada excepción,
     justificada en `.gitleaks.toml`.
   - **Lo bruto del piloto y los casos reales** nunca entran en el repositorio; solo su versión
     destilada o sintética.
3. `SECURITY.md` corto, en español: cómo avisar por el informe privado de vulnerabilidades y qué
   interesa (saltarse la guardia, fugas de datos personales, inyección de instrucciones).
4. Las plantillas de issues y de PR llevan el aviso «Nada personal: si hace falta un ejemplo,
   invéntalo». **Se aplica en la fase 5**, al instalar esas plantillas.
5. Cuando el H1 fije la carpeta de los ficheros personales, se añade su `deny` de lectura a
   `.claude/settings.json`, como el de `.env`.
6. **Añadido de JimiC:** `Bash(uv run pyright*)` en el `allow` de `.claude/settings.json`, junto
   al de ruff: pyright es parte de la N1 (decisión 46).

### Fase 5: GitHub como gestor del trabajo

Como en el núcleo.

### Fase 6: prueba en seco y primera tarea

Como en el núcleo. Para la 6b, una orden pequeña de la CLI con tipos y dos o tres tests con valores
a mano y una versión incorrecta; por ejemplo, `jimic doctor`, que dice si existe `.env` (sin
leerlo) y qué versión de Python se usa.

### Fase 7: cierre del hito 0

- **ADR 0001** «Adoptamos el método EDsim v2», con el objetivo doble y el criterio de empresa
  (decisiones 1, 41 y 48), y **0002** (repositorio público), **0003** (pyright) y **0004**
  (gitleaks).
- Antes de cerrar, **el piloto está hecho** y sus conclusiones están en Drive.
- El cierre propondrá `adr-y-releases` (ya hay decisiones difíciles de revertir) y anotará que
  `metricas-pr` y `revisor-plan` entran al empezar el H1 (sección 12.4 del diseño).

## Piloto (decisión 54) [PERSONA]

- **Cuándo:** un día laborable normal mientras el agente hace las fases 2 a 5, y siempre antes de
  la fase 7. Dos ratos de chat de 20 a 30 minutos: el plan por la mañana y el cierre por la noche.
- **Cómo:** una sesión nueva del proyecto «Asistente Personal» en claude.ai, con los conectores de
  Calendar, Todoist y Drive, y el prompt `JimiC/piloto/prompt-piloto.md` de Drive.
- **Dónde queda:** todo lo bruto (conclusiones, borradores de prompts, escenarios) en
  `JimiC/piloto/` de Drive. **Nada de eso entra en el repositorio**: lo destilado llega en el H1.

## Fase 1: comprobación de la máquina (17 y 18-nov-2026)

| Comprobación | Resultado |
| --- | --- |
| WSL2 con Ubuntu por defecto (`wsl -l -v`) | ✅ VERSION 2 |
| `.wslconfig` con `memory=7GB` y `swap=8GB` | ✅ |
| git, jq, gh, uv y Claude Code responden | ✅ git 2.43, jq 1.7, gh 2.103, uv 0.13, Claude Code 2.1 |
| `gh auth status` con `pdromer0` y permisos `repo` y `workflow` | ✅ |
| Correo *noreply* en git | ✅ |
| `init.defaultBranch`, `pull.ff only`, `fetch.prune true` | ✅ configurados (18-nov-2026) |
| Python 3.14 | ✅ 3.14.10, descargado por uv en la fase 2 al fijarlo con `uv python pin 3.14` |
| Cursor en *WSL: Ubuntu* y la extensión de Claude Code en modo plan | ✅ *WSL: Ubuntu*, sin entorno activado; `claudeCode.initialPermissionMode = "plan"` comprobado en el panel (18-nov-2026) |
| `~/proyectos/jimic` no existe | ✅ |

## Fase 3: ajustes comprobados por la API (18-nov-2026)

Leídos con `gh api` (solo lectura), salvo la aprobación de los PR de fuera, que estaba en
`first_time_contributors` y el agente cambió por la API con el permiso de Pedro.

| Ajuste | Lectura | Resultado |
| --- | --- | --- |
| Ruleset «Proteger main» | `rulesets/27541863` | ✅ `active`, sin excepciones, `~DEFAULT_BRANCH`; cinco reglas: `deletion`, `non_fast_forward`, `pull_request` (0 aprobaciones, solo `squash`), `required_status_checks` (`tests`) y `code_scanning` (CodeQL, `high_or_higher`, alertas `none`) |
| Fusión | `repos/pdromer0/jimic` | ✅ solo *squash*; `delete_branch_on_merge` |
| CodeQL | `code-scanning/default-setup` | ✅ `configured`, `actions` y `python`, semanal; primer análisis en `main` con 0 resultados |
| Secret scanning y push protection | `security_and_analysis` | ✅ `enabled` los dos |
| Dependabot | `vulnerability-alerts`, `automated-security-fixes` | ✅ avisos activados (204); *security updates* desactivados (`enabled: false`) |
| Informe privado de vulnerabilidades | `private-vulnerability-reporting` | ✅ `enabled: true` |
| Aprobación del CI para PR de fuera | `actions/permissions/fork-pr-contributor-approval` | ✅ `all_external_contributors` (estaba en `first_time_contributors`; cambiado por la API) |
| Token del CI | `actions/permissions/workflow` | ✅ `read`, sin aprobar PR |
| SBOM de GitHub | `dependency-graph/sbom` | ✅ lee `uv.lock`: sus 22 paquetes, con las dependencias indirectas (`cfgv`, `distlib`, `nodeenv`…), y las 3 acciones del CI |

## Fases

| Fase | Estado | PR o nota |
| --- | --- | --- |
| 0 Preguntas | Hecha (17-nov-2026) | Decisiones 41 a 54 del diseño |
| 1 Máquina | Comprobada (17 y 18-nov-2026) | Tabla anterior |
| 2 Repositorio y calidad | Hecha (18-nov-2026) | Commit inicial en `main`; pyright 1.1.414 y gitleaks v8.30.1 en pre-commit |
| 3 CI y ruleset | Hecha (18-nov-2026) | PR #1; checkout v7.0.1, setup-uv v10.3.3 y cache v6.1.0, por SHA; ajustes en la tabla «Fase 3» |
| 4 Agentes | Hecha (18-nov-2026); falta la comprobación de Pedro en una sesión nueva tras fusionar | PR #2; núcleo instalado, `diseno.md` publicable, reglas de JimiC en `tarea.md`, `SECURITY.md` y `gitleaks-historia` en N3 y CI |
| 5 GitHub | | |
| 6 Primera tarea | | |
| 7 Cierre del hito 0 | | |
| Piloto | | |

## Incidencias

- **Fase 1. Entorno virtual de otro proyecto activo.** El terminal de Cursor abierto en EDsim
  muestra `(edsim)`: Cursor activa el entorno de ese proyecto. Causa: la ventana de Cursor es la de
  EDsim. Solución: trabajar en JimiC desde una ventana de Cursor propia (`~/proyectos/jimic`) o un
  terminal de Ubuntu sin `(edsim)`; si aparece, `deactivate`.
- **Fase 1. Docker Desktop en marcha.** Su distribución comparte la máquina virtual de WSL2 y el
  techo de 7 GB. JimiC no usa Docker (decisión 30): cerrar Docker Desktop mientras se trabaja en
  JimiC si no hace falta para otra cosa.
- **Fase 1.** Docker Desktop se queda abierto porque lo usan otros proyectos de la máquina.
  Mientras se trabaja en JimiC, la pila de EDsim se pausa con `docker compose -p edsim stop`
  (available pasó de 3,3 a 4,8 GiB) y se reanuda con `start`.
- **Fase 1. Ajustes de git sin configurar** (`pull.ff`, `fetch.prune`). Solución: los comandos de la
  sección 2.2 del núcleo antes de la fase 2.
- **Fase 2. pyright desde PyPI no trae Node.js.** La rueda de `pyright` lleva el JS de pyright,
  pero sin Node: si no hay `node` en el `PATH`, lo descarga con `nodeenv` en la primera ejecución,
  fuera de `uv.lock` (aquí no hay `node` de Linux; sí un `npm` de Windows en `/mnt/c`, que se cuela
  en el `PATH`), y en el CI usaría el Node de la imagen. Solución: `uv add --dev "pyright[nodejs]"`
  en lugar de `pyright`; `nodejs-wheel-binaries` (24.19.0) queda fijado en `uv.lock` y es el mismo
  en local y en el CI. Se comprueba con `PYRIGHT_PYTHON_DEBUG=1 uv run pyright --version`: «using
  bundled pyright» y «Using nodejs_wheel».
- **Fase 2. gitleaks se compila la primera vez.** Su hook es `language: golang`: pre-commit
  descarga Go y compila gitleaks (unos 7 minutos, una sola vez). El binario no conoce su versión
  (`gitleaks version` dice «version is set by build process»): la versión es el `rev` de
  `.pre-commit-config.yaml`. Para revisar la historia se usa ese mismo binario
  (`~/.cache/pre-commit/repo*/golangenv-default/bin/gitleaks git`).
- **Fase 2. Un verde que no prueba nada.** `scripts/n3.sh` antes del primer `git add`:
  `pre-commit run --all-files` solo ve los ficheros de git, se salta todos los hooks y sale en
  verde. Con el árbol preparado, los ocho hooks se ejecutan.
- **Fase 3. gitleaks en el CI: caché de pre-commit.** Sin caché, el CI instala los entornos de
  pre-commit en cada ejecución; gitleaks se compila en unos 24 s (no los 7 minutos de local:
  el log no dice qué Go usa, pero por el tamaño de la caché, 26 MiB, sería el `go` que trae la
  imagen `ubuntu-26.04`). Solución: `actions/cache` de `~/.cache/pre-commit` con la clave del hash
  de `.pre-commit-config.yaml`. Medido en el PR #1: primera ejecución, sin caché, pre-commit 31 s y
  job 43 s; segunda, con caché, pre-commit 1 s y job 7 s (en rojo por el fallo deliberado); tercera,
  pre-commit 1 s y job 13 s. Límites: la caché de un PR solo la ve ese PR (tras el squash, el
  primer push a `main` vuelve a compilar una vez), solo se guarda con el job en verde y GitHub la
  borra tras 7 días sin uso. `setup-uv` ya guarda la caché de uv por su cuenta
  (`enable-cache: auto`).
- **Fase 3. Comprobación deliberada.** Un test con un `assert` de una versión incorrecta (no
  `assert False`, que ruff marca con B011): el commit pasa los hooks locales y el CI se para en
  `uv run pytest` con `FAILED …test_fallo_deliberado_de_la_fase_3 - AssertionError: fallo
  deliberado de la fase 3`, con pre-commit en verde. Pedro lo leyó con `gh run view --log-failed`;
  se quitó en el commit siguiente y el CI volvió a verde.
- **Fase 3. Aprobación de los PR de fuera.** Al cerrar la fase, la API daba
  `first_time_contributors` (el valor por defecto) en lugar de «Require approval for all external
  contributors» (decisión 52). Solución: `gh api -X PUT
  repos/pdromer0/jimic/actions/permissions/fork-pr-contributor-approval -f
  approval_policy=all_external_contributors`, con el permiso de Pedro, y lectura de comprobación.
- **Fase 4. gitleaks no revisaba nada en la N3 ni en el CI.** El hook oficial es `gitleaks git
  --pre-commit --staged`: solo mira lo preparado. En la N3 y el CI (`pre-commit run --all-files`)
  no hay nada preparado y salía «Passed» tras revisar «0 commits, ~0 bytes» (comprobado en local;
  en el CI de la fase 3, el mismo «Passed»). Solución: un segundo hook del mismo repositorio y
  `rev`, `gitleaks-historia` (`gitleaks git --redact --verbose`, `stages: [manual]`), que revisa
  toda la historia: en la N3, con `scripts/n3.d/gitleaks.parte.sh`; en el CI, como paso propio con
  `--verbose` y `fetch-depth: 0`. Se corrigieron con ello las secciones 12.2 y 13 y la decisión 49
  de `diseno.md`. **Candidata para `lecciones.md` en el cierre:** un hook en verde no dice qué
  revisó; en la N3 y el CI se mira cuánto revisó, no solo el «Passed».
