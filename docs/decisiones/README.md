# Decisiones (ADR)

Un ADR registra una decisión difícil de revertir, que afecta a varias partes o con alternativas
razonables. Un ADR aceptado no se reescribe: si cambia, se escribe otro y el antiguo pasa a
`Sustituida por NNNN`. Nombre `NNNN-titulo.md`, con el número que figura en la columna ADR del
registro de `docs/diseno.md`; los ADR se numeran en orden y uno puede recoger varias decisiones.

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

- [0001. Adoptamos el método EDsim v2](0001-adoptamos-el-metodo-edsim.md) (decisiones 1, 41 y 48)
- [0002. Repositorio público sin nada personal](0002-repositorio-publico.md) (43, 50, 52 y 53)
- [0003. Comprobación de tipos con pyright](0003-pyright.md) (46)
- [0004. gitleaks contra los secretos](0004-gitleaks.md) (49)
