"""El estado de las tareas clave y sus topes en la segunda pasada (tarea 1.15, CU-02).

Lo que el código sabe de cada tarea clave (hecha y por qué fuente, ya no es de hoy, «hoy no», la
pausa, desde cuándo se puede insistir y el nivel del tono) y lo que la segunda pasada garantiza
con ello, decida lo que decida el modelo. Datos inventados; el día es el jueves 10/01/2030.
"""

import asyncio
import json
from datetime import UTC, date, datetime
from typing import Any

import pytest
from apex_data import invented_snapshot
from conftest import FixedClock
from heartbeat_data import (
    GYM,
    HABIT,
    MADRID,
    SpyDecide,
    add_notice,
    calendar_with,
    intentions,
    limits,
    madrid,
    outgoing,
    sources,
)
from todoist_data import FixedTodoistReader

from jimic.clock import local_day_bounds
from jimic.heartbeat.cycle import CycleResult, run_cycle
from jimic.heartbeat.key_tasks import KeyTaskStatus, key_task_status, read_jumped
from jimic.heartbeat.limits import WaitingNotices, second_pass
from jimic.heartbeat.sources import Sources
from jimic.heartbeat.state import build_day_state
from jimic.memory import key_marks
from jimic.memory.database import open_database
from jimic.memory.key_marks import IntentMarks
from jimic.memory.notices import sent_between
from jimic.memory.schemas import Habit, KeyTask
from jimic.tools.apex_schema import ApexSnapshot, TodayWorkout
from jimic.tools.failures import ToolFailure
from jimic.tools.todoist import TaskList, TodoistTask, TodoistTools

DAY = date(2030, 1, 10)
OFFERS_KEY = {**GYM, "id": "insistir_ofertas", "habit": "ofertas", "default_time": "17:15"}
OFFERS_HABIT = {
    **HABIT,
    "id": "ofertas",
    "name": "Revisar ofertas",
    "todoist_task": "Revisar ofertas",
}
INTENTION = KeyTask.model_validate(GYM)
GYM_HABIT = Habit.model_validate(HABIT)
TRAINING = Habit.model_validate({**HABIT, "training": True})


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.sqlite3")
    yield connection
    connection.close()


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2030, 1, 10, hour, minute, tzinfo=MADRID)


ONE_PM = at(13)


def gym_task(due: Any = None, task_id: str = "t-gym", title: str = "Gimnasio") -> TodoistTask:
    return TodoistTask(
        id=task_id,
        title=title,
        project_id="proyecto-inventado",
        due=at(12) if due is None else due,
        is_recurring=False,
        has_subtasks=False,
        duration_minutes=75,
    )


def tasks(*items: TodoistTask) -> TaskList:
    return TaskList(available=True, tasks=list(items))


def trained(yes: bool = True) -> ApexSnapshot:
    snapshot = invented_snapshot("2030-01-10 09:00")
    workout = TodayWorkout.TRAINED if yes else TodayWorkout.NOT_RECORDED
    return snapshot.model_copy(update={"today_workout": workout})


def status(
    now: datetime = ONE_PM,
    *,
    habit: Habit | None = GYM_HABIT,
    open_tasks: TaskList | None = None,
    completed: TaskList | None = None,
    apex: ApexSnapshot | None = None,
    marks: IntentMarks | None = None,
    jumped: frozenset[str] = frozenset(),
    moved: frozenset[str] = frozenset(),
    sent: int = 0,
) -> KeyTaskStatus:
    return key_task_status(
        now=now,
        intention=INTENTION,
        habit=habit,
        tasks=open_tasks if open_tasks is not None else tasks(gym_task()),
        completed=completed if completed is not None else tasks(),
        apex=apex if apex is not None else trained(False),
        marks=marks if marks is not None else IntentMarks(),
        jumped=jumped,
        moved=moved,
        sent_today=sent,
    )


# --- Desde cuándo se puede insistir (1.15-i) ---------------------------------------------------


def test_se_puede_insistir_desde_la_hora_de_la_tarea_mas_el_margen():
    """12:00 + 45 min = 12:45 (valor a mano del plan). Pilla el margen ignorado."""
    assert status().insist_from == at(12, 45)
    assert status(at(12, 44)).refusal(at(12, 44)) == "key_too_early"
    assert status(at(12, 45)).refusal(at(12, 45)) is None


@pytest.mark.parametrize(
    "open_tasks",
    [
        tasks(),
        tasks(gym_task(DAY)),  # sin hora
        tasks(gym_task(datetime(2030, 1, 9, 13, tzinfo=MADRID))),  # la de ayer, vencida
        tasks(gym_task(title="Otra cosa")),
    ],
    ids=["sin_tarea", "sin_hora", "vencida_de_ayer", "otro_titulo"],
)
def test_sin_tarea_de_hoy_con_hora_cuenta_la_hora_por_defecto(open_tasks):
    """13:00 + 45 min = 13:45. La vencida de ayer no es la de hoy (e02)."""
    assert status(open_tasks=open_tasks).insist_from == at(13, 45)


def test_el_titulo_se_compara_sin_mayusculas_ni_espacios_de_mas():
    found = status(open_tasks=tasks(gym_task(title="  gimnasio ")))
    assert found.task is not None and found.insist_from == at(12, 45)


def test_con_dos_tareas_de_hoy_manda_la_primera_con_hora():
    two = tasks(gym_task(DAY, "t-dia"), gym_task(at(18), "t-18"), gym_task(at(12), "t-12"))
    found = status(open_tasks=two)
    assert found.task is not None and found.task.id == "t-12"


def test_si_el_margen_cae_manana_hoy_no_se_insiste():
    """23:30 + 45 min = 00:15 del día siguiente (valor a mano)."""
    late = status(at(23, 50), open_tasks=tasks(gym_task(at(23, 30))))
    assert late.refusal(at(23, 50)) == "key_too_early"
    assert late.insist_from == datetime(2030, 1, 11, 0, 15, tzinfo=MADRID)


def test_si_la_tarea_se_mueve_cuenta_la_hora_nueva():
    """[Más tarde] a las 20:00: 20:45 (valor a mano del plan)."""
    assert status(open_tasks=tasks(gym_task(at(20)))).insist_from == at(20, 45)


# --- Hecha (1.15-e) y ya no es de hoy (1.15-j) -------------------------------------------------


def test_el_boton_la_da_por_hecha():
    assert status(marks=IntentMarks(done=True)).done_by == "button"
    assert status(marks=IntentMarks(done=True)).refusal(at(13)) == "key_done"


def test_completada_en_la_app_y_sin_ninguna_abierta_de_hoy_es_hecha_por_todoist():
    done = status(open_tasks=tasks(), completed=tasks(gym_task()))
    assert done.done_by == "todoist" and done.refusal(at(13)) == "key_done"


def test_completada_pero_con_otra_abierta_de_hoy_no_es_hecha():
    """Versión incorrecta 6: el [Deshacer] de una recurrente la devuelve abierta a hoy, y la
    completada de antes sigue en la lista: no está hecha."""
    assert status(completed=tasks(gym_task())).done_by is None


def test_con_las_completadas_caidas_no_se_supone_hecha():
    down = status(open_tasks=tasks(), completed=TaskList.unavailable("http_503"))
    assert down.done_by is None


def test_el_entreno_en_apex_solo_cuenta_en_el_habito_del_entreno():
    assert status(habit=TRAINING, apex=trained()).done_by == "apex"
    assert status(habit=GYM_HABIT, apex=trained()).done_by is None
    assert status(habit=TRAINING, apex=ApexSnapshot.unavailable("file_missing")).done_by is None


def test_el_boton_manda_sobre_todoist_y_todoist_sobre_apex():
    both = status(habit=TRAINING, open_tasks=tasks(), completed=tasks(gym_task()), apex=trained())
    assert both.done_by == "todoist"
    assert status(habit=TRAINING, apex=trained(), marks=IntentMarks(done=True)).done_by == "button"


def test_la_tarea_vista_hoy_que_salta_a_otro_dia_ya_no_es_de_hoy():
    """1.15-j: una recurrente completada en la app pasa a mañana y no sale en las completadas."""
    jumped = status(
        open_tasks=tasks(),
        marks=IntentMarks(seen=frozenset({"t-gym"})),
        jumped=frozenset({"t-gym"}),
    )
    assert jumped.rescheduled and jumped.done_by is None
    assert jumped.refusal(at(13)) == "key_rescheduled"


def test_un_movimiento_de_jimic_no_es_un_salto():
    """Versión incorrecta 9 (Pedro): [Más tarde] a mañana lo hace JimiC; no cuenta como salto."""
    moved = status(
        open_tasks=tasks(),
        marks=IntentMarks(seen=frozenset({"t-gym"})),
        jumped=frozenset({"t-gym"}),
        moved=frozenset({"t-gym"}),
    )
    assert not moved.rescheduled


def test_una_tarea_no_vista_hoy_no_salta():
    unseen = status(open_tasks=tasks(), jumped=frozenset({"t-gym"}))
    assert not unseen.rescheduled


# --- «Hoy no», la pausa y el tono --------------------------------------------------------------


def test_hoy_no_corta_la_insistencia():
    assert status(marks=IntentMarks(not_today=True)).refusal(at(13)) == "key_not_today"


def test_voy_ahora_pausa_la_duracion_del_habito():
    """13:30 + 75 min = 14:45 (valor a mano)."""
    going = IntentMarks(going_at=at(13, 30).astimezone(UTC))
    assert status(at(14), marks=going).paused_until == at(14, 45)
    assert status(at(14), marks=going).refusal(at(14)) == "key_paused"
    assert status(at(14), marks=going).to_json(at(14))["paused_until"] == "14:45"


def test_al_acabar_la_pausa_se_puede_volver_a_insistir():
    """Versión incorrecta 3: una pausa que no caduca."""
    going = IntentMarks(going_at=at(13, 30).astimezone(UTC))
    assert status(at(14, 45), marks=going).refusal(at(14, 45)) is None
    assert "paused_until" not in status(at(14, 45), marks=going).to_json(at(14, 45))


@pytest.mark.parametrize(("sent", "level"), [(0, 1), (1, 2), (2, 3), (3, 3)])
def test_el_nivel_del_tono_son_los_avisos_de_hoy_mas_uno_hasta_tres(sent, level):
    assert status(sent=sent).tone_level == level


def test_hecha_manda_sobre_los_demas_topes():
    everything = status(
        at(12),
        marks=IntentMarks(done=True, not_today=True, going_at=at(11, 50).astimezone(UTC)),
    )
    assert everything.refusal(at(12)) == "key_done"


# --- El estado del día y la segunda pasada -----------------------------------------------------


def state_at(conn, now: datetime, its=None, open_tasks=None, completed=None):
    its = its if its is not None else intentions(GYM, OFFERS_KEY, habits=[HABIT, OFFERS_HABIT])
    return build_day_state(
        now,
        MADRID,
        calendar_with(),
        open_tasks if open_tasks is not None else tasks(gym_task()),
        trained(False),
        sent_between(conn, *local_day_bounds(now.date(), MADRID)),
        its,
        limits(),
        WaitingNotices(),
        completed=completed if completed is not None else tasks(),
        marks=key_marks.of_day(conn, now.date()),
    )


def test_el_nivel_cuenta_solo_los_avisos_de_su_tarea(conn):
    """Versión incorrecta 5: con dos tareas clave, cada una lleva su propio nivel."""
    add_notice(conn, "insistir_gimnasio", madrid("2030-01-10T12:45"))
    add_notice(conn, "insistir_gimnasio", madrid("2030-01-10T13:30"))
    state = state_at(conn, at(17, 30))
    assert state.key_tasks["insistir_gimnasio"].tone_level == 3
    assert state.key_tasks["insistir_ofertas"].tone_level == 1


def test_el_json_lleva_lo_de_cada_tarea_clave_dentro_de_su_intencion(conn):
    key_marks.record(conn, DAY, "insistir_ofertas", "not_today", at(9))
    document = json.loads(state_at(conn, at(13)).to_json())
    entries = {entry["id"]: entry for entry in document["intentions"]}
    assert entries["insistir_gimnasio"]["key_task"] == {
        "task": "12:00",
        "tone_level": 1,
        "sent_today": 0,
    }
    assert entries["insistir_ofertas"]["key_task"]["not_today"] is True


def blocked(conn, now: datetime, intent: str = "insistir_gimnasio") -> list[str]:
    """The reasons of the second pass for `intent`, with the state of the day at `now`."""
    its = intentions(GYM, OFFERS_KEY, habits=[HABIT, OFFERS_HABIT])
    state = state_at(conn, now, its)
    verdicts = second_pass(
        now,
        [outgoing(intent)],
        its,
        limits(),
        sent_between(conn, *local_day_bounds(now.date(), MADRID)),
        WaitingNotices(),
        None,
        state.key_tasks,
    )
    return [verdict.reason for verdict in verdicts]


def test_la_segunda_pasada_frena_una_tarea_hecha(conn):
    """Versión incorrecta 1: sin este tope, el modelo insistiría con el gimnasio hecho."""
    key_marks.record(conn, DAY, "insistir_gimnasio", "done", at(12, 50))
    assert blocked(conn, at(13)) == ["key_done"]


def test_la_segunda_pasada_frena_hoy_no(conn):
    key_marks.record(conn, DAY, "insistir_gimnasio", "not_today", at(9))
    assert blocked(conn, at(13)) == ["key_not_today"]


def test_la_segunda_pasada_frena_antes_del_margen_y_deja_despues(conn):
    """Versión incorrecta 2: sin el margen sale a las 12:30."""
    assert blocked(conn, at(12, 30)) == ["key_too_early"]
    assert blocked(conn, at(12, 45)) == ["ok"]


def test_la_segunda_pasada_frena_durante_la_pausa(conn):
    key_marks.record(conn, DAY, "insistir_gimnasio", "going", at(13, 30))
    assert blocked(conn, at(14, 30)) == ["key_paused"]


def test_la_segunda_pasada_deja_insistir_cuando_acaba_la_pausa(conn):
    key_marks.record(conn, DAY, "insistir_gimnasio", "going", at(13, 30))
    assert blocked(conn, at(14, 45)) == ["ok"]


def test_nunca_mas_de_tres_aunque_no_este_hecha(conn):
    """El tope de siempre (`insist_cap`) sigue después de los nuevos."""
    for minute in ("12:45", "13:30", "14:15"):
        add_notice(conn, "insistir_gimnasio", madrid(f"2030-01-10T{minute}"))
    assert blocked(conn, at(15)) == ["insist_cap"]


# --- El ciclo lee lo que hace falta ------------------------------------------------------------


class Reader(FixedTodoistReader):
    """Today's list, the completed ones and single tasks, counting what is read."""

    def __init__(
        self, open_tasks: TaskList, completed: TaskList, others: dict[str, TodoistTask]
    ) -> None:
        super().__init__(open_tasks)
        self.completed = completed
        self.others = others
        self.completed_reads = 0
        self.got: list[str] = []

    async def read_completed_today(self) -> TaskList:
        self.completed_reads += 1
        return self.completed

    async def get_task(self, task_id: str):
        self.got.append(task_id)
        return self.others.get(task_id)


def run(conn, now: datetime, reader: Reader, its) -> CycleResult:
    fixed = sources(calendar_with())
    srcs = Sources(
        todoist=TodoistTools(reader, fixed.todoist.writer, fixed.todoist.close),
        calendar=fixed.calendar,
        apex=fixed.apex,
    )
    return asyncio.run(
        run_cycle(
            clock=FixedClock(now),
            timezone=MADRID,
            limits=limits(),
            intentions=its,
            sources=srcs,
            conn=conn,
            queue=WaitingNotices(),
            decide=SpyDecide(outgoing("insistir_gimnasio")),
        )
    )


def test_el_ciclo_ve_la_tarea_que_salto_y_la_frena(conn):
    """La vista a las 12:00, abierta mañana a las 13:00 (una recurrente completada): salto."""
    key_marks.note_seen(conn, DAY, "insistir_gimnasio", "t-gym", at(12))
    tomorrow = gym_task(datetime(2030, 1, 11, 12, tzinfo=MADRID))
    reader = Reader(tasks(), tasks(), {"t-gym": tomorrow})
    result = run(conn, at(13), reader, intentions(GYM))
    assert reader.got == ["t-gym"]
    assert result.state is not None and result.state.key_tasks["insistir_gimnasio"].rescheduled
    assert [verdict.reason for verdict in result.verdicts] == ["key_rescheduled"]


def test_sin_tareas_clave_no_se_leen_las_completadas(conn):
    reader = Reader(tasks(), tasks(), {})
    run(
        conn,
        at(13),
        reader,
        intentions(
            {
                "id": "regar",
                "type": "notice",
                "purpose": "Regar",
                "days": ["thu"],
                "default_time": "19:00",
                "priority": "low",
            }
        ),
    )
    assert reader.completed_reads == 0


def test_con_una_tarea_clave_se_leen_una_vez(conn):
    reader = Reader(tasks(gym_task()), tasks(), {})
    run(conn, at(13), reader, intentions(GYM))
    assert reader.completed_reads == 1 and reader.got == []


def test_un_salto_que_no_se_puede_leer_no_cuenta():
    class Failing(FixedTodoistReader):
        async def get_task(self, task_id: str):
            raise ToolFailure("timeout")

    assert asyncio.run(read_jumped(Failing(), {"t-gym"}, DAY)) == frozenset()


def test_no_se_relee_lo_completado_hoy_ni_lo_de_una_tarea_hecha_o_con_hoy_no(conn):
    """Revisión propia: cada lectura son dos llamadas a Todoist en cada latido."""
    key_marks.note_seen(conn, DAY, "insistir_gimnasio", "t-gym", at(12))
    key_marks.note_seen(conn, DAY, "insistir_ofertas", "t-ofertas", at(12))
    key_marks.record(conn, DAY, "insistir_ofertas", "not_today", at(12, 5))
    reader = Reader(tasks(), tasks(gym_task()), {})
    run(conn, at(13), reader, intentions(GYM, OFFERS_KEY, habits=[HABIT, OFFERS_HABIT]))
    assert reader.got == []


# --- Los días del hábito (decisión F2-a) -------------------------------------------------------


def test_su_tarea_clave_no_toca_un_jueves_si_su_habito_es_de_fin_de_semana():
    """Pilla `refusal` sin `off_day` (versión incorrecta 6): el 10-ene-2030 es jueves."""
    weekend = Habit.model_validate({**HABIT, "days": ["sat", "sun"]})
    assert status(habit=weekend).off_day
    assert status(habit=weekend).refusal(ONE_PM) == "key_off_day"


def test_su_tarea_clave_si_toca_el_dia_que_le_toca_y_sin_days_todos():
    thursday = Habit.model_validate({**HABIT, "days": ["thu"]})
    assert status(habit=thursday).refusal(ONE_PM) is None
    assert status().refusal(ONE_PM) is None


def test_el_dia_que_no_toca_manda_sobre_lo_hecho():
    weekend = Habit.model_validate({**HABIT, "days": ["sat", "sun"]})
    assert status(habit=weekend, marks=IntentMarks(done=True)).refusal(ONE_PM) == "key_off_day"


def test_el_dia_que_no_toca_tiene_su_why_y_la_segunda_pasada_lo_frena(conn):
    weekend = {**HABIT, "days": ["sat", "sun"]}
    its = intentions(GYM, OFFERS_KEY, habits=[weekend, OFFERS_HABIT])
    state = state_at(conn, at(13), its=its)
    entries = {entry.id: entry for entry in state.intentions}
    assert (entries["insistir_gimnasio"].due_now, entries["insistir_gimnasio"].why) == (
        False,
        "off_day",
    )
    assert state.key_tasks["insistir_gimnasio"].refusal(at(13)) == "key_off_day"
