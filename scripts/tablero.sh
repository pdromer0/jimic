#!/usr/bin/env bash
# Mueve la ficha de un issue en el tablero de Projects:   tablero.sh mover N "<Columna>"
# El tablero solo se maneja con `gh project` (GraphQL): en la nube da 403. Si cualquier paso
# falla, sale con 3 y dice qué ficha mover a mano; no es un error de la tarea. Los id del
# proyecto, del campo Status y de sus opciones se leen en cada uso, nunca de memoria.
set -u
TABLERO="${TABLERO:-JimiC}"
[ "${1:-}" = mover ] && [ $# -eq 3 ] && [[ "$2" =~ ^[1-9][0-9]*$ ]] && [ -n "$3" ] \
  || { echo "uso: tablero.sh mover N \"<Columna>\"" >&2; exit 64; }
N=$2
COL=$3
raiz=$(git rev-parse --show-toplevel) || exit 1
cd "$raiz" || exit 1
# shellcheck source=scripts/comun.sh
source scripts/comun.sh
R=$(repo_slug) || exit 1
OWNER=${R%%/*}
a_mano() { echo "tablero.sh: $1. Mueve a mano la ficha del #$N a «$COL»." >&2; exit 3; }

proyecto=$(gh project list --owner "$OWNER" --format json \
  --jq ".projects[] | select(.title==\"$TABLERO\") | \"\(.number) \(.id)\"" 2>/dev/null) || a_mano "gh project no responde"
[ "$(wc -l <<<"$proyecto")" -eq 1 ] && [ -n "$proyecto" ] || a_mano "no hay exactamente un tablero «$TABLERO»"
read -r numero pid <<<"$proyecto"

campo=$(gh project field-list "$numero" --owner "$OWNER" --format json \
  --jq ".fields[] | select(.name==\"Status\") | \"\(.id) \(.options[] | select(.name==\"$COL\") | .id)\"" 2>/dev/null) \
  || a_mano "no se pudo leer el campo Status"
read -r fid oid <<<"$campo"
[ -n "${oid:-}" ] || a_mano "la columna «$COL» no existe"

ficha() {
  gh project item-list "$numero" --owner "$OWNER" --format json --limit 500 \
    --jq ".items[] | select(.content.type==\"Issue\" and .content.number==$N and .content.repository==\"$R\") | .id" 2>/dev/null
}
item=$(ficha) || a_mano "no se pudo leer el tablero"
if [ -z "$item" ]; then
  # Si el añadido automático llegó antes («Content already exists»), se vuelve a leer.
  item=$(gh project item-add "$numero" --owner "$OWNER" --url "https://github.com/$R/issues/$N" --format json --jq .id 2>/dev/null) || item=$(ficha)
fi
[ -n "$item" ] || a_mano "la ficha del #$N no está en el tablero"
gh project item-edit --id "$item" --project-id "$pid" --field-id "$fid" --single-select-option-id "$oid" >/dev/null 2>&1 \
  || a_mano "no se pudo mover"
echo "tablero.sh: #$N en «$COL»"
