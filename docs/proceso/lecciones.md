# Lecciones transversales

> **Estado:** documento vivo. Cada lección dice qué se aprendió y, entre paréntesis, dónde.

## Tests y verificación

- Los errores de los planes están en lo que interpretan, no en la aritmética: los tests nacen de
  la misma interpretación y no los ven (EDsim, hito 5).
- Un valor esperado calculado con la función probada hereda su error: se calcula con código
  independiente y se ejecuta (EDsim).
- Una predicción de «pasa» falla más que una de «falla»: cada versión incorrecta dice las dos.
- Un registro que crece en un doble de prueba llenó 7,5 GB en un minuto y colgó WSL: tope en los
  dobles, límite por test y `memory` en `.wslconfig` (EDsim, tarea 7.5).

## Proceso y agentes

- Toda regla de repetición dice cuándo: «repite la N3» se leyó como «tras cada corrección»
  (EDsim, hito 5).
- Una sola N3, después de la revisión propia: la que corre en paralelo casi nunca es la que sube
  (EDsim, ADR 0083).
- Un dato del plan puede perderse al ejecutarlo: prueba en seco antes del primer uso real (EDsim, P.2).
- Lo que vale más allá de una sesión va al issue, como «Notas del chat», no como argumento.
- Afirmaciones de un modelo sobre versiones, URLs o APIs: se verifican con una herramienta.
