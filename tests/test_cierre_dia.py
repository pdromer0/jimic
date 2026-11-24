"""Lo que enseña el cierre y dónde deja una tarea [➡️ Mañana] (tarea 1.16), sin reloj ni I/O.

Datos del e08 inventado (`close_data.py`): un jueves a las 21:30.
"""

from datetime import date, datetime, timedelta

import pytest
from close_data import (
    FRIDAY,
    OFFERS,
    THURSDAY,
    WALK,
    apex_e08,
    at,
    completed_today,
    free_calendar,
    open_today,
    todo,
    tomorrow_tasks,
)
from heartbeat_data import MADRID, all_day, calendar_with, event

from jimic.close.day import (
    KeyDone,
    busy_spans,
    close_view,
    next_free,
    tomorrow_due,
    tomorrow_line,
)
from jimic.tools.apex_schema import ApexSnapshot
from jimic.tools.calendar import CalendarDay
from jimic.tools.todoist import TaskList

# --- Lo hecho y lo sin marcar ----------------------------------------------------------------


def test_el_e08_hecho_son_las_tres_completadas_y_sin_marcar_las_cuatro_abiertas_por_hora():
    """Pilla lo completado sin enseñar, lo abierto fuera de orden o una completada entre lo
    pendiente."""
    view = close_view(open_today(), completed_today(), [], THURSDAY)
    assert view.done == ("Meditación", "Comida", "Trabajo en el proyecto")
    assert [t.id for t in view.pending] == ["t-ofertas", "t-proyecto", "t-paseo", "t-cena"]
    assert view.done_ids == {"t-meditacion", "t-comida", "t-trabajo"}
    assert view.marked_at["t-comida"] == at("2030-01-10T13:20")


def test_sin_marcar_son_solo_las_de_hoy_no_las_atrasadas():
    """Pilla una atrasada de ayer en «Sin marcar»: el cierre es del día, el plan ve lo demás."""
    overdue = todo("t-ayer", "Estiramientos", "2030-01-09T21:00")
    tasks = TaskList(available=True, tasks=[overdue, *open_today().tasks])
    view = close_view(tasks, completed_today(), [], THURSDAY)
    assert "t-ayer" not in [t.id for t in view.pending]


def test_una_tarea_clave_hecha_por_apex_sale_hecha_y_su_tarea_no_queda_sin_marcar():
    """Pilla el gimnasio hecho por APEX que sigue en «Sin marcar» (la tarea sigue abierta en
    Todoist) o que no sale en «Hecho»."""
    gym = todo("t-gimnasio", "Gimnasio", "2030-01-10T12:00", minutes=75)
    tasks = TaskList(available=True, tasks=[gym, *open_today().tasks])
    view = close_view(
        tasks, completed_today(), [KeyDone("Gimnasio", frozenset({gym.id}))], THURSDAY
    )
    assert view.done[-1] == "Gimnasio"
    assert "t-gimnasio" not in [t.id for t in view.pending]
    assert "t-gimnasio" in view.done_ids


def test_una_tarea_clave_ya_completada_en_todoist_no_se_repite():
    """Pilla «Gimnasio» dos veces en «Hecho»: la completada y la marca del botón."""
    gym = todo("t-gimnasio", "Gimnasio", "2030-01-10T12:00", done_at="2030-01-10T13:30")
    completed = TaskList(available=True, tasks=[gym])
    view = close_view(open_today(), completed, [KeyDone("gimnasio")], THURSDAY)
    assert view.done == ("Gimnasio",)


def test_con_todoist_caido_no_hay_nada_que_enseñar_y_lo_dice():
    view = close_view(TaskList.unavailable("timeout"), completed_today(), [], THURSDAY)
    assert view.tasks_down and view.pending == () and view.done == ()


def test_con_las_completadas_caidas_lo_abierto_sigue_saliendo():
    view = close_view(open_today(), TaskList.unavailable("timeout"), [], THURSDAY)
    assert view.done == () and len(view.pending) == 4


# --- [➡️ Mañana] -----------------------------------------------------------------------------


def test_e08_el_paseo_y_las_ofertas_van_a_su_misma_hora_de_manana():
    """Valores a mano: 19:00 → 19:00; 14:30 (30 min) → 14:30, porque el gimnasio de 75 min acaba
    a las 14:15. Pilla un movimiento a otra hora sin choque, o al día equivocado."""
    calendar, tasks = free_calendar(), tomorrow_tasks()
    assert tomorrow_due(WALK, THURSDAY, calendar, tasks) == at("2030-01-11T19:00")
    assert tomorrow_due(OFFERS, THURSDAY, calendar, tasks) == at("2030-01-11T14:30")


def test_no_pisa_el_gimnasio_de_las_13_00():
    """Valor a mano: 13:30 (30 min) choca con el gimnasio de 13:00 a 14:15 y va a las 14:15.
    Pilla un [Mañana] que no mira lo que ya hay mañana (versión incorrecta 7)."""
    task = todo("t-llamar", "Llamar al seguro", "2030-01-10T13:30", minutes=30)
    assert tomorrow_due(task, THURSDAY, free_calendar(), tomorrow_tasks()) == at("2030-01-11T14:15")


@pytest.mark.parametrize(
    ("start", "minutes", "expected"),
    [
        ("12:30", 30, "12:30"),  # contigua: acaba cuando empieza el gimnasio
        ("12:45", 30, "14:15"),  # se mete en el gimnasio
        ("13:00", None, "14:15"),  # sin duración ocupa 15 minutos
        ("14:15", 30, "14:15"),  # empieza cuando acaba
    ],
)
def test_los_bordes_son_semiabiertos(start, minutes, expected):
    """Valores a mano. Pilla intervalos cerrados (14:15 no choca con lo que acaba a las 14:15)."""
    task = todo("t-x", "Tarea inventada", f"2030-01-10T{start}", minutes=minutes)
    due = tomorrow_due(task, THURSDAY, free_calendar(), tomorrow_tasks())
    assert due == at(f"2030-01-11T{expected}")


def test_salta_de_choque_en_choque_y_mira_tambien_el_calendario():
    """Valor a mano: con un evento de 14:15 a 15:00, 13:30 → 15:00. Pilla un salto que solo mira
    el primer choque, o el calendario sin mirar."""
    task = todo("t-llamar", "Llamar al seguro", "2030-01-10T13:30", minutes=30)
    calendar = calendar_with(event(at("2030-01-11T14:15"), minutes=45))
    assert tomorrow_due(task, THURSDAY, calendar, tomorrow_tasks()) == at("2030-01-11T15:00")


def test_un_evento_rechazado_o_de_dia_entero_no_ocupa():
    task = todo("t-llamar", "Llamar al seguro", "2030-01-10T10:00", minutes=30)
    calendar = calendar_with(
        event(at("2030-01-11T10:00"), declined=True), all_day(FRIDAY, "Fiesta inventada")
    )
    due = tomorrow_due(task, THURSDAY, calendar, TaskList(available=True))
    assert due == at("2030-01-11T10:00")


def test_sin_hueco_antes_de_medianoche_va_a_manana_sin_hora():
    """Valor a mano: 23:30 con 23:00–24:00 ocupado no cabe. Pilla un bucle o el día siguiente."""
    task = todo("t-tarde", "Leer", "2030-01-10T23:30", minutes=30)
    calendar = calendar_with(event(at("2030-01-11T23:00"), minutes=60))
    assert tomorrow_due(task, THURSDAY, calendar, TaskList(available=True)) == FRIDAY


def test_sin_hora_va_a_manana_sin_hora():
    task = todo("t-llamar", "Llamar al seguro", "2030-01-10")
    assert tomorrow_due(task, THURSDAY, free_calendar(), tomorrow_tasks()) == FRIDAY


def test_la_tarea_que_se_mueve_no_choca_consigo_misma():
    """Pilla una segunda pasada (la tarea ya está mañana) que la empuja detrás de sí misma."""
    moved = todo("t-paseo", "Paseo 5 km", "2030-01-11T19:00")
    tasks = TaskList(available=True, tasks=[moved])
    assert tomorrow_due(WALK, THURSDAY, free_calendar(), tasks) == at("2030-01-11T19:00")


def test_dos_movidas_a_la_misma_hora_no_se_pisan():
    """La global: la segunda ve la primera ya en mañana y salta tras ella."""
    first = todo("t-a", "Primera", "2030-01-11T18:00", minutes=30)
    second = todo("t-b", "Segunda", "2030-01-10T18:00", minutes=30)
    tasks = TaskList(available=True, tasks=[first])
    assert tomorrow_due(second, THURSDAY, free_calendar(), tasks) == at("2030-01-11T18:30")


def test_el_cambio_de_hora_no_mueve_la_hora_de_reloj():
    """Del sábado 30 al domingo 31 de marzo de 2030 cambia la hora: 21:00 sigue a las 21:00."""
    task = todo("t-x", "Tarea inventada", "2030-03-30T21:00")
    due = tomorrow_due(task, date(2030, 3, 30), free_calendar(), TaskList(available=True))
    assert due == at("2030-03-31T21:00")
    assert isinstance(due, datetime) and due.utcoffset() == timedelta(hours=2)


def test_next_free_y_busy_spans_por_separado():
    busy = busy_spans(free_calendar(), tomorrow_tasks(), FRIDAY, MADRID)
    assert busy == [(at("2030-01-11T13:00"), at("2030-01-11T14:15"))]
    assert next_free(at("2030-01-11T13:00"), 30, busy) == at("2030-01-11T14:15")


# --- 🔮 Mañana --------------------------------------------------------------------------------


def test_e08_la_linea_de_manana_es_una_con_calendario_libre_y_el_entreno():
    """Pilla más de una línea, o el entreno de hoy en vez del de mañana."""
    apex = apex_e08().model_copy(update={"planned_today": "Descanso"})
    line = tomorrow_line(free_calendar(), apex, FRIDAY, MADRID)
    assert line == "🔮 Mañana: calendario libre · 🏋️ Pierna · fuerza"
    assert "\n" not in line


def test_el_primer_evento_es_el_de_hora_mas_temprana_no_rechazado():
    calendar = calendar_with(
        event(at("2030-01-11T11:00"), title="Reunión inventada"),
        event(at("2030-01-11T08:00"), title="Rechazada", declined=True),
        event(at("2030-01-11T09:00"), title="Dentista"),
        all_day(FRIDAY, "Cumpleaños inventado"),
    )
    assert tomorrow_line(calendar, apex_e08(), FRIDAY, MADRID).startswith(
        "🔮 Mañana: 9:00 Dentista ·"
    )


def test_solo_con_eventos_de_dia_entero_sale_el_primero():
    calendar = calendar_with(all_day(FRIDAY, "Vacaciones inventadas"))
    assert tomorrow_line(calendar, apex_e08(), FRIDAY, MADRID).startswith(
        "🔮 Mañana: Vacaciones inventadas ·"
    )


def test_con_el_calendario_caido_lo_dice_y_sin_apex_solo_el_calendario():
    line = tomorrow_line(
        CalendarDay.unavailable("timeout"), ApexSnapshot.unavailable("file_missing"), FRIDAY, MADRID
    )
    assert line == "🔮 Mañana: no puedo leer el calendario"


def test_sin_entreno_previsto_lo_dice():
    apex = apex_e08().model_copy(update={"planned_tomorrow": None})
    assert tomorrow_line(free_calendar(), apex, FRIDAY, MADRID).endswith("🏋️ sin entreno previsto")


def test_un_titulo_largo_no_rompe_la_linea():
    calendar = calendar_with(event(at("2030-01-11T09:00"), title="Reunión " * 30))
    line = tomorrow_line(calendar, apex_e08(), FRIDAY, MADRID)
    assert "\n" not in line and len(line) < 100
