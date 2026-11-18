# Documentación de JimiC

| Qué | Dónde | Quién la mantiene |
| --- | --- | --- |
| Diseño: visión, modelo, hoja de ruta, QA y registro de decisiones | [diseno.md](diseno.md) | El chat redacta; entra por PR |
| Especificación de cada hito, con su anexo | `specs/hito-N.md` ([plantilla](specs/plantilla-hito.md)) | Fusionar su PR es aprobarla; cada tarea añade su entrada al anexo |
| Decisiones difíciles de revertir (ADR) | [decisiones/](decisiones/README.md) | Al cerrar el hito |
| Ciclo de una tarea y sus reglas | [proceso/tarea.md](proceso/tarea.md) | Por PR |
| Módulos del método activos | [proceso/modulos.md](proceso/modulos.md) | Una tarea de proceso por cambio |
| Lecciones transversales | [proceso/lecciones.md](proceso/lecciones.md) | Al cerrar cada tarea que enseñe algo |
| Respuestas del arranque | [proceso/arranque.md](proceso/arranque.md) | El chat, en las fases 0 y 1 |
| Aprendizaje | [diario/](diario/README.md) | El agente al cerrar; Pedro, sus reflexiones |
| Seguimiento | Issues, milestones y etiquetas de GitHub | Al fusionar |

Reglas: cada especificación lleva debajo del título `> **Estado:** …`; el issue resume y enlaza la
especificación, nunca la copia; las decisiones se citan «decisión 12 (H2-3)», sin almohadilla
(con ella GitHub enlaza el issue 12); cada cifra con su medida (fecha, máquina, commit); un
fichero de más de unos 50 KB se lee por secciones; los diagramas, en Mermaid.
