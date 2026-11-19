"""Estructura de rutina, objetivos y preferencias: se comprueba la forma, nunca el contenido."""

from jimic.memory.markdown_checks import check_objectives, check_preferences, check_routine
from jimic.memory.problems import Problem

ROUTINE = """\
# Rutina inventada

## Horario base
Me levanto a las 7:00.

## Comidas
Como a las 14:00.

## Jornada
De 9:00 a 17:00.

## Fines de semana
Más tarde.

## Otra sección libre
"""

OBJECTIVES = """\
# Objetivos inventados

## Entrenamiento
Meta: entrenar con constancia
Prioridad: alta
- [medible] 3 entrenos por semana, según APEX
  - un detalle libre en un subelemento
* [enlazado] Los objetivos de fuerza de APEX
1. [declarado] Estirar más

---
* * *

## Social
Prioridad: ahora no
"""

PREFERENCES = """\
# Preferencias inventadas

- Nada de mensajes largos.

## Iconos

| Tarea | Icono |
| :---: | --- |
| Gimnasio | 🏋️ |
| Barra \\| vertical | 📏 |

## Otra
| una | dos | tres |
"""


def test_una_rutina_con_sus_cuatro_secciones_es_valida():
    assert check_routine(ROUTINE) == []


def test_una_rutina_sin_una_seccion_o_con_una_repetida_se_senala():
    """Pilla que la sección se busque por un título parecido (otro nivel u otra caja)."""
    text = ROUTINE.replace("## Comidas", "### Comidas").replace("## Jornada", "## jornada")
    text += "\n## Horario base\n"
    assert check_routine(text) == [
        Problem(17, "la sección «Horario base» está repetida"),
        Problem(None, "falta la sección «Comidas»"),
        Problem(None, "falta la sección «Jornada»"),
    ]


def test_un_titulo_dentro_de_un_bloque_de_codigo_no_cuenta():
    text = ROUTINE.replace("## Comidas", "```\n## Comidas\n```")
    assert check_routine(text) == [Problem(None, "falta la sección «Comidas»")]


def test_unos_objetivos_con_sus_etiquetas_son_validos():
    """Subelementos, `Meta:`, `Prioridad:` y separadores no son objetivos."""
    assert check_objectives(OBJECTIVES) == []


def test_un_objetivo_de_primer_nivel_sin_etiqueta_se_senala_con_cualquier_marcador():
    text = OBJECTIVES + "- Quedar más\n+ [medible]\n2) sin etiqueta\n- \n-\n"
    reason = "objetivo sin [medible], [declarado] ni [enlazado]"
    assert check_objectives(text) == [Problem(n, reason) for n in (16, 18, 19, 20)]


def test_unas_preferencias_con_su_tabla_de_dos_columnas_son_validas():
    """La fila separadora no cuenta y `\\|` no parte una celda; otra sección es libre."""
    assert check_preferences(PREFERENCES) == []


def test_sin_seccion_de_iconos_o_sin_tabla_se_senala():
    assert check_preferences("# Preferencias\n\n- Algo\n") == [
        Problem(None, "falta la sección «Iconos»")
    ]
    assert check_preferences("## Iconos\n\nSin tabla.\n## Otra\n| a | b |\n") == [
        Problem(1, "la sección «Iconos» no tiene tabla")
    ]


def test_una_fila_de_iconos_con_otras_columnas_o_vacia_se_senala():
    text = PREFERENCES.replace("| Gimnasio | 🏋️ |", "| Gimnasio | 🏋️ | extra |")
    text = text.replace("| Barra \\| vertical | 📏 |", "| Barra |  |")
    assert check_preferences(text) == [
        Problem(9, "la fila de iconos no tiene dos columnas"),
        Problem(10, "la fila de iconos tiene una celda vacía"),
    ]
