"""El mensaje del cierre pintado por el código (tarea 1.16, formato de CU-14), sin Telegram.

Datos del e08 inventado (`close_data.py`).
"""

from dataclasses import replace

from close_data import (
    PLAN,
    THURSDAY,
    completed_today,
    open_today,
    todo,
)
from heartbeat_data import MADRID

from jimic.channel.message import Outcome, expire, render, resolve
from jimic.close.day import CloseView, close_view
from jimic.close.message import (
    FIXED_NOTE,
    CloseContent,
    cut_note,
    paint,
)
from jimic.tools.todoist import TaskList

TOMORROW = "🔮 Mañana: calendario libre · 🏋️ Pierna · fuerza"
ICONS = {"comida": "🍽️", "cena": "🍽️", "paseo 5 km": "🚶"}


def e08(note: str | None = "Tres de siete y la comida antes de hora: ¡qué ansia! 😄", **changes):
    content = CloseContent(
        view=close_view(open_today(), completed_today(), [], THURSDAY),
        note=note,
        tomorrow=TOMORROW,
        plan=frozenset(task.id for task in PLAN),
        keys={},
    )
    return paint(replace(content, **changes), THURSDAY, MADRID, ICONS)


def test_el_e08_se_pinta_con_el_formato_de_la_especificacion():
    """Pilla una sección que falta, fuera de orden o con más de una línea de mañana."""
    view = render(e08(), 5)
    assert view.text.split("\n") == [
        "🌙 Cierre del día, Pedro",
        "",
        "✅ Hecho",
        "▫️ Meditación",
        "🍽️ Comida",
        "▫️ Trabajo en el proyecto",
        "",
        "⏳ Sin marcar",
        "1️⃣ ▫️ Revisar ofertas (14:30)",
        "2️⃣ ▫️ Proyecto personal (17:15)",
        "3️⃣ 🚶 Paseo 5 km (19:00)",
        "4️⃣ 🍽️ Cena (21:30)",
        "",
        "📈 Por ahora: 3 de 7 del plan",
        "🎯 Tres de siete y la comida antes de hora: ¡qué ansia! 😄",
        TOMORROW,
        "",
        "¿Cómo ha ido el día?",
    ]
    rows = [[button.label for button in row] for row in view.keyboard]
    assert rows == [
        ["1", "2", "3", "4", "5"],
        ["1 ✅ Hecha", "1 🗣️ La haré", "1 ➡️ Mañana"],
        ["2 ✅ Hecha", "2 🗣️ La haré", "2 ➡️ Mañana"],
        ["3 ✅ Hecha", "3 🗣️ La haré", "3 ➡️ Mañana"],
        ["4 ✅ Hecha", "4 🗣️ La haré", "4 ➡️ Mañana"],
        ["➡️ Todo lo pendiente a mañana"],
    ]


def test_cada_elemento_lleva_su_tarea_y_si_es_del_plan_en_sus_datos_y_no_en_el_texto():
    message = e08(plan=frozenset({"t-ofertas"}))
    data = [item.data for item in message.items[:-1]]
    assert data[0] == {
        "task_id": "t-ofertas",
        "day": "2030-01-10",
        "due": "2030-01-10T14:30:00+01:00",
        "in_plan": True,
    }
    assert [d["in_plan"] for d in data if d] == [True, False, False, False]


def test_la_cifra_sube_con_hecha_y_solo_con_las_del_plan():
    """Pilla una cifra que no sube o que cuenta una tarea que no es del plan (versión
    incorrecta 13)."""
    message = e08(plan=frozenset({"t-meditacion", "t-comida", "t-trabajo", "t-ofertas"}))
    assert "📈 Por ahora: 3 de 4 del plan" in render(message, 5).text
    message = resolve(message, {1: Outcome("done", "✅ hecha")})  # no es del plan
    assert "📈 Por ahora: 3 de 4 del plan" in render(message, 5).text
    message = resolve(message, {0: Outcome("done", "✅ hecha")})
    assert "📈 Por ahora: 4 de 4 del plan" in render(message, 5).text


def test_sin_plan_no_hay_cifra():
    """Pilla «0 de 0» o una cifra inventada sin plan."""
    assert "📈" not in render(e08(plan=None), 5).text
    assert "📈" not in render(e08(plan=frozenset()), 5).text


def test_una_tarea_clave_lleva_su_intencion_en_los_datos():
    message = e08(keys={"revisar ofertas": "ofertas"})
    assert (message.items[0].data or {})["intent"] == "ofertas"


def test_sin_nada_pendiente_solo_queda_la_valoracion():
    view = CloseView(("Comida",), (), frozenset({"t-comida"}))
    message = e08(view=view)
    text = render(message, 5).text
    assert "⏳ Sin marcar\n▫️ Nada pendiente: día redondo" in text
    assert [b.label for row in render(message, 5).keyboard for b in row] == list("12345")


def test_con_todoist_caido_lo_dice_y_sale_la_valoracion():
    view = close_view(TaskList.unavailable("timeout"), completed_today(), [], THURSDAY)
    text = render(e08(view=view), 5).text
    assert "⏳ Ahora no puedo leer tu Todoist" in text
    assert "✅ Hecho" not in text and text.endswith("¿Cómo ha ido el día?")


def test_mas_de_siete_sin_marcar_lleva_botones_siete_y_el_resto_se_cuenta():
    """Pilla un mensaje de nueve elementos (no cabe) o el resto perdido sin decirlo."""
    many = [todo(f"t-{n}", f"Tarea {n}", f"2030-01-10T{10 + n}:00") for n in range(9)]
    view = close_view(TaskList(available=True, tasks=many), completed_today(), [], THURSDAY)
    message = e08(view=view)
    assert len(message.items) == 8 and message.items[-1].question
    assert "⏳ Sin marcar (y 2 más en Todoist)" in render(message, 5).text


def test_la_nota_se_recorta_a_dos_lineas_sin_la_marca_repetida():
    """Pilla una nota sin recortar (versión incorrecta 12) o con «🎯 🎯»."""
    assert cut_note("🎯 Una.\n\nDos.\nTres.\nCuatro.") == "Una.\nDos."
    assert cut_note("x" * 300) == "x" * 199 + "…"
    assert cut_note("  \n ") is None
    assert cut_note(None) is None


def test_sin_nota_del_agente_sale_la_fija():
    assert f"🎯 {FIXED_NOTE}" in render(e08(note=None), 5).text


def test_al_caducar_lo_no_tocado_se_queda_y_la_valoracion_queda_sin_valorar():
    text = render(expire(e08()), 5).text
    assert "4️⃣ 🍽️ Cena (21:30) — sin tocar" in text
    assert text.endswith("¿Cómo ha ido el día? — sin valoración")
