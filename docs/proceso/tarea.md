# Ciclo de una tarea

Cada tarea de JimiC sale de su issue de GitHub y sigue estos pasos, la haga el agente que la
haga. Claude Code los ejecuta con la skill `/tarea-jimic N`. Este fichero es la **única fuente** de las
reglas de tests, de GitHub y de git: `AGENTS.md`, `CLAUDE.md` y las skills enlazan aquí.

**Pasos de los módulos activos.** Si existe `docs/proceso/pasos/`, lee sus ficheros antes de
empezar: cada uno añade los pasos de un módulo activo y dice en qué punto del ciclo entran.

## Reglas de toda la tarea

- **GitHub, solo con `scripts/gh.sh`** (REST, igual en local y en la nube): `issue`,
  `comentarios`, `bloqueado-por`, `pr`, `checks`, `revisiones`, `abrir-pr` y `editar-pr`. Si
  hace falta algo que no hace, se pide permiso y se dice por qué.
- **Niveles de tests.** N1: antes de cada commit, `uv run pre-commit run --files <tocados>`
  (ruff, pyright, gitleaks sobre lo preparado y los hooks básicos) y los ficheros de test del
  área tocada. N2: el hook `Stop`, al final de cada turno. N3: `scripts/n3.sh` (pre-commit en
  todo, gitleaks sobre toda la historia y la suite completa), **una sola vez antes de cada
  push**, después de la revisión propia y de sus correcciones (§4). El CI repite la N3.
- **Git.** La rama es la del issue, con prefijo `feat/`, `fix/`, `docs/`, `refactor/`, `test/`,
  `chore/` o `ci/`. Nunca `main`, nunca push forzado, nunca fusionar.
- Si un paso necesita algo que no puedes hacer, para y dilo; no lo rodees.
- **Nada personal** en issues, PR, «Notas del chat», commits ni logs: el repositorio es público.
  Si hace falta un ejemplo, se inventa.
- **Silencios de pyright:** cada `# type: ignore` lleva el código del error y su motivo; `Any`,
  solo con un comentario que lo justifique. Las librerías sin tipos se encierran en las
  herramientas propias, que declaran lo que devuelven.
- **gitleaks:** en los tests, valores falsos evidentes (`"test-token-123"`); cada excepción,
  justificada en `.gitleaks.toml`.
- **Lo bruto del piloto y los casos reales** nunca entran en el repositorio; solo su versión
  destilada o sintética.

## 1. Arranque

- `scripts/gh.sh issue N` y `scripts/gh.sh comentarios N`. Si está cerrado o `es_pr` es `true`,
  para. Las «Notas del chat» son el comentario que empieza así; si un comentario precisa el
  cuerpo, manda él, y entre dos que se contradicen, el más reciente.
- `scripts/gh.sh bloqueado-por N`: si alguno sigue abierto, para y di cuáles. Las dependencias
  nativas son las únicas; el cuerpo solo guarda su motivo.
- Lee solo las secciones de la especificación que enlaza el issue (`grep -n '^#'` y rangos). Si
  no enlaza ninguna, la especificación son el cuerpo y los comentarios.

## 2. Plan

1. **Contexto y qué cambia**, con la rama.
2. **Lo que el plan interpreta**: todo lo que no dicen literalmente la especificación ni las notas
   (un nombre, una población, un orden, un caso no cubierto). Es lo primero que se revisa.
3. **Diferencias con las notas del chat**, y por qué. Si no hay, se dice.
4. **Valores a mano**, cada uno con su cálculo **ejecutado** con código independiente de la
   función probada (`python3 - <<'EOF'`) y su salida. Nunca de memoria.
5. **Casos límite**, como lista: enteros enormes, NaN e infinitos, `date` frente a `datetime`,
   empates y orden de lo simultáneo, longitudes distintas, vacíos, primero y último.
6. **Versiones incorrectas**, cada una con su predicción firme: qué falla y qué sigue pasando.
7. **Comprobaciones**: tests, medidas y comprobaciones a mano.
8. **Cuándo parar y preguntar.**

El plan es corto; el detalle va al PR. Lo aprueba Pedro.

## 3. Implementación

- Un commit por paso lógico, con N1, sin esperar revisión entre pasos. Mensajes con heredoc.
- El refactor, en un commit aparte y sin tocar los tests.
- Una versión incorrecta se aplica, se ejecutan los ficheros donde se predice el fallo y los que
  deben seguir pasando, y se deshace. Cada test dice qué error pilla que no pille otro.

## 4. Antes del push

1. **Revisión adversaria del propio diff**: en Claude Code, `/code-review medium main...<rama>`
   (siempre con nivel y destino).
2. **Clasifica todos los hallazgos antes de tocar nada**: se corrige, se explica o va a
   «Pendiente» con su destino.
3. **Las correcciones**, cada una en su commit con N1.
4. **Una sola N3** (`scripts/n3.sh`). Si falla, se corrige con N1 y se repite **una vez**, no una
   por corrección; si vuelve a fallar, para y dilo.

Sin N3 solo en dos casos: el primer push de una rama que es entera documentación o plantillas,
con permiso de Pedro; y una ronda de ajustes que solo toca documentación. Los scripts de los
hooks no los ejecuta la N3: se verifican a mano y se anota en el PR.

## 5. Documentación en el mismo PR

Una entrada en el anexo del documento que enlaza el issue: qué se hizo (issue, rama, commits),
decisiones del diff, valores a mano, medidas, versiones incorrectas (predicción frente a
observado), revisión y pendiente. Y lo que la tarea cambie en el diseño o en la especificación.

## 6. Pull request

- `git push -u origin <rama>` y `scripts/gh.sh abrir-pr <rama> "<título>" <fichero>`, con la
  plantilla `.github/pull_request_template.md` y «Closes #N».
- Recuerda a Pedro que haga `/clear` antes de la siguiente tarea.

## 7. Ajustes de la revisión

`scripts/gh.sh pr N`, `checks <punta>`, `comentarios N` y `revisiones N`. Si la punta del PR no es
la local, `git pull --ff-only` antes de nada. Después, como en §4: clasificar todo, corregir con
N1 y una sola N3 antes del push. Pon al día el PR y el anexo.

## 8. Tras la fusión

Pedro fusiona con *squash*. Después, en una sesión limpia, `/sincronizar <PR>`.
