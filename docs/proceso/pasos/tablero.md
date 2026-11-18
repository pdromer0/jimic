# Pasos del módulo tablero

Los workflows del tablero mueven las fichas: issue nuevo → Backlog, PR enlazado → En revisión,
issue cerrado o PR fusionado → Hecho. Ningún evento de GitHub lleva una ficha a «En curso».

- **En §1, al aprobar el plan:** `scripts/tablero.sh mover N "En curso"`. Si sale con 3 (en la
  nube, siempre), sigue la tarea y di a Pedro qué ficha mover.
- **En §6, tras abrir el PR:** nada; si el workflow falló, `scripts/tablero.sh mover N "En revisión"`.
