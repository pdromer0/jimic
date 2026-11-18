#!/usr/bin/env bash
# SessionStart hook: la salida entra en el contexto al abrir, reanudar, tras /clear y al
# compactar. Solo git, grep y lecturas locales, sin red; siempre sale con 0.
cd "${CLAUDE_PROJECT_DIR:-$(dirname "$0")/../..}" 2>/dev/null || exit 0
git rev-parse --git-dir >/dev/null 2>&1 || exit 0

rama=$(git branch --show-current 2>/dev/null)
echo "rama: ${rama:-(HEAD desacoplado)}"
etiqueta=$(git describe --tags --abbrev=0 2>/dev/null) || etiqueta=""
echo "última etiqueta: ${etiqueta:-ninguna}"
hito=$(grep -m1 '^- Hito en curso' AGENTS.md 2>/dev/null) || hito=""
hito=${hito#- }
echo "${hito:-Hito en curso: no encontrado}"
git status -sb 2>/dev/null \
  | awk 'NR <= 15 { print } END { if (NR > 15) printf "… y %d líneas más\n", NR - 15 }'
# Disparadores locales de los módulos inactivos (milisegundos; sin red).
if [ -x scripts/disparadores.sh ]; then
  scripts/disparadores.sh --linea 2>/dev/null || true
fi
exit 0
