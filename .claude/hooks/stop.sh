#!/usr/bin/env bash
# Stop hook (N2): al terminar cada turno de Claude, los tests rápidos (sin `slow`), parando en
# el primer fallo; si existe scripts/n2.sh (lo añade el módulo sello-n2), ese. En rojo sale con
# 2 y Claude recibe la cola de la salida; en verde, un systemMessage corto. En modo plan no
# ejecuta nada. Apunta la duración para disparadores.sh.
set -u

# Sin jq no se puede leer la entrada: error visible que no bloquea (el 2, sin poder leer
# stop_hook_active, entraría en bucle; el stderr de un 0 no se ve).
if ! command -v jq >/dev/null 2>&1; then
  echo "stop.sh: jq no está instalado; se omiten los tests." >&2
  exit 1
fi

entrada=$(cat)
if [ "$(jq -r '.stop_hook_active // false' <<<"$entrada" 2>/dev/null)" = "true" ]; then
  exit 0
fi
if [ "$(jq -r '.permission_mode // ""' <<<"$entrada" 2>/dev/null)" = "plan" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" || exit 0
# shellcheck source=scripts/comun.sh
source scripts/comun.sh

inicio=$(date +%s)
if [ -x scripts/n2.sh ]; then
  salida=$(scripts/n2.sh 2>&1)
else
  salida=$(uv run pytest -q -x -m "not slow" 2>&1)
fi
rc=$?
segundos=$(($(date +%s) - inicio))
apunta_tiempo rapida "$segundos" "$rc"

if [ "$rc" -ne 0 ]; then
  printf '%s\n' "$salida" | tail -n 50 >&2
  exit 2
fi
ultima=$(tail -n 1 <<<"$salida")
case "$ultima" in
  "n2.sh: "*) mensaje="N2: ${ultima#n2.sh: } (${segundos} s)" ;;
  *) mensaje="N2 en verde (${segundos} s)" ;;
esac
jq -n --arg m "$mensaje" '{systemMessage: $m}'
