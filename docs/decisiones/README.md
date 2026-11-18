# Decisiones (ADR)

Un ADR registra una decisión difícil de revertir, que afecta a varias partes o con alternativas
razonables. Un ADR aceptado no se reescribe: si cambia, se escribe otro y el antiguo pasa a
`Sustituida por NNNN`. Nombre `NNNN-titulo.md`, con el número de su fila en el registro de
`docs/diseno.md`.

## Plantilla

```markdown
# NNNN. Título

- **Estado:** Propuesta | Aceptada | Sustituida por NNNN
- **Fecha:** AAAA-MM-DD

## Contexto
## Decisión
## Alternativas consideradas
Cada una: por qué no, y cuándo tendría sentido.
## Consecuencias
## Qué cambió al implementarla
Cada cambio con su tarea y su commit, o «Nada».
```

## Índice

- [0001. Adoptamos el método EDsim](0001-adoptamos-el-metodo-edsim.md)
