# Sesión de configuración de JimiC

Esta carpeta guarda los ficheros personales de JimiC (`intenciones.yaml`, `limites.yaml`,
`rutina.md`, `objetivos.md` y `preferencias.md`). Aquí se revisan y se editan con ayuda;
`/configurar-jimic` guía. Este fichero lo reescribe `jimic config sesion` en cada arranque.

## Reglas de la sesión

- **Nunca leas ni enseñes** el `.env` ni `google_cliente.json` ni `google_token.json`, de esta
  copia ni de la otra: lo que lees entra en la conversación. Si algo depende de ellos, pregunta.
- **Nada sale de esta carpeta:** sin web, sin red, sin `git push`, `gh` ni conectores MCP. Los
  datos de JimiC (`~/.local/share/jimic*`) tampoco se leen. Si un comando se
  deniega, no busques otro camino: dilo.
- **Cada cambio:** enséñalo, espera el OK, edita y valida con `{jimic} config check`. Usa ese
  comando tal cual: dentro del sandbox, `uv run` no puede escribir su caché.
- No copies datos personales fuera de los ficheros que se están editando.
- Si Claude Code pide permiso para acceder a la red, la sesión no se abrió con
  `jimic config sesion`: recomienda denegarlo y abrirla así.
