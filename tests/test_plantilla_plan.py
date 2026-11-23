"""La plantilla del plan del día (decisiones 1.14-b y 1.14-d): «Tu día», el plan tardío, las
propuestas, las 25 líneas, los iconos y la línea 🧠. Funciones puras con datos inventados, salvo
la línea 🧠, que lee `changes` y `notices` de una base temporal.
"""

from datetime import date, datetime

import pytest
from heartbeat_data import all_day, calendar_with, event, madrid, task

from jimic.channel.message import InteractiveMessage, render
from jimic.clock import to_db
from jimic.memory.database import open_database
from jimic.memory.markdown_checks import icon_rows
from jimic.memory.notices import record_sent
from jimic.plan.day import day_view
from jimic.plan.message import (
    MAX_LINES,
    PlanContent,
    icon_table,
    learned_line,
    line_count,
    paint,
)
from jimic.tools.calendar import CalendarDay
from jimic.tools.todoist import TaskList

TODAY = date(2030, 1, 10)
NOW = madrid("2030-01-10T07:45")
TABLE = icon_table([("Comida", "🍽️"), ("Gimnasio", "🏋️")])


def tasks(*items) -> TaskList:
    return TaskList(available=True, tasks=list(items))


def timed(task_id: str, title: str, at: str, minutes: int | None = None):
    return task(task_id, title, madrid(f"2030-01-10T{at}")).model_copy(
        update={"duration_minutes": minutes}
    )


def lines(plan: InteractiveMessage | str) -> list[str]:
    return (plan if isinstance(plan, str) else render(plan, 1).text).split("\n")


def create(title: str, due: str, minutes: int | None = None, **extra) -> dict:
    entry = {"action": "create", "title": title, "due": due, "reason": "toca hoy"}
    return entry | {"duration_minutes": minutes} | extra


def snapshot(title: str, due: str | None) -> dict:
    return {"title": title, "due": due, "project_id": "p", "is_recurring": False}


# --- «Tu día» ---------------------------------------------------------------------------------


def test_tu_dia_va_en_orden_con_los_de_todo_el_dia_arriba_y_sin_rechazados():
    """Pilla otro orden, un evento rechazado que se cuela o una atrasada en «Tu día»."""
    calendar = calendar_with(
        event(madrid("2030-01-10T15:30"), 210, "Trabajo en el proyecto"),
        event(madrid("2030-01-10T10:00"), 60, "Reunión de equipo"),
        event(madrid("2030-01-10T12:00"), 30, "Comida con un cliente", declined=True),
        all_day(TODAY, "Fiesta del barrio"),
    )
    view = day_view(
        calendar,
        tasks(
            timed("t1", "Comida", "14:00", 60),
            task("t2", "Llamar al seguro", TODAY),
            task("t3", "Atrasada", date(2030, 1, 9)),
            timed("t4", "Reunión de equipo", "10:00"),
        ),
        NOW,
        late=False,
    )
    assert [(e.title, e.start and e.start.strftime("%H:%M")) for e in view.entries] == [
        ("Fiesta del barrio", None),
        ("Llamar al seguro", None),
        ("Reunión de equipo", "10:00"),  # el evento antes que la tarea a la misma hora
        ("Reunión de equipo", "10:00"),
        ("Comida", "14:00"),
        ("Trabajo en el proyecto", "15:30"),
    ]
    assert [e.is_event for e in view.entries][2:4] == [True, False]


def test_el_plan_tardio_deja_solo_el_resto_del_dia_y_cuenta_lo_pasado_sin_marcar():
    """El p02 de la evaluación a las 12:30 (valor a mano del plan): 3 sin marcar y «Tu día» con
    15:00 Comida, 15:30 Trabajo en el proyecto y 19:00 Paseo 5 km."""
    calendar = calendar_with(
        event(madrid("2030-01-10T09:50"), 70, "Webinar de innovación"),
        event(madrid("2030-01-10T15:30"), 210, "Trabajo en el proyecto"),
    )
    pending = tasks(
        timed("t-meditacion", "Meditación", "07:10"),
        timed("t-estiramientos", "Estiramientos", "08:05"),
        timed("t-webinar", "Webinar de innovación", "10:00"),
        timed("t-comida", "Comida", "15:00"),
        timed("t-paseo", "Paseo 5 km", "19:00"),
    )
    view = day_view(calendar, pending, madrid("2030-01-10T12:30"), late=True)
    assert view.unmarked == 3
    assert [(e.start.strftime("%H:%M") if e.start else None, e.title) for e in view.entries] == [
        ("15:00", "Comida"),
        ("15:30", "Trabajo en el proyecto"),
        ("19:00", "Paseo 5 km"),
    ]
    content = PlanContent(greeting="Hola", entries=[])
    plan = paint(content, view, True, madrid("2030-01-10T12:30"), TABLE)
    assert "3 cosas de esta mañana sin marcar: las vemos en el cierre" in lines(plan)
    assert lines(plan)[2].startswith("📅 Lo que queda de tu día")


def test_un_evento_en_curso_sigue_en_el_plan_tardio():
    calendar = calendar_with(event(madrid("2030-01-10T12:00"), 60, "Reunión larga"))
    view = day_view(calendar, tasks(), madrid("2030-01-10T12:30"), late=True)
    assert [e.title for e in view.entries] == ["Reunión larga"]


def test_los_iconos_salen_de_la_tabla_luego_del_agente_y_si_no_del_cuadrado():
    view = day_view(
        calendar_with(event(madrid("2030-01-10T10:00"), 60, "Reunión de equipo")),
        tasks(timed("t1", "comida", "14:00"), timed("t2", "Paseo  5 km", "19:00")),
        NOW,
        late=False,
    )
    content = PlanContent(greeting="Hola", entries=[], day_icons={"paseo 5 km": "🚶"})
    assert lines(paint(content, view, False, NOW, TABLE))[3:6] == [
        "▫️ 10:00 Reunión de equipo",
        "🍽️ 14:00 comida",
        "🚶 19:00 Paseo 5 km",  # los espacios se juntan
    ]


def test_con_todoist_caido_tu_dia_lo_dice_y_no_finge_que_no_hay_tareas():
    view = day_view(calendar_with(), TaskList.unavailable("timeout"), NOW, late=False)
    plan = lines(paint(PlanContent(greeting="Hola", entries=[]), view, False, NOW, TABLE))
    assert plan[2] == "📅 Tu día (de tu calendario; Todoist no responde)"
    both = day_view(CalendarDay.unavailable("auth"), TaskList.unavailable("timeout"), NOW, False)
    text = lines(paint(PlanContent(greeting="Hola", entries=[]), both, False, NOW, TABLE))
    assert text[2] == "📅 Tu día: ahora no puedo leer ni tu Todoist ni tu calendario"
    assert len(text) == 3


# --- Las propuestas y el mensaje -------------------------------------------------------------


def test_el_plan_pinta_las_lineas_de_la_especificacion_y_sus_botones():
    """El ejemplo del formato: crear con su franja, mover con ⚠️ y borrar; números 1️⃣ y la fila
    global del plan. Pilla un texto libre del agente que se cuela en el plan."""
    view = day_view(calendar_with(), tasks(timed("t1", "Comida", "14:00", 60)), NOW, False)
    entries = [
        create("Gimnasio", "2030-01-10T12:00", 75, reason="toca pierna"),
        create("Estiramientos", "2030-01-10T21:30", 15, icon="🧘", reason="hábito de la noche"),
        {
            "action": "move",
            "task_id": "t-web",
            "due": "2030-01-10T09:50",
            "reason": "manda el calendario",
            "icon": "⚠️",
            "snapshot": snapshot("Webinar", "2030-01-10T10:00:00+01:00"),
        },
        {
            "action": "delete",
            "task_id": "t-andar",
            "reason": "duplica «Paseo 30 min» de hoy",
            "snapshot": snapshot("Andar 30 min", "2030-01-09"),
        },
    ]
    content = PlanContent(
        greeting="Jueves de pierna, ¿eh?",
        entries=entries,
        training="🏋️ Pierna · fuerza a mediodía",
        learned="🧠 Desde ayer he aprendido algo",
    )
    plan = paint(content, view, False, NOW, TABLE)
    assert isinstance(plan, InteractiveMessage)
    view_ = render(plan, 7)
    assert view_.text.split("\n") == [
        "¡Pedro! Jueves de pierna, ¿eh?",
        "",
        "📅 Tu día (de tu Todoist y tu calendario)",
        "🍽️ 14:00 Comida",
        "",
        "🧩 Te propongo añadir",
        "1️⃣ 🏋️ Gimnasio · 12:00–13:15 (toca pierna)",
        "2️⃣ 🧘 Estiramientos · 21:30–21:45 (hábito de la noche)",
        "3️⃣ ⚠️ «Webinar» 10:00 → 9:50 · manda el calendario",
        "4️⃣ 🗑️ «Andar 30 min» · ayer · duplica «Paseo 30 min» de hoy",
        "",
        "🧠 Desde ayer he aprendido algo",
        "🏋️ Pierna · fuerza a mediodía",
    ]
    assert [[b.label for b in row] for row in view_.keyboard] == [
        ["1 ✅", "1 ✏️", "1 ⏭"],
        ["2 ✅", "2 ✏️", "2 ⏭"],
        ["3 🔄 A las 9:50", "3 🆗 Déjalo"],
        ["4 🗑️ Bórrala", "4 🆗 Déjala"],
        ["✅ Créalo todo", "⏭ Hoy sin plan"],
    ]
    # Lo que hace cada elemento vive en sus datos, no en el texto.
    assert plan.items[0].data == entries[0]


def test_sin_propuestas_el_plan_es_texto_sin_botones_con_lo_que_se_queda_fuera():
    view = day_view(calendar_with(), tasks(), NOW, False)
    content = PlanContent(
        greeting="¡Pedro! Día tranquilo",
        entries=[],
        left_out=["😅 El gimnasio hoy no cabe: mañana a primera hora"],
    )
    plan = paint(content, view, False, NOW, TABLE)
    assert isinstance(plan, str)
    assert lines(plan) == [
        "¡Pedro! Día tranquilo",
        "",
        "📅 Tu día (de tu Todoist y tu calendario)",
        "▫️ Nada apuntado para hoy",
        "",
        "😅 El gimnasio hoy no cabe: mañana a primera hora",
    ]


def test_el_plan_no_pasa_de_25_lineas_aunque_tu_dia_sea_enorme():
    """Versión incorrecta 5: una plantilla que no corta «Tu día» pasaría de 25 líneas."""
    many = [
        timed(f"t{n}", f"Tarea {n:02d}", f"{8 + n // 4:02d}:{(n % 4) * 15:02d}") for n in range(30)
    ]
    view = day_view(calendar_with(), tasks(*many), NOW, False)
    entries = [create(f"Nueva {n}", "2030-01-10") for n in range(4)] + [
        {
            "action": "delete",
            "task_id": f"d{n}",
            "reason": "duplicada",
            "snapshot": snapshot(f"Vieja {n}", "2030-01-09"),
        }
        for n in range(4)
    ]
    content = PlanContent(
        greeting="Hola",
        entries=entries,
        left_out=["uno", "dos"],
        training="entreno",
        learned="🧠 aprendido",
    )
    plan = paint(content, view, True, NOW, TABLE)
    assert line_count(plan) <= MAX_LINES
    text = lines(plan)
    assert any(line.startswith("… y ") and line.endswith(" más") for line in text)
    assert "▫️ 8:00 Tarea 00" in text  # se recorta por el final


def test_un_dia_que_cabe_no_se_recorta():
    view = day_view(calendar_with(), tasks(timed("t1", "Comida", "14:00")), NOW, False)
    plan = paint(PlanContent(greeting="Hola", entries=[]), view, False, NOW, TABLE)
    assert not any(line.startswith("… y") for line in lines(plan))


# --- La tabla de iconos -----------------------------------------------------------------------


def test_la_tabla_de_iconos_salta_la_cabecera_y_las_filas_rotas():
    text = """# Preferencias

## Iconos

| Tarea | Icono |
| --- | --- |
| Gimnasio | 🏋️ |
| Cena | |
| Comida | 🍽️ |

## Otra
| Fuera | ❌ |
"""
    assert icon_rows(text) == [("Gimnasio", "🏋️"), ("Comida", "🍽️")]


# --- 🧠 (decisión 1.14-d) ---------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


def add_change(conn, at: datetime, file: str = "preferencias.md", reverted: bool = False):
    conn.execute(
        "INSERT INTO changes (created_at, file, before, after, source_message, operation,"
        " reverted_at) VALUES (?, ?, NULL, 'x', 'origen', 'add', ?)",
        (to_db(at), file, to_db(at) if reverted else None),
    )


def test_la_linea_sale_con_un_cambio_guardado_desde_ayer(conn):
    """Sin plan anterior, desde las 00:00 de ayer (valor a mano: 2030-01-08T23:00Z): un cambio
    de antes de ese momento no cuenta y uno de después, sí."""
    add_change(conn, madrid("2030-01-08T23:59"))
    assert learned_line(conn, NOW, ["plan_del_dia"]) is None
    add_change(conn, madrid("2030-01-09T00:01"))
    assert learned_line(conn, NOW, ["plan_del_dia"]) == (
        "🧠 Desde ayer he aprendido: preferencias.md · cambiado (lo ves y lo deshaces con /memoria)"
    )


def test_la_linea_no_sale_con_un_cambio_deshecho(conn):
    """Versión incorrecta 8: contar un cambio que Pedro deshizo le diría que aprendió algo que
    ya no está."""
    add_change(conn, madrid("2030-01-09T20:00"), reverted=True)
    assert learned_line(conn, NOW, ["plan_del_dia"]) is None


def test_la_linea_cuenta_desde_el_plan_anterior_y_sin_ideas(conn):
    record_sent(conn, "plan_del_dia", madrid("2030-01-09T21:00"), 5)
    add_change(conn, madrid("2030-01-09T20:00"))  # antes del plan anterior
    add_change(conn, madrid("2030-01-09T22:00"), file="ideas.md")
    assert learned_line(conn, NOW, ["plan_del_dia"]) is None
    add_change(conn, madrid("2030-01-09T22:30"))
    add_change(conn, madrid("2030-01-10T07:00"), file="rutina.md")
    assert learned_line(conn, NOW, ["plan_del_dia"]) == (
        "🧠 Desde ayer he aprendido 2 cosas (preferencias.md, rutina.md):"
        " lo ves y lo deshaces con /memoria"
    )


def test_la_linea_dice_la_fecha_si_el_plan_anterior_no_fue_ayer(conn):
    record_sent(conn, "plan_del_dia", madrid("2030-01-07T08:00"), 5)
    add_change(conn, madrid("2030-01-08T10:00"))
    line = learned_line(conn, NOW, ["plan_del_dia"])
    assert line is not None and line.startswith("🧠 Desde el lun 07/01 he aprendido:")
