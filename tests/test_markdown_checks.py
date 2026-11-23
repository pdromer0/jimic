"""Estructura de rutina, objetivos y preferencias: se comprueba la forma, nunca el contenido."""

from jimic.memory.markdown_checks import (
    Section,
    check_objectives,
    check_preferences,
    check_routine,
    outline,
)
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


def test_un_titulo_con_almohadillas_de_cierre_cuenta():
    """Pilla «## Comidas ##», Markdown válido, tomado por otra sección; «C#» no se recorta."""
    text = ROUTINE.replace("## Comidas", "## Comidas ##").replace("## Jornada", "## Jornada #")
    assert check_routine(text + "## C#\n##\n") == []
    assert check_preferences(PREFERENCES.replace("## Iconos", "## Iconos ##")) == []


def test_un_titulo_dentro_de_un_bloque_de_codigo_no_cuenta():
    text = ROUTINE.replace("## Comidas", "```\n## Comidas\n```")
    assert check_routine(text) == [Problem(None, "falta la sección «Comidas»")]


def test_un_bloque_de_codigo_solo_lo_cierra_una_valla_igual_o_mas_larga():
    """Pilla cerrar un bloque de cuatro comillas con una de tres: lo de después se perdería."""
    text = "````\n```\n## Comidas\n```\n````\n" + ROUTINE.replace("## Comidas\n", "")
    assert check_routine(text) == [Problem(None, "falta la sección «Comidas»")]
    text = ROUTINE.replace("## Comidas", "~~~ texto\n## Comidas\n```\n~~~")
    assert check_routine(text) == [Problem(None, "falta la sección «Comidas»")]


def test_codigo_en_linea_al_principio_no_abre_un_bloque():
    """Pilla tomar «```x``` texto» por una valla: el resto del fichero no se miraría."""
    assert check_routine("```x``` texto\n" + ROUTINE) == []
    assert check_objectives("```x``` texto\n- sin etiqueta\n") == [
        Problem(2, "objetivo sin [medible], [declarado] ni [enlazado]")
    ]


def test_lo_que_hay_dentro_de_un_comentario_html_no_cuenta():
    """Pilla un ejemplo comentado tomado por un objetivo sin etiqueta."""
    text = "<!--\n- ejemplo sin etiqueta\n-->\n<!-- una línea -->\n- tampoco\n"
    assert check_objectives(text) == [
        Problem(5, "objetivo sin [medible], [declarado] ni [enlazado]")
    ]


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


def test_outline_parte_por_secciones_de_nivel_2_sin_comentarios_ni_codigo():
    text = (
        "# Título\nantes\n\n## Uno\n<!-- pista -->\nlínea\n### Sub\nmás\n"
        "```\n## No es sección\n```\n## Dos\n"
    )

    sections = outline(text)

    assert [section.title for section in sections] == [None, None, "Uno", "Dos"]
    assert [line for _, line in sections[1].body] == ["antes", ""]
    assert [line for _, line in sections[2].body] == ["línea", "### Sub", "más"]
    assert sections[3].body == ()


def test_outline_de_un_texto_vacio_es_una_seccion_sin_titulo():
    assert outline("") == [Section(None, None, ())]
