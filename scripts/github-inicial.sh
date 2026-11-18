#!/usr/bin/env bash
# Etiquetas mínimas y milestones del hito 0 y del Backlog. Idempotente: se puede repetir.
set -eu
raiz=$(git rev-parse --show-toplevel)
cd "$raiz"
# shellcheck source=scripts/comun.sh
source scripts/comun.sh
R=$(repo_slug)

etiqueta() { gh label create "$1" --repo "$R" --color "$2" --description "$3" --force; }
etiqueta tipo:tarea 1f6feb "Trabajo planificado con plan y PR"
etiqueta tipo:bug   1f6feb "Algo no funciona como debería"
etiqueta tipo:idea  1f6feb "Propuesta sin decidir"
etiqueta critica    d1242f "Tarea delicada: una decisión difícil de revertir o un concepto nuevo"

existentes=$(gh api "repos/$R/milestones?state=all" --jq '.[].title')
milestone() {
  if grep -qxF "$1" <<<"$existentes"; then echo "milestone «$1»: ya existe"; return; fi
  gh api -X POST "repos/$R/milestones" -f title="$1" -f description="$2" --jq '"milestone «\(.title)»: creado"'
}
milestone "Hito 0 — Entorno" "docs/specs/hito-0.md"
milestone "Backlog" "Pendiente sin hito"
