# 0001. Adoptamos el método EDsim

- **Estado:** Aceptada
- **Fecha:** AAAA-MM-DD

## Contexto

JimiC se construye con agentes de IA y una persona que decide y fusiona. Hace falta un
entorno y un ciclo de trabajo probados desde el primer día.

## Decisión

Adoptamos el núcleo del método EDsim, versión <versión del paquete del método> (repositorio
pdromer0/edsim, commit <commit>). Los módulos se activan uno a uno cuando se cumple su
disparador, con una tarea de proceso cada uno (`docs/proceso/modulos.md`).

## Alternativas consideradas

- Montar todo lo que EDsim acumuló desde el día 0: descartado; mete coste y complejidad antes de
  tener el problema que resuelve cada pieza.

## Consecuencias

<Lo que se gana y lo que queda pendiente.>

## Desviaciones

Cada desviación del núcleo tiene su propio ADR, enlazado aquí. Ninguna por ahora.

## Qué cambió al implementarla

<Al cerrar el hito 0.>
