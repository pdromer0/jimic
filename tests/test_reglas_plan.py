"""Las reglas duras del plan que son cuentas de horas (decisiones 1.16-e, F2-a y F2-b).

`jimic.plan.rules.refusals` es lo que `propose_plan` le rechaza al agente en el plan del día; los
solapes y el pasado son las mismas funciones que corrigen `no_overlap` y `due_from_now` en la
evaluación. Jueves 10-ene-2030, con los valores a mano del plan de la tarea.
"""

from datetime import date
from typing import Any

import pytest
from heartbeat_data import event, madrid, task

from jimic.memory.schemas import Habit
from jimic.plan.rules import clashes, refusals, starts_before

MORNING = madrid("2030-01-10T07:45")
NOON = madrid("2030-01-10T12:30")
MEETING = event(madrid("2030-01-10T10:00"), 60, "Reunión de equipo")


def habit(habit_id: str, name: str, **changes: Any) -> Habit:
    return Habit.model_validate(
        {
            "id": habit_id,
            "name": name,
            "priority": "medium",
            "duration": "15m",
            "moment": "evening",
            "todoist_task": name,
        }
        | changes
    )


GYM = habit("gimnasio", "Gimnasio", priority="high", duration="75m")
STRETCH = habit("estiramientos", "Estiramientos", days=["sat", "sun"])


def create(title: str, due: str, minutes: int | None = None) -> dict[str, Any]:
    return {"action": "create", "title": title, "due": due, "duration_minutes": minutes}


def move(task_id: str, due: str, title: str = "x") -> dict[str, Any]:
    return {"action": "move", "task_id": task_id, "due": due, "snapshot": {"title": title}}


def delete(task_id: str, title: str = "x") -> dict[str, Any]:
    return {"action": "delete", "task_id": task_id, "snapshot": {"title": title}}


def why(*items, start=0, tasks=(), events=(MEETING,), habits=(GYM, STRETCH), now=MORNING):
    return refusals(list(items), start, list(tasks), list(events), list(habits), now)


# --- Solapes -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("due", "minutes", "steps"),
    [
        ("2030-01-10T11:00", 30, False),
        ("2030-01-10T10:30", 30, True),
        ("2030-01-10T09:30", 30, False),
        ("2030-01-10T09:45", 30, True),
        ("2030-01-10T10:59", None, True),
        ("2030-01-10T11:00", None, False),
    ],
)
def test_contiguo_no_pisa_y_encima_de_un_evento_si(due, minutes, steps):
    """Pilla intervalos cerrados (11:00 tras una reunión hasta las 11:00 pisaría)."""
    expected = ["El elemento 1: pisa «Reunión de equipo» 10:00–11:00"] if steps else []
    assert why(create("Ofertas", due, minutes)) == expected


def test_una_tarea_con_hora_y_sin_duracion_es_un_instante():
    webinar = task("t-web", "Webinar", madrid("2030-01-10T10:00"))
    assert why(create("Ofertas", "2030-01-10T10:00", 30), tasks=[webinar], events=()) == [
        "El elemento 1: pisa «Webinar» a las 10:00"
    ]
    assert why(create("Ofertas", "2030-01-10T09:30", 30), tasks=[webinar], events=()) == []


def lunch() -> Any:
    return task("t-comida", "Comida", madrid("2030-01-10T14:00")).model_copy(
        update={"duration_minutes": 60}
    )


def test_mover_libera_el_hueco():
    """Pilla lo movido que sigue ocupando su hueco: ofertas 13:45–14:15 cabe si la comida se va."""
    offers = create("Revisar ofertas", "2030-01-10T13:45", 30)
    assert why(offers, tasks=[lunch()]) == ["El elemento 1: pisa «Comida» 14:00–15:00"]
    assert why(move("t-comida", "2030-01-10T15:00"), offers, tasks=[lunch()]) == []


def test_borrar_libera_el_hueco_y_vale_aunque_llegue_en_otra_llamada():
    offers = create("Revisar ofertas", "2030-01-10T14:00", 30)
    assert why(delete("t-comida"), offers, start=1, tasks=[lunch()]) == []


def test_lo_ya_propuesto_en_la_ejecucion_cuenta_y_el_numero_es_el_de_esta_llamada():
    gym = create("Gimnasio", "2030-01-10T12:00", 75)
    assert why(create("Ofertas", "2030-01-10T13:30", 30), start=0) == []
    assert why(gym, create("Ofertas", "2030-01-10T13:00", 30), start=1) == [
        "El elemento 1: pisa «Gimnasio» 12:00–13:15"
    ]
    # Dos sin duración a la misma hora se pisan.
    assert why(create("A", "2030-01-10T18:00"), create("B", "2030-01-10T18:00")) == [
        "El elemento 2: pisa «A» a las 18:00"
    ]


def test_ni_el_evento_rechazado_ni_el_de_todo_el_dia_ni_el_del_mismo_titulo_pisan():
    declined = event(madrid("2030-01-10T10:00"), 60, "Otra", declined=True)
    same = event(madrid("2030-01-10T09:50"), 60, "Webinar")
    webinar = task("t-web", "Webinar", madrid("2030-01-10T10:00"))
    assert why(create("Ofertas", "2030-01-10T10:15", 30), events=[declined]) == []
    assert why(move("t-web", "2030-01-10T09:50"), tasks=[webinar], events=[same]) == []


def test_otro_dia_no_se_mira():
    assert why(create("Ofertas", "2030-01-11T10:30", 30)) == []


def test_clashes_es_lo_que_corrige_no_overlap():
    items = [create("Ofertas", "2030-01-10T10:30", 30), create("B", "2030-01-10T16:00", 30)]
    (clash,) = clashes(items, [], [MEETING], MORNING)
    assert clash.index == 0 and clash.other.title == "Reunión de equipo"


# --- El pasado --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("due", "past"),
    [
        ("2030-01-10T12:00", True),
        ("2030-01-10T12:30", False),
        ("2030-01-10", False),
        ("2030-01-09", True),
        ("2030-01-09T23:59", True),
    ],
)
def test_lo_que_empieza_antes_de_ahora_se_rechaza(due, past):
    """Las 12:30 en punto no son pasado, ni hoy sin hora."""
    assert starts_before(create("Ofertas", due), NOON) is past
    expected = ["El elemento 1: empieza antes de ahora (12:30)"] if past else []
    assert why(create("Ofertas", due), events=(), now=NOON) == expected


def test_un_borrado_no_empieza_nunca():
    assert not starts_before(delete("t-x"), NOON)


# --- Lo de esta mañana (H1-7 con la F2-b) ----------------------------------------------------

GYM_TASK = task("t-gym", "Gimnasio", madrid("2030-01-10T09:00"))
WEBINAR = task("t-web", "Webinar de innovación", madrid("2030-01-10T10:00"))


def test_la_tarea_de_un_habito_que_paso_esta_manana_se_puede_mover_a_la_tarde():
    assert why(move("t-gym", "2030-01-10T15:00"), tasks=[GYM_TASK], events=(), now=NOON) == []


def test_la_tarea_de_un_habito_que_paso_no_se_borra_ni_se_mueve_a_otra_hora_pasada():
    assert why(delete("t-gym"), tasks=[GYM_TASK], events=(), now=NOON) == [
        "El elemento 1: «Gimnasio» era hoy a las 9:00: no la borres, muévela a un hueco libre de"
        " más tarde"
    ]
    assert why(move("t-gym", "2030-01-10T11:00"), tasks=[GYM_TASK], events=(), now=NOON) == [
        "El elemento 1: empieza antes de ahora (12:30)"
    ]


@pytest.mark.parametrize("item", [move("t-web", "2030-01-10T15:00"), delete("t-web")])
def test_una_tarea_pasada_de_hoy_que_no_es_de_un_habito_no_se_toca(item):
    """Pilla la regla que deja tocar cualquier tarea pasada (versión incorrecta 5)."""
    assert why(item, tasks=[WEBINAR], events=(), now=NOON) == [
        "El elemento 1: «Webinar de innovación» era hoy a las 10:00 y ya pasó: lo ve el cierre,"
        " no la toques"
    ]


def test_las_atrasadas_de_ayer_y_lo_que_aun_no_ha_llegado_se_pueden_tocar():
    yesterday = task("t-ayer", "Llamar al fontanero", madrid("2030-01-09T10:00"))
    later = task("t-luego", "Llamar al seguro", madrid("2030-01-10T17:00"))
    today = task("t-hoy", "Comprar pilas", date(2030, 1, 10))
    assert why(delete("t-ayer"), tasks=[yesterday], events=(), now=NOON) == []
    assert why(delete("t-luego"), delete("t-hoy"), tasks=[later, today], events=(), now=NOON) == []


# --- Los días de un hábito (F2-a) --------------------------------------------------------------


def test_la_tarea_de_un_habito_no_se_lleva_a_un_dia_que_no_le_toca():
    """El 10 es jueves; el 12, sábado."""
    reason = "El elemento 1: «Estiramientos» no toca el jueves (sus días: sábado, domingo)"
    assert why(create("Estiramientos", "2030-01-10T21:00", 15)) == [reason]
    assert why(create("Estiramientos", "2030-01-12T21:00", 15)) == []
    overdue = task("t-est", "Estiramientos de movilidad", madrid("2030-01-09T21:00"))
    assert why(move("t-est", "2030-01-10"), tasks=[overdue]) == [reason]
    assert why(move("t-est", "2030-01-12"), tasks=[overdue]) == []


def test_sin_days_todo_igual_que_hoy():
    assert why(create("Gimnasio", "2030-01-10T12:00", 75)) == []
    assert (
        why(create("Estiramientos", "2030-01-10T21:00", 15), habits=[habit("e", "Estiramientos")])
        == []
    )


def test_todos_los_motivos_de_una_llamada_a_la_vez():
    items = [
        create("Estiramientos", "2030-01-10T10:30", 15),
        create("Ofertas", "2030-01-10T16:00", 30),
        create("Lectura", "2030-01-10T07:00", 20),
    ]
    assert why(*items) == [
        "El elemento 1: «Estiramientos» no toca el jueves (sus días: sábado, domingo); pisa"
        " «Reunión de equipo» 10:00–11:00",
        "El elemento 3: empieza antes de ahora (7:45)",
    ]
