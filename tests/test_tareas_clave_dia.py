"""Las tareas clave a lo largo de un día, con el latido entero y dobles (tarea 1.15, CU-02).

El modelo es un doble que imita el criterio de Haiku que la evaluación mide aparte: insiste con
una tarea clave cuando el estado lo permite y han pasado 45 minutos desde su último aviso (el
espacio entre avisos es criterio del modelo, 1.15-i). Lo que se prueba es lo que garantiza el
código: los topes, los botones y lo que hace cada toque. Todoist es el doble detrás del lector y
el escritor de verdad; el canal, un doble que guarda cada mensaje con botones como aprobación.
Todo es inventado. El día es el jueves 10/01/2030, el de la especificación.
"""

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from apex_data import invented_snapshot
from conftest import FixedClock
from heartbeat_data import (
    BINS,
    GYM,
    HABIT,
    MADRID,
    CountingApex,
    CountingCalendar,
    FakeSender,
    RunawayDouble,
    calendar_with,
    intentions,
    limits,
)
from todoist_data import FakeTodoistApi, raw_task

from jimic.agent.proposals import TaskSnapshot
from jimic.channel import store
from jimic.channel.message import InteractiveMessage, Tap
from jimic.channel.taps import process_tap
from jimic.clock import to_db
from jimic.execution.execute import Executor
from jimic.execution.key_tasks import KeyTaskActions
from jimic.heartbeat.beat import Heartbeat
from jimic.heartbeat.cycle import Decision
from jimic.heartbeat.key_message import KEY_KIND
from jimic.heartbeat.limits import Outgoing
from jimic.heartbeat.sources import Sources
from jimic.heartbeat.state import DayState
from jimic.memory import key_marks
from jimic.memory.database import open_database
from jimic.memory.notices import last_sent_of
from jimic.tools.todoist import ApiTodoistReader, ApiTodoistWriter, TodoistTools

DAY = date(2030, 1, 10)
PROJECT = "proyecto-inventado"
OFFERS_KEY = {**GYM, "id": "insistir_ofertas", "habit": "ofertas", "default_time": "17:15"}
OFFERS_HABIT = {
    **HABIT,
    "id": "ofertas",
    "name": "Revisar ofertas",
    "todoist_task": "Revisar ofertas",
}
SPACING = timedelta(minutes=45)
MAX_DECISIONS = 120


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2030, 1, 10, hour, minute, tzinfo=MADRID)


def beats(start: datetime, end: datetime) -> list[datetime]:
    """Every quarter of an hour from `start` to `end`, both included."""
    moments, moment = [], start
    while moment <= end:
        moments.append(moment)
        moment += timedelta(minutes=15)
    return moments


class PacedModel:
    """Stands in for Haiku: it insists with each key task the state lets out, 45 minutes after
    its last notice. `forced` picks these intents whatever the state says (to test the code)."""

    model = "doble"

    def __init__(self, conn) -> None:
        self.conn = conn
        self.states: list[DayState] = []
        self.forced: tuple[str, ...] = ()

    async def __call__(self, state: DayState) -> Decision:
        self.states.append(state)
        if len(self.states) > MAX_DECISIONS:
            raise RunawayDouble("the model was asked in a loop")
        chosen = list(self.forced)
        for intent, status in state.key_tasks.items():
            if intent in chosen or status.refusal(state.now) is not None:
                continue
            left = next(i.left_today for i in state.intentions if i.id == intent)
            last = last_sent_of(self.conn, [intent], state.now)
            if left > 0 and (last is None or state.now - last >= SPACING):
                chosen.append(intent)
        if not chosen:
            return Decision(False, None, "nada")
        texts = tuple(Outgoing(intent=i, text=f"Aviso inventado de {i}") for i in chosen)
        return Decision(True, ",".join(chosen), "doble", texts)


class StoringSender(FakeSender):
    """The channel: a message with buttons is saved as an approval, as `deliver` does."""

    def __init__(self, conn, clock) -> None:
        super().__init__()
        self.conn = conn
        self.clock = clock
        self.approvals: list[int] = []

    async def deliver(self, kind: str, content: Any, expires_at: datetime) -> int:
        message_id = await super().deliver(kind, content, expires_at)
        approval = store.create(self.conn, self.clock, kind, content, expires_at)
        store.set_message_id(self.conn, approval, message_id)
        self.approvals.append(approval)
        return message_id


class Day:
    def __init__(self, conn, tasks, items=(GYM, OFFERS_KEY), training=True) -> None:
        self.conn = conn
        self.clock = FixedClock(at(7))
        self.api = FakeTodoistApi(tasks)
        self.todoist = TodoistTools(
            ApiTodoistReader(self.api, self.clock, MADRID),
            ApiTodoistWriter(self.api, MADRID),
            self.api.close,
        )
        self.its = intentions(*items, habits=[{**HABIT, "training": training}, OFFERS_HABIT])
        self.model = PacedModel(conn)
        self.sender = StoringSender(conn, self.clock)
        self.heartbeat = Heartbeat(
            clock=self.clock,
            timezone=MADRID,
            limits=limits(),
            intentions=self.its,
            sources=Sources(
                todoist=self.todoist,
                calendar=CountingCalendar(calendar_with()),
                apex=CountingApex(invented_snapshot("2030-01-10 09:00")),
            ),
            conn=conn,
            decider=self.model,
            sender=self.sender,
            icons=lambda: {"gimnasio": "🏋️"},
        )
        self.keys = KeyTaskActions(
            conn=conn, clock=self.clock, timezone=MADRID, todoist=self.todoist
        )
        self.executor = Executor(
            conn=conn, clock=self.clock, timezone=MADRID, limits=limits(), todoist=self.todoist
        )

    def run(self, moments: list[datetime]) -> None:
        for moment in moments:
            self.clock.moment = moment
            # The double's cap is per heartbeat here: a whole day of them passes it honestly.
            self.api.calls.clear()
            asyncio.run(self.heartbeat.beat())

    def press(self, approval: int, item: int, option: str):
        return asyncio.run(
            process_tap(self.conn, self.clock, self.keys.handlers, Tap(approval, item, option))
        )

    def complete_in_app(self, task_id: str, moment: datetime) -> None:
        """Pedro marks it in the Todoist app at `moment`."""
        self.clock.moment = moment
        self.api.completed_at = to_db(moment).replace("+00:00", ".000000Z")
        asyncio.run(self.api.complete_task(task_id))

    def sent(self, intent: str) -> list[str]:
        rows = self.conn.execute(
            "SELECT sent_at FROM notices WHERE intent = ? ORDER BY sent_at", (intent,)
        ).fetchall()
        return [datetime.fromisoformat(at).astimezone(MADRID).strftime("%H:%M") for (at,) in rows]

    def reasons(self) -> list[str]:
        return [reason for (reason,) in self.conn.execute("SELECT reason FROM decisions")]


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


def tasks_of_the_day() -> list[dict[str, Any]]:
    return [
        raw_task("t-gym", "Gimnasio", PROJECT, due="2030-01-10T12:00:00"),
        raw_task("t-ofertas", "Revisar ofertas", PROJECT, due="2030-01-10T16:30:00"),
    ]


# --- El día de ejemplo (criterio del issue) ----------------------------------------------------


def test_el_dia_de_ejemplo_da_dos_avisos_y_ninguno_repetido(conn):
    """12:45 primer aviso; 13:30 el segundo y [Más tarde] a las 20:00; las ofertas, hechas en la
    app a las 17:10; [Ya entrené] a las 20:15 con el botón del segundo aviso. Valor a mano del
    plan: dos avisos, 12:45 y 13:30."""
    day = Day(conn, tasks_of_the_day())
    day.run(beats(at(12), at(13, 30)))
    assert day.sent("insistir_gimnasio") == ["12:45", "13:30"]
    levels = [s.key_tasks["insistir_gimnasio"].tone_level for s in day.model.states]
    assert levels[3] == 1 and levels[6] == 2  # el estado de las 12:45 y el de las 13:30
    second = day.sender.approvals[-1]
    # [⏰ Más tarde] y, con la respuesta de Pedro, el agente mueve la tarea a las 20:00.
    day.clock.moment = at(13, 32)
    assert day.press(second, 1, "later").send == ("¿A qué hora? ⏰",)
    task = asyncio.run(day.todoist.reader.get_task("t-gym"))
    assert task is not None
    snapshot = TaskSnapshot.of(task).model_dump()
    move = {"action": "move", "task_id": "t-gym", "due": "2030-01-10T20:00", "snapshot": snapshot}
    asyncio.run(day.executor.now(move))
    day.run(beats(at(13, 45), at(17)))
    day.complete_in_app("t-ofertas", at(17, 10))
    day.run(beats(at(17, 15), at(20)))
    day.clock.moment = at(20, 15)
    done = day.press(second, 1, "done")
    assert done.code == "resolved"
    day.run(beats(at(20, 15), at(22, 45)))
    assert day.sent("insistir_gimnasio") == ["12:45", "13:30"]
    assert day.sent("insistir_ofertas") == []
    statuses = [s.key_tasks for s in day.model.states]
    assert statuses[-1]["insistir_gimnasio"].done_by == "button"
    assert statuses[-1]["insistir_ofertas"].done_by == "todoist"


def test_aunque_el_modelo_insista_la_tarea_hecha_no_sale(conn):
    """El código lo garantiza: con [Ya entrené], el modelo que insiste a la fuerza se frena."""
    day = Day(conn, tasks_of_the_day())
    day.run([at(12, 45)])
    day.clock.moment = at(12, 50)
    day.press(day.sender.approvals[-1], 1, "done")
    day.model.forced = ("insistir_gimnasio",)
    day.run([at(13, 30), at(14, 30)])
    assert day.sent("insistir_gimnasio") == ["12:45"]
    assert all("block key_done" in reason for reason in day.reasons()[-2:])


# --- Completar por cada fuente corta en el siguiente latido ------------------------------------


def test_completada_en_la_app_corta_en_el_siguiente_latido(conn):
    day = Day(conn, tasks_of_the_day())
    day.run([at(12, 45)])
    day.complete_in_app("t-gym", at(12, 50))
    day.model.forced = ("insistir_gimnasio",)
    day.run([at(13, 30)])
    assert day.sent("insistir_gimnasio") == ["12:45"]
    assert day.model.states[-1].key_tasks["insistir_gimnasio"].done_by == "todoist"


def test_una_recurrente_completada_en_la_app_salta_y_corta(conn):
    """1.15-j: no sale en las completadas, pero la vista de las 12:45 está mañana."""
    gym = raw_task("t-gym", "Gimnasio", PROJECT, due="2030-01-10T12:00:00", recurring=True)
    day = Day(conn, [gym])
    day.run([at(12, 45)])
    day.complete_in_app("t-gym", at(12, 50))
    day.model.forced = ("insistir_gimnasio",)
    day.run([at(13, 30)])
    status = day.model.states[-1].key_tasks["insistir_gimnasio"]
    assert status.rescheduled and status.done_by is None
    assert day.sent("insistir_gimnasio") == ["12:45"]
    assert "block key_rescheduled" in day.reasons()[-1]


def test_el_entreno_en_apex_corta_en_el_habito_del_entreno(conn):
    day = Day(conn, tasks_of_the_day())
    trained = invented_snapshot("2030-01-10 09:00").model_copy(update={"today_workout": "trained"})
    day.heartbeat._sources = Sources(
        todoist=day.todoist,
        calendar=CountingCalendar(calendar_with()),
        apex=CountingApex(trained),
    )
    day.model.forced = ("insistir_gimnasio",)
    day.run([at(12, 45)])
    assert day.sent("insistir_gimnasio") == []
    assert day.model.states[-1].key_tasks["insistir_gimnasio"].done_by == "apex"


def test_hoy_no_corta_el_resto_del_dia_y_deshacer_la_devuelve(conn):
    day = Day(conn, tasks_of_the_day())
    day.run([at(12, 45)])
    day.clock.moment = at(12, 50)
    followup = day.press(day.sender.approvals[-1], 1, "skip").messages[0]
    day.model.forced = ("insistir_gimnasio",)
    day.run([at(13, 30), at(18), at(21)])
    assert day.sent("insistir_gimnasio") == ["12:45"]
    undo = store.create(conn, day.clock, followup.kind, followup.message, followup.expires_at)
    asyncio.run(process_tap(conn, day.clock, day.keys.handlers, Tap(undo, 1, "undo")))
    day.run([at(21, 15)])
    assert day.sent("insistir_gimnasio") == ["12:45", "21:15"]


def test_voy_ahora_pausa_lo_que_dura_el_habito_y_despues_vuelve(conn):
    """[Voy ahora] a las 13:30, 75 min: pausa hasta las 14:45 (valor a mano)."""
    day = Day(conn, tasks_of_the_day())
    day.run([at(12, 45)])
    day.clock.moment = at(13, 30)
    day.press(day.sender.approvals[-1], 1, "go")
    day.model.forced = ("insistir_gimnasio",)
    day.run([at(13, 45), at(14, 30)])
    assert day.sent("insistir_gimnasio") == ["12:45"]
    day.run([at(14, 45)])
    assert day.sent("insistir_gimnasio") == ["12:45", "14:45"]


def test_nunca_mas_de_max_per_day(conn):
    day = Day(conn, tasks_of_the_day())
    day.model.forced = ("insistir_gimnasio",)
    day.run(beats(at(12, 45), at(15)))
    assert len(day.sent("insistir_gimnasio")) == 3


# --- El mensaje que sale -----------------------------------------------------------------------


def test_un_aviso_y_una_tarea_clave_van_juntos_con_botones_solo_de_la_tarea(conn):
    day = Day(conn, tasks_of_the_day(), items=(GYM, BINS))
    day.model.forced = ("basura",)
    day.run([at(21)])  # gimnasio abierto y sin avisos: insiste; la basura, a su hora
    (kind, message, expires) = day.sender.delivered[-1]
    assert kind == KEY_KIND and isinstance(message, InteractiveMessage)
    assert [(item.data or {})["intent"] for item in message.items] == ["insistir_gimnasio"]
    assert "Aviso inventado de basura" in (message.body or "")
    assert expires == datetime(2030, 1, 11, 0, 0, tzinfo=MADRID)
    rows = conn.execute("SELECT intent, telegram_message_id FROM notices").fetchall()
    assert {intent for intent, _ in rows} == {"basura", "insistir_gimnasio"}
    assert len({message_id for _, message_id in rows}) == 1  # un solo mensaje


def test_un_mensaje_sin_tarea_clave_sigue_siendo_texto(conn):
    day = Day(conn, tasks_of_the_day(), items=(BINS,))
    day.model.forced = ("basura",)
    day.run([at(21)])
    assert day.sender.delivered == [] and day.sender.texts == ["Aviso inventado de basura"]


def test_si_el_mensaje_con_botones_no_cabe_sale_como_texto(conn):
    day = Day(conn, tasks_of_the_day())
    day.sender.too_long = True
    day.run([at(12, 45)])
    assert day.sender.texts == ["Aviso inventado de insistir_gimnasio"]
    assert day.sent("insistir_gimnasio") == ["12:45"]


def test_el_latido_anota_la_tarea_de_hoy_que_ve(conn):
    day = Day(conn, tasks_of_the_day())
    day.run([at(12), at(12, 15)])
    assert key_marks.of_day(conn, DAY).of("insistir_gimnasio").seen == {"t-gym"}
    (count,) = conn.execute(
        "SELECT COUNT(*) FROM key_task_marks WHERE mark = 'seen' AND intent = ?",
        ("insistir_gimnasio",),
    ).fetchone()
    assert count == 1
    assert to_db(at(12))  # los momentos van en UTC
