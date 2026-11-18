# 0004. gitleaks contra los secretos

- **Estado:** Aceptada
- **Fecha:** 2026-11-17

## Contexto

El repositorio es público (ADR [0002](0002-repositorio-publico.md)). Un secreto publicado gasta
créditos de la API o suplanta al bot de Telegram. El núcleo solo trae la regla `deny` de
`Read(./.env)`, y el escaneo de GitHub llega tarde (el secreto ya está en un commit) y solo
bloquea los tipos que reconoce con precisión. Desviación del núcleo: **añade** una pieza (ADR
[0001](0001-adoptamos-el-metodo-edsim.md)).

## Decisión

Secretos en tres capas (decisión 49):

1. La regla `deny` de `Read(./.env)` (núcleo).
2. **gitleaks** en pre-commit: en cada commit, sobre lo preparado (`--staged`); en la N3 y el CI,
   sobre toda la historia, con un segundo hook `gitleaks-historia` del mismo repositorio y la
   misma versión. En los tests, valores falsos evidentes (`"test-token-123"`); cada excepción,
   justificada en `.gitleaks.toml` (al cerrar el hito 0 no hay ninguna, ni el fichero).
3. El escaneo de secretos y el bloqueo al hacer push de GitHub.

## Alternativas consideradas

- Solo GitHub: para tarde y no ve las claves genéricas. Tendría sentido en un repositorio privado
  sin ningún secreto propio.
- Solo gitleaks: pierde una vigilancia gratuita que ya viene activada en los repositorios públicos.

## Consecuencias

- Un secreto se para en el PC, antes de entrar en la historia.
- La primera vez, pre-commit compila gitleaks con Go (unos 7 minutos en local, una sola vez).
- Un falso positivo exige una excepción justificada, no un `--no-verify`.

## Qué cambió al implementarla

- Fase 3 (PR #1, 4948329): una caché de `~/.cache/pre-commit` en el CI, con la clave del hash de
  `.pre-commit-config.yaml`; sin ella, el CI compilaba gitleaks en cada ejecución.
- Fase 4 (PR #2, 04066d4): el hook oficial (`--staged`) no revisaba nada en la N3 ni en el CI, y
  salía «Passed» tras «0 commits, ~0 bytes». Se añadió `gitleaks-historia`
  (`gitleaks git --redact --verbose`, `stages: [manual]`): en la N3, con
  `scripts/n3.d/gitleaks.parte.sh`; en el CI, como paso propio con `fetch-depth: 0`. Con ello se
  corrigieron la decisión 49 y las secciones 12.2 y 13 de `diseno.md` (`arranque.md`, incidencias
  de la fase 4).
