# Pasos del módulo adr-y-releases (en /cerrar-hito)

> Parche local de la plantilla del método (P4): la Release pasa por `scripts/gh.sh` y sale en
> borrador, y los ADR que faltan se proponen y Pedro elige (nota P4-a del #24).

- **En el plan del cierre, ADR propuestos:** de las decisiones del hito (registro de
  `docs/diseno.md`), las que cumplen los criterios de `docs/decisiones/README.md` y aún no
  tienen ADR, con una línea de motivo cada una. Son una propuesta: Pedro decide cuáles se
  escriben.
- **ADR completos:** uno por cada decisión que Pedro elija, con su contexto, las alternativas
  descartadas y cuándo tendrían sentido, y «Qué cambió al implementarla» con la tarea y el commit
  de cada cambio. Los ADR que ya escribió una tarea del hito se ponen al día en esa sección. Su
  número es el de la columna ADR del registro; el índice del README, al día.
- **Notas de la Release** en `docs/releases/v0.N-hitoN.md`, en español y sin nada personal: qué
  hace JimiC al cerrar el hito, las decisiones con sus ADR, el balance y lo que queda en el
  Backlog. Los enlaces, absolutos y fijados a la etiqueta
  (`https://github.com/pdromer0/jimic/blob/v0.N-hitoN/docs/specs/hito-N.md`): en una Release los
  relativos se rompen. Se revisan en el PR de cierre.
- **Release, tras la fusión y la etiqueta de Pedro**, en la sesión de `/sincronizar` del PR de
  cierre, cuando Pedro la pida, y en local (desde la nube o el chat GitHub puede responder 403):
  `scripts/gh.sh crear-release v0.N-hitoN "Hito N — …" docs/releases/v0.N-hitoN.md`. La crea
  **en borrador** y se niega si la etiqueta no está en GitHub o si ya hay una Release con ella.
  Pedro la revisa y la publica a mano desde la web.
