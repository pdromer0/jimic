#!/usr/bin/env bash
# Mide lo que se puede medir y lista los disparadores cumplidos de los módulos inactivos de
# docs/proceso/modulos.md (un módulo que no está en el registro cuenta como inactivo).
#
#   disparadores.sh            informe local: cumplidos, datos y preguntas para el cierre
#   disparadores.sh --github   además, lo que necesita GitHub (issues, PR, duración del CI)
#   disparadores.sh --linea    una línea o nada, sin red (para SessionStart)
#
# Lo usa /cerrar-hito para el balance. Sale siempre con 0 salvo error de uso.
set -u
modo=informe
case "${1:-}" in
  "") ;;
  --github) modo=github ;;
  --linea) modo=linea ;;
  *) echo "uso: disparadores.sh [--github|--linea]" >&2; exit 64 ;;
esac
raiz=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
cd "$raiz" || exit 0
# shellcheck source=scripts/comun.sh
source scripts/comun.sh

UMBRAL_RAPIDA=60     # s, mediana de la rápida en verde: módulo sello-n2
UMBRAL_N3_NUBE=600   # s, última N3 en verde: módulo nube
UMBRAL_N3_XDIST=120  # s, última N3 en verde: medir xdist
UMBRAL_DOCS_CI=1800  # s por ventana de 20 PR: PR de solo documentación × duración del CI

declare -A estado
if [ -f docs/proceso/modulos.md ]; then
  while IFS='|' read -r _ m _ e _; do
    m=$(tr -d ' `' <<<"$m"); e=$(tr -d ' ' <<<"$e")
    [ -n "$m" ] && estado[$m]=$e
  done < <(grep -E '^\| *`?[a-z0-9-]+`? *\|' docs/proceso/modulos.md)
fi
inactivo() { local e=${estado[$1]:-inactivo}; [ "$e" = inactivo ]; }

cumplidos=()
cumple() { inactivo "$1" && cumplidos+=("$1|$2"); }

mediana() { sort -n | awk '{v[NR]=$1} END {if (NR==0) exit 1; if (NR%2) print v[(NR+1)/2]; else print int((v[NR/2]+v[NR/2+1])/2)}'; }
T=$CACHE_DIR/tiempos.tsv
if [ -f "$T" ]; then
  if r=$(awk -F'\t' '$2=="rapida" && $4==0 {print $3}' "$T" | tail -n 10 | mediana); then
    [ "$r" -gt "$UMBRAL_RAPIDA" ] && cumple sello-n2 "la rápida tarda $r s (mediana)"
    datos+=("rápida en verde: mediana $r s")
  fi
  if n3=$(awk -F'\t' '$2=="n3" && $4==0 {print $3}' "$T" | tail -n 1 | mediana); then
    [ "$n3" -gt "$UMBRAL_N3_NUBE" ] && cumple nube "la última N3 tardó $n3 s"
    [ "$n3" -gt "$UMBRAL_N3_XDIST" ] && cumple xdist "la última N3 tardó $n3 s: mide xdist"
    datos+=("última N3 en verde: $n3 s")
  fi
fi
[ -f compose.yaml ] || [ -f docker-compose.yml ] && cumple servicios-docker "existe compose.yaml"
[ -d notebooks ] && cumple notebooks "existe notebooks/"
{ [ -f app/pyproject.toml ] || grep -qs '^\[tool\.uv\.workspace\]' pyproject.toml; } && cumple app-workspace "hay un segundo paquete"
{ [ -d config ] || [ -d tests/data ]; } && cumple datos-de-referencia "existe config/ o tests/data/"
n_tests=$(grep -rhoE '^\s*(async )?def test_' tests 2>/dev/null | wc -l)
datos+=("tests: $n_tests")

if [ "$modo" = github ]; then
  R=$(repo_slug) || exit 0
  if criticas=$(gh api "repos/$R/issues?state=all&labels=critica&per_page=100" --jq '[.[] | select(has("pull_request") | not)] | length' 2>/dev/null); then
    [ "$criticas" -gt 0 ] && cumple revisor-plan "$criticas issue(s) con la etiqueta critica"
  fi
  if abiertos=$(gh api "repos/$R/issues?state=open&per_page=100" --jq '[.[] | select(has("pull_request") | not)] | length' 2>/dev/null); then
    [ "$abiertos" -gt 10 ] && cumple tablero "$abiertos issues abiertos"
    datos+=("issues abiertos: $abiertos")
  fi
  if prs=$(gh api "repos/$R/pulls?state=open&per_page=100" --jq 'length' 2>/dev/null); then
    [ "$prs" -ge 2 ] && cumple tablero "$prs PR abiertos a la vez"
  fi
  # PR de solo documentación entre los 20 últimos fusionados × mediana del CI de los PR.
  if fusionados=$(gh api "repos/$R/pulls?state=closed&per_page=20" --jq '.[] | select(.merged_at != null) | .number' 2>/dev/null); then
    docs=0
    for n in $fusionados; do
      otros=$(gh api "repos/$R/pulls/$n/files?per_page=100" --jq '[.[].filename | select((test("\\.md$") or startswith("docs/")) | not)] | length' 2>/dev/null) || continue
      [ "$otros" -eq 0 ] && docs=$((docs + 1))
    done
    if ci=$(gh api "repos/$R/actions/runs?event=pull_request&status=completed&per_page=20" --jq '.workflow_runs[] | ((.updated_at | fromdate) - (.run_started_at | fromdate))' 2>/dev/null | mediana); then
      datos+=("PR de solo documentación en los 20 últimos: $docs; CI de un PR: mediana $ci s")
      [ $((docs * ci)) -ge "$UMBRAL_DOCS_CI" ] && cumple ci-salto-docs "$docs PR de solo documentación × $ci s de CI"
    fi
  fi
fi

if [ "$modo" = linea ]; then
  [ "${#cumplidos[@]}" -eq 0 ] && exit 0
  nombres=$(printf '%s\n' "${cumplidos[@]}" | cut -d'|' -f1 | sort -u | paste -sd, - | sed 's/,/, /g')
  echo "disparadores cumplidos (módulos inactivos): $nombres"
  exit 0
fi

echo "== Disparadores cumplidos (módulos inactivos)"
if [ "${#cumplidos[@]}" -eq 0 ]; then echo "ninguno"; else printf '%s\n' "${cumplidos[@]}" | sed 's/|/: /'; fi
echo "== Datos"
printf '%s\n' "${datos[@]}"
echo "== Preguntas para el cierre (no se pueden medir aquí)"
pregunta() { inactivo "$1" && echo "$1: $2"; }
pregunta mutante "¿alguna tarea tuvo más de 2 versiones incorrectas o dejó el árbol sucio?"
pregunta revision-reforzada "¿llegó a main un bug que una revisión externa habría visto?"
pregunta claude-en-pr "¿hubo más de 3 ajustes triviales que obligaron a abrir una sesión?"
pregunta adr-y-releases "¿hubo una decisión difícil de revertir?"
pregunta metricas-pr "¿empieza el hito 1 o uno posterior?"
pregunta guardia "¿hace falta autorizar escrituras en GitHub sin preguntar?"
pregunta dobles-con-tope "¿hay un doble de prueba que el código llama en bucle?"
pregunta comodidad-local "¿se te pasan las esperas de Claude Code sin enterarte?"
pregunta migracion-docs "¿hay documentación del proyecto fuera del repositorio?"
pregunta diagnostico "¿cambiaron las instrucciones de los agentes en este hito?"
exit 0
