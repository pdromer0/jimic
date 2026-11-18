#!/usr/bin/env bash
# N3: comprobación completa antes de cada push, una sola vez, después de la revisión propia.
# Ejecuta todas las partes aunque alguna falle (pre-commit en todos los ficheros y la suite
# completa), imprime el tiempo de cada una y sale con 1 si alguna falló.
#
# Los módulos añaden piezas en scripts/n3.d/, que se cargan con `source` en el mismo shell:
#   *.antes.sh       antes de las partes (pueden fijar PYTEST_N3_ARGS)
#   *.parte.sh       partes nuevas, con la función `parte etiqueta comando…`
#   *.tras-verde.sh  solo si todo salió en verde
set -u
raiz=$(git rev-parse --show-toplevel) || exit 1
cd "$raiz" || exit 1
# shellcheck source=scripts/comun.sh
source scripts/comun.sh

resumen=()
fallos=0
PYTEST_N3_ARGS=()
piezas() { local f; for f in scripts/n3.d/*."$1".sh; do [ -f "$f" ] && source "$f"; done; return 0; }
parte() {
  local etiqueta=$1 inicio rc segundos estado
  shift
  printf '\n== n3.sh: %s\n' "$etiqueta"
  inicio=$(date +%s)
  "$@"
  rc=$?
  segundos=$(($(date +%s) - inicio))
  if [ "$rc" -eq 0 ]; then estado=verde; else estado="fallo (código $rc)"; fallos=$((fallos + 1)); fi
  resumen+=("$(printf '%-12s %-18s %d:%02d' "$etiqueta" "$estado" $((segundos / 60)) $((segundos % 60)))")
}

piezas antes
total_inicio=$(date +%s)
parte pre-commit uv run pre-commit run --all-files
parte pytest uv run pytest "${PYTEST_N3_ARGS[@]}"
piezas parte
total=$(($(date +%s) - total_inicio))

printf '\n== n3.sh: resumen\n'
printf '%s\n' "${resumen[@]}"
printf 'total        %d:%02d\n' $((total / 60)) $((total % 60))
if [ "$fallos" -gt 0 ]; then
  apunta_tiempo n3 "$total" 1
  echo "n3.sh: $fallos parte(s) fallaron."
  exit 1
fi
apunta_tiempo n3 "$total" 0
piezas tras-verde
echo "n3.sh: todo en verde."
