# Pasos del módulo tablero

Los workflows del tablero mueven las fichas, que son solo issues (el auto-add filtra `is:issue`):
issue nuevo → Backlog, PR enlazado → En revisión, issue cerrado → Hecho (al fusionar un PR con
«Closes #N»). Ningún evento de GitHub lleva una ficha a «En curso».

- **Con el plan aprobado (tras §2, antes de §3):** `scripts/tablero.sh mover N "En curso"`. Si
  sale con 3 (en la nube, siempre), sigue la tarea y di a Pedro qué ficha mover.
- **En §6, tras abrir el PR:** nada; si el workflow falló, `scripts/tablero.sh mover N "En revisión"`.
