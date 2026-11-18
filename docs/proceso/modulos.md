# Módulos del método

> **Estado:** documento vivo. Activar o retirar un módulo es una tarea de proceso (serie P), un
> módulo por tarea: entra en `piloto`, y el cierre del hito decide si pasa a `activo` o a
> `retirado`. `scripts/disparadores.sh` lee esta tabla.

Estados: `inactivo`, `piloto`, `activo`, `retirado`. El catálogo, con cada disparador y su
señal de que sobra, viene con el método (`modulos/README.md`).

| Módulo | Disparador | Estado | Desde | PR |
| --- | --- | --- | --- | --- |
| sello-n2 | la rápida pasa de 60 s (mediana) | inactivo | — | — |
| ci-salto-docs | PR de solo documentación × duración del CI ≥ 30 min por ventana de 20 PR | inactivo | — | — |
| mutante | más de 2 versiones incorrectas por tarea, o el árbol se ensucia | inactivo | — | — |
| revisor-plan | primera tarea con la etiqueta critica | inactivo | — | — |
| revision-reforzada | llega a main un bug que una revisión externa habría visto | inactivo | — | — |
| tablero | más de 10 issues abiertos, o dos PR abiertos a la vez | piloto | 2026-11-18 | #33 |
| nube | la N3 local pasa de 10 min, o hace falta el portátil libre | inactivo | — | — |
| claude-en-pr | más de 3 ajustes triviales por hito que obligan a abrir sesión | inactivo | — | — |
| servicios-docker | aparece compose.yaml o un servicio | inactivo | — | — |
| notebooks | aparece notebooks/ | inactivo | — | — |
| app-workspace | aparece un segundo paquete (app/) | inactivo | — | — |
| xdist | la N3 pasa de 2 min (entonces se mide el criterio del 60 %) | inactivo | — | — |
| adr-y-releases | primera decisión difícil de revertir | inactivo | — | — |
| metricas-pr | empieza el hito 1 | piloto | 2026-11-18 | #34 |
| guardia | hace falta autorizar escrituras en GitHub sin preguntar | inactivo | — | — |
| datos-de-referencia | aparece config/ calibrada o tests/data/ | inactivo | — | — |
| dobles-con-tope | primer doble de prueba que el código llama en bucle | inactivo | — | — |
| comodidad-local | se pasan esperas de Claude Code sin enterarse | inactivo | — | — |
| migracion-docs | hay documentación del proyecto fuera del repositorio | inactivo | — | — |
| diagnostico | cambian las instrucciones de los agentes | inactivo | — | — |
