# Funciones comunes de los scripts del método. Se cargan con `source`, desde la raíz del
# repositorio. Sin red y sin escribir en .git.

CACHE_DIR=.cache/metodo

# owner/repo de GitHub. METODO_REPO manda; si no, las dos últimas partes de la URL de origin
# (vale para https, ssh y los proxies de las sesiones en la nube, que acaban en owner/repo).
repo_slug() {
  if [ -n "${METODO_REPO:-}" ]; then
    printf '%s\n' "$METODO_REPO"
    return 0
  fi
  local url
  url=$(git remote get-url origin 2>/dev/null) || {
    echo "comun.sh: no hay remoto origin; define METODO_REPO=owner/repo" >&2
    return 1
  }
  url=${url%.git}
  url=${url%/}
  url=${url//://}
  printf '%s/%s\n' "$(basename "$(dirname "$url")")" "$(basename "$url")"
}

# Apunta una medida en .cache/metodo/tiempos.tsv: fecha, nivel, segundos y código de salida.
# La leen scripts/disparadores.sh y la tarea de cierre del hito.
apunta_tiempo() {
  mkdir -p "$CACHE_DIR" || return 0
  printf '%s\t%s\t%s\t%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$3" >>"$CACHE_DIR/tiempos.tsv"
}
