---
name: cerrar-hito
description: Cierra un hito de JimiC desde el issue de su tarea de cierre: diario, balance con los disparadores de los módulos, ADR, notas de la Release, lecciones y portada.
argument-hint: "N [notas efímeras de esta sesión]"
disable-model-invocation: true
---

# /cerrar-hito

N es el número del **issue** de la tarea de cierre. El cierre es una tarea más: sigue entera
[la skill `/tarea-jimic`](../tarea-jimic/SKILL.md) con este N. Esto añade lo que se escribe al cerrar.
En local: necesita GitHub para el balance. Cada cifra sale de un comando o del anexo, nunca de
memoria; si dos fuentes no coinciden, para y pregunta.

## Lo que lleva el plan

1. Las decisiones del hito y dónde quedan (registro de `docs/diseno.md`), y las que se
   **proponen** como ADR ([paso `adr-y-releases`](../../../docs/proceso/pasos/adr-y-releases.md)):
   Pedro elige cuáles se escriben.
2. Lo que el anexo dejó para el cierre (`grep -n` con el número de la tarea de cierre).
3. La salida de `scripts/disparadores.sh --github`: disparadores cumplidos y preguntas para
   Pedro. Cada módulo que se proponga activar será una tarea de proceso aparte; no se
   activa en el cierre.
4. Los conceptos de «Pedro sabe explicar», de los criterios de aceptación del hito.

## Lo que se escribe (un commit por bloque)

1. **Especificación del hito**: `## Balance del hito` antes del anexo (qué se entregó y dónde;
   PR, commits y tiempos calculados con un script; disparadores y propuestas, sin implantar) y la
   cabecera de estado `cerrado (v0.N-hitoN)`.
2. **Diseño**: el registro de decisiones y la hoja de ruta.
3. **ADR**: los que Pedro eligió en el plan y el «Qué cambió al implementarla» de los del hito;
   el índice de `docs/decisiones/README.md`, al día.
4. **Notas de la Release** `docs/releases/v0.N-hitoN.md`, según el paso `adr-y-releases`.
5. **Diario** `docs/diario/hito-N.md`: una entrada por tarea, «Pedro sabe explicar» y
   «Reflexiones de Pedro», vacía.
6. **Lecciones** nuevas en `docs/proceso/lecciones.md`, con la tarea entre paréntesis.
7. **Portada**: «Hito en curso» y «Último hito cerrado» en `AGENTS.md`; el estado en `README.md`.

Si la rama solo toca documentación, sin N3, con permiso de Pedro.

## Tras la fusión

1. Pedro crea la etiqueta `v0.N-hitoN` sobre la fusión del cierre y la sube.
2. En la sesión de `/sincronizar` del PR de cierre, cuando Pedro la pida, la **Release en
   borrador**: `scripts/gh.sh crear-release v0.N-hitoN "Hito N — …" docs/releases/v0.N-hitoN.md`.
   Se niega sin la etiqueta en GitHub.
3. Pedro revisa el borrador y lo publica a mano desde la web.
