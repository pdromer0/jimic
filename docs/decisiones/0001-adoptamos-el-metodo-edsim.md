# 0001. Adoptamos el método EDsim v2

- **Estado:** Aceptada
- **Fecha:** 2026-11-17

## Contexto

JimiC se construye con agentes de IA y una persona que decide y fusiona. Hace falta un
entorno y un ciclo de trabajo probados desde el primer día. El objetivo es doble, producto y
aprendizaje (decisión 41), con un criterio de Pedro: la metodología y las tecnologías se acercan
lo máximo a lo que exigiría una empresa real, y cada desviación se expone con sus alternativas.

## Decisión

Adoptamos el núcleo del método EDsim, versión 2 (15-nov-2026; repositorio `pdromer0/edsim`,
`main`, commit `916ed1e`), con sus nueve decisiones de fondo sin cambios (decisión 48): la
persona decide y el agente implementa; se usan las librerías; GitHub Flow; idiomas por zona;
tests por niveles; el repositorio es la fuente de verdad; cada regla en un solo sitio; la lógica
determinista en scripts con tests; una novedad del método por tarea. Los módulos se activan uno
a uno cuando se cumple su disparador, con una tarea de proceso cada uno
(`docs/proceso/modulos.md`). Recoge las decisiones 1, 41 y 48 de `docs/diseno.md`.

## Alternativas consideradas

- Montar todo lo que EDsim acumuló desde el día 0: descartado; mete coste y complejidad antes de
  tener el problema que resuelve cada pieza. Tendría sentido si JimiC heredara ya los problemas
  de EDsim (varios agentes en paralelo, servicios, notebooks).
- Solo producto, sin el componente didáctico: más rápido, pero obligaría a quitar «Pedro sabe
  explicar…» de los criterios de cada hito (decisión 41). Tendría sentido si JimiC fuera solo
  una herramienta.

## Consecuencias

- Se gana un ciclo probado desde la primera tarea: plan aprobado, rama, N1 a N3, revisión propia,
  CI obligatorio y fusión por Pedro con *squash*.
- El método crece con los disparadores de `scripts/disparadores.sh` en cada cierre, no a ojo.
- Comparado con una empresa, solo difiere la aprobación del PR por otra persona, que aquí cubre
  el chat (decisión 48).
- Queda pendiente: los módulos previstos para el H1 (sección 12.4 del diseño).

## Desviaciones

Cada desviación del núcleo tiene su propio ADR. Todas **añaden** piezas; ninguna cambia una
decisión de fondo:

- [0002. Repositorio público sin nada personal](0002-repositorio-publico.md)
- [0003. Comprobación de tipos con pyright](0003-pyright.md)
- [0004. gitleaks contra los secretos](0004-gitleaks.md)

## Qué cambió al implementarla

- Fase 2 (102abff): `uv init --package` dentro de la carpeta ya creada, en lugar de crearla con
  uv (`arranque.md`, fase 2).
- Fase 6 (#4 y #5, PR #7): la prueba en seco se hizo **reducida**, por decisión de Pedro: solo
  `/tarea-jimic` sobre la 6b con la 6a abierta; se omitieron la prueba con el número de un PR,
  `/sincronizar` con un PR abierto y la del formulario web de las plantillas (`arranque.md`,
  fase 6).
