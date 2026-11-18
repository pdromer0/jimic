#!/usr/bin/env bash
# Tras la fusión del PR N: comprueba que está fusionado, pone main al día, borra la rama local
# si todos sus commits entraron y avisa de lo que queda. Nunca hace push ni borra ramas
# remotas. Códigos: 0 bien; 2 árbol sucio; 3 PR sin fusionar; 4 la fusión no está en main;
# 5 la rama local tiene commits que no entraron; 64 uso.
set -u
[ $# -eq 1 ] && [[ "$1" =~ ^[1-9][0-9]*$ ]] || { echo "uso: sincronizar.sh N_DEL_PR" >&2; exit 64; }
N=$1
raiz=$(git rev-parse --show-toplevel) || exit 1
cd "$raiz" || exit 1

if [ -n "$(git status --porcelain)" ]; then
  echo "sincronizar.sh: el árbol tiene cambios; no se toca nada." >&2
  git status --short >&2
  exit 2
fi
inicial=$(git branch --show-current)

pr=$(scripts/gh.sh pr "$N") || exit 1
if [ "$(jq -r .merged <<<"$pr")" != "true" ]; then
  echo "sincronizar.sh: el PR #$N no está fusionado; no se toca nada." >&2
  exit 3
fi
fusion=$(jq -r .merge_commit_sha <<<"$pr")
rama=$(jq -r .head_ref <<<"$pr")
punta_pr=$(jq -r .head_sha <<<"$pr")

antes=$(git rev-parse main)
git switch -q main && git pull -q --ff-only origin main || exit 1
if ! git merge-base --is-ancestor "$fusion" HEAD; then
  echo "sincronizar.sh: la fusión $fusion no está en main." >&2
  exit 4
fi

if git show-ref --verify -q "refs/heads/$rama"; then
  local_punta=$(git rev-parse "refs/heads/$rama")
  git fetch -q origin "pull/$N/head" || exit 1
  if git merge-base --is-ancestor "$local_punta" "$punta_pr"; then
    git branch -q -D "$rama"
    echo "rama local $rama: borrada"
  else
    echo "sincronizar.sh: la rama $rama tiene commits que no entraron:" >&2
    git --no-pager log --oneline "$punta_pr..refs/heads/$rama" >&2
    exit 5
  fi
else
  echo "rama local $rama: no existía"
fi
git fetch -q --prune

if [ -n "$(git diff --name-only "$antes" HEAD -- uv.lock pyproject.toml)" ]; then
  echo "DEPENDENCIAS: cambiaron uv.lock o pyproject.toml; hace falta uv sync."
fi
for issue in $(jq -r '.closes[]' <<<"$pr"); do
  estado=$(scripts/gh.sh issue "$issue" | jq -r .state)
  echo "issue #$issue: $estado"
done
git --no-pager log --oneline -3
if [ -n "$inicial" ] && [ "$inicial" != main ] && [ "$inicial" != "$rama" ]; then
  echo "AVISO: arrancaste en $inicial; vuelve con git switch $inicial"
fi
