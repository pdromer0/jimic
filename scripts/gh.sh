#!/usr/bin/env bash
# Todo lo que los agentes hacen con GitHub, por REST (funciona en local y en la nube, donde
# GraphQL da 403). Valida sus argumentos y solo hace lecturas, abrir un PR, editar su
# descripción y crear una Release en borrador: es lo único que .claude/settings.json deja pasar
# sin preguntar. Cualquier otro `gh api` que escriba el modelo pide permiso.
#
#   gh.sh issue N             cuerpo, estado, etiquetas y milestone del issue N
#   gh.sh comentarios N       comentarios del issue o PR N
#   gh.sh bloqueado-por N     issues que bloquean al N (dependencias nativas)
#   gh.sh pr N                estado del PR N (merged, punta, rama, issues que cierra)
#   gh.sh checks SHA          check runs del commit SHA
#   gh.sh revisiones N        revisiones y comentarios de línea del PR N
#   gh.sh abrir-pr RAMA TITULO FICHERO    abre el PR de RAMA contra main
#   gh.sh editar-pr N FICHERO             cambia la descripción del PR N
#   gh.sh crear-release ETIQUETA TITULO docs/releases/ETIQUETA.md   Release en borrador (v0.N-hitoN)
set -u
raiz=$(git rev-parse --show-toplevel) || exit 1
cd "$raiz" || exit 1
# shellcheck source=scripts/comun.sh
source scripts/comun.sh

uso() { sed -n '7,15p' "$0" >&2; exit 64; }
numero() { [[ "$1" =~ ^[1-9][0-9]{0,6}$ ]] || { echo "gh.sh: «$1» no es un número de issue o PR" >&2; exit 64; }; }
fichero() { [ -f "$1" ] || { echo "gh.sh: no existe el fichero «$1»" >&2; exit 64; }; }

[ $# -ge 1 ] || uso
R=$(repo_slug) || exit 1
orden=$1
shift
case "$orden" in
  issue)
    [ $# -eq 1 ] || uso; numero "$1"
    exec gh api "repos/$R/issues/$1" --jq '{number,title,state,es_pr:has("pull_request"),labels:[.labels[].name],milestone:.milestone.title,body}' ;;
  comentarios)
    [ $# -eq 1 ] || uso; numero "$1"
    exec gh api "repos/$R/issues/$1/comments" --paginate --jq '.[] | {user:.user.login,created_at,body}' ;;
  bloqueado-por)
    [ $# -eq 1 ] || uso; numero "$1"
    exec gh api "repos/$R/issues/$1/dependencies/blocked_by" --paginate --jq '.[] | {number,title,state}' ;;
  pr)
    [ $# -eq 1 ] || uso; numero "$1"
    exec gh api "repos/$R/pulls/$1" --jq '{number,state,merged,merge_commit_sha,head_ref:.head.ref,head_sha:.head.sha,closes:([(.body // "") | scan("(?i)\\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?):?\\s+#([0-9]+)") | .[0] | tonumber] | unique)}' ;;
  checks)
    [ $# -eq 1 ] || uso
    [[ "$1" =~ ^[0-9a-f]{7,40}$ ]] || { echo "gh.sh: «$1» no es un SHA" >&2; exit 64; }
    exec gh api "repos/$R/commits/$1/check-runs" --paginate --jq '.check_runs[] | {name,status,conclusion}' ;;
  revisiones)
    [ $# -eq 1 ] || uso; numero "$1"
    gh api "repos/$R/pulls/$1/reviews" --paginate --jq '.[] | {tipo:"revision",user:.user.login,state,body}' || exit 1
    exec gh api "repos/$R/pulls/$1/comments" --paginate --jq '.[] | {tipo:"linea",user:.user.login,path,line,body}' ;;
  abrir-pr)
    [ $# -eq 3 ] || uso
    [[ "$1" =~ ^(feat|fix|docs|refactor|test|chore|ci)/[A-Za-z0-9._/-]+$ ]] || { echo "gh.sh: rama «$1» sin prefijo permitido" >&2; exit 64; }
    [ -n "$2" ] || { echo "gh.sh: falta el título" >&2; exit 64; }
    fichero "$3"
    exec gh api -X POST "repos/$R/pulls" -f title="$2" -f head="$1" -f base=main -F body=@"$3" --jq '{number,html_url}' ;;
  editar-pr)
    [ $# -eq 2 ] || uso; numero "$1"; fichero "$2"
    exec gh api -X PATCH "repos/$R/pulls/$1" -F body=@"$2" --jq .html_url ;;
  crear-release)
    # Solo en borrador, y solo si la etiqueta ya está en GitHub (la crea Pedro tras la fusión del
    # cierre): nada se publica sin que Pedro lo revise y lo publique desde la web.
    [ $# -eq 3 ] || uso
    [[ "$1" =~ ^v0\.(0|[1-9][0-9]?)-hito(0|[1-9][0-9]?)$ ]] && [ "${BASH_REMATCH[1]}" = "${BASH_REMATCH[2]}" ] \
      || { echo "gh.sh: «$1» no es una etiqueta v0.N-hitoN" >&2; exit 64; }
    [ -n "$2" ] || { echo "gh.sh: falta el título" >&2; exit 64; }
    [ "$3" = "docs/releases/$1.md" ] || { echo "gh.sh: las notas de $1 son docs/releases/$1.md, no «$3»" >&2; exit 64; }
    fichero "$3"
    gh api "repos/$R/git/ref/tags/$1" --jq .ref >/dev/null \
      || { echo "gh.sh: la etiqueta $1 no está en GitHub o no se pudo comprobar (arriba, el error de gh); no se crea nada" >&2; exit 1; }
    ya=$(gh api "repos/$R/releases" --paginate --jq '.[] | select(.tag_name == "'"$1"'") | .id') \
      || { echo "gh.sh: no se pudo listar las Releases; no se crea nada" >&2; exit 1; }
    [ -z "$ya" ] || { echo "gh.sh: ya hay una Release con la etiqueta $1 (id $ya); no se crea otra" >&2; exit 1; }
    exec gh api -X POST "repos/$R/releases" -f tag_name="$1" -f name="$2" -F body=@"$3" -F draft=true --jq '{id,html_url,draft}' ;;
  *) uso ;;
esac
