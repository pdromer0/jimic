---
name: configurar-jimic
description: Revisa y edita los ficheros personales de JimiC en esta carpeta, con los límites de la sesión y validando con jimic config check.
---

# /configurar-jimic

## 1. Explica los límites

Al empezar, cuenta en pocas líneas qué puede hacer esta sesión y qué no:

- **Puede** leer y editar los ficheros personales de esta carpeta (cada edición pide permiso) y
  ejecutar `{jimic} config check`.
- **No puede** leer ni escribir el `.env` ni los ficheros de Google (de esta copia ni de la
  otra), leer los datos de JimiC (la base de datos, los logs, las transcripciones), hacer
  `git push`, usar `gh` ni conectores MCP, buscar en la web ni salir a la red. Lo frenan dos capas: las reglas
  `deny` de Claude Code y el sandbox (bubblewrap), sin el que la sesión no arranca.
- El `.claude/` de esta carpeta lo reescribe `jimic config sesion` cada vez: lo que se cambie ahí
  a mano se pierde.

## 2. Valida

Lanza `{jimic} config check` y resume el resultado: qué fichero falla, en qué línea y por qué. La
salida no enseña el contenido de los ficheros; tu resumen tampoco.

## 3. Pregunta qué quiere cambiar

Si hay errores, propón empezar por ellos. Si no, pregunta qué quiere revisar o cambiar.

## 4. Cada cambio, como en el chat

1. Enseña el cambio (solo las líneas que cambian, antes y después) y por qué.
2. Espera el OK.
3. Edita.
4. Valida con `{jimic} config check`. Si falla, enseña el error y propón el arreglo.

## 5. Más insistente o más caro: confirmación explícita

Aquí no rige la regla asimétrica del agente (son cambios a mano, con ayuda). Pero si un cambio
hace a JimiC más insistente o más caro, **avísalo** y pide una confirmación explícita («sí, más
insistente» o «sí, más caro») antes de editar. Por ejemplo:

- `limites.yaml`: subir `max_messages_per_day`, `max_insist_per_task`, `max_deletions_per_day`
  o cualquier valor de `budget`; acortar `quiet_hours` o `quiet_while_chatting`; poner
  `silent_during_events: false`; pasar una herramienta de `blocked` a `free`.
- `intenciones.yaml`: en una tarea clave, bajar `spacing` o `grace`, o subir `max_per_day`.
