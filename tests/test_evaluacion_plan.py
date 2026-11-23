"""`jimic evaluar plan` (decisión 1.14-f): el catálogo de comprobaciones, la carga de
`evals/plan.yaml`, una pasada entera con el doble del agente y el informe.

Ningún test llama al agente de verdad: el motor es `FakeAgent`, que pasa por los *hooks* y el
servidor de las opciones. Todo es inventado.
"""

import asyncio
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from agent_data import FakeAgent, ToolCall, result_message
from conftest import VALID_ENV, FixedClock, write_env
from evals_data import use_dev

import jimic.cli
from jimic.config import Settings
from jimic.evals import command
from jimic.evals.plan import PlanOutcome, plan_score, run_pass
from jimic.evals.plan_checks import CheckError, CheckInput, parse_check, run_check
from jimic.evals.plan_report import compare
from jimic.evals.plan_scenarios import load_plan_suite
from jimic.evals.scenarios import EvalError
from jimic.memory.schemas import Habit
from jimic.paths import resolve_paths
from jimic.tools.calendar import CalendarEvent
from jimic.tools.todoist import TodoistTask

REAL_FILE = Path(__file__).parents[1] / "evals" / "plan.yaml"
MADRID = ZoneInfo("Europe/Madrid")
NOW = datetime(2030, 1, 10, 7, 45, tzinfo=MADRID)
START = datetime(2030, 1, 20, 10, 30, tzinfo=MADRID)
HABITS = [
    Habit.model_validate(
        {
            "id": "gimnasio",
            "name": "Gimnasio",
            "priority": "high",
            "duration": "75m",
            "moment": "midday",
            "todoist_task": "Gimnasio",
        }
    )
]


def at(hhmm: str, day: int = 10) -> datetime:
    hour, minute = map(int, hhmm.split(":"))
    return datetime(2030, 1, day, hour, minute, tzinfo=MADRID)


def task(task_id: str, title: str, due, minutes: int | None = None) -> TodoistTask:
    return TodoistTask(
        id=task_id,
        title=title,
        project_id="p",
        due=due,
        is_recurring=False,
        has_subtasks=False,
        duration_minutes=minutes,
    )


def event(title: str, start: str, end: str) -> CalendarEvent:
    return CalendarEvent(
        title=title,
        start=at(start),
        end=at(end),
        all_day=False,
        organizer_is_me=True,
        declined=False,
    )


def create(title: str, due: str, minutes: int | None = None) -> dict:
    return {"action": "create", "title": title, "due": due, "duration_minutes": minutes}


def move(task_id: str, due: str, title: str = "Tarea") -> dict:
    snapshot = {"title": title, "due": None, "project_id": "p"}
    return {"action": "move", "task_id": task_id, "due": due, "snapshot": snapshot}


def delete(task_id: str) -> dict:
    return {"action": "delete", "task_id": task_id, "snapshot": {"title": "x", "due": None}}


MEETING = event("Reunión de equipo", "10:00", "11:00")
LUNCH = task("t-comida", "Comida", at("14:00"), 60)


def check(raw, *items, tasks=(LUNCH,), events=(MEETING,), now=NOW, **extra) -> bool:
    data = CheckInput(
        now=now,
        items=list(items),
        tasks=list(tasks),
        events=list(events),
        habits=HABITS,
        **extra,
    )
    return run_check(parse_check(raw), data)


# --- El catálogo: cada comprobación, con un caso que pasa y otro que no -------------------------


def test_no_overlap_contiguo_no_solapa_y_encima_de_un_evento_si():
    """Versión incorrecta 9: con intervalos cerrados, 11:00 tras una reunión de 10 a 11
    solaparía."""
    assert check("no_overlap", create("Gimnasio", "2030-01-10T11:00", 75))
    assert not check("no_overlap", create("Gimnasio", "2030-01-10T10:30", 30))


def test_no_overlap_una_tarea_con_hora_y_sin_duracion_es_un_instante():
    lone = task("t1", "Llamar", at("12:00"))
    assert not check("no_overlap", create("Gimnasio", "2030-01-10T11:30", 60), tasks=(lone,))
    assert check("no_overlap", create("Gimnasio", "2030-01-10T12:01", 60), tasks=(lone,))


def test_no_overlap_lo_que_se_borra_o_se_mueve_libera_su_hueco():
    stretch = task("t-estir", "Estiramientos", at("14:30"), 15)
    offers = create("Revisar ofertas", "2030-01-10T14:30", 30)
    assert not check("no_overlap", offers, tasks=(stretch,))
    assert check("no_overlap", offers, delete("t-estir"), tasks=(stretch,))


def test_no_overlap_entre_dos_propuestas_y_con_el_evento_de_su_mismo_titulo():
    a, b = create("A", "2030-01-10T12:00", 60), create("B", "2030-01-10T12:30", 15)
    assert not check("no_overlap", a, b)
    webinar = task("t-web", "Webinar", at("10:00"), 60)
    same = event("Webinar", "09:50", "11:00")
    assert check("no_overlap", move("t-web", "2030-01-10T09:50"), tasks=(webinar,), events=(same,))


def test_includes_habit_por_crear_o_por_traer_a_hoy():
    assert check({"includes_habit": "gimnasio"}, create("Gimnasio (pierna)", "2030-01-10T12:00"))
    gym = task("t-gym", "Gimnasio", at("13:00", 9), 75)
    assert check({"includes_habit": "gimnasio"}, move("t-gym", "2030-01-10T12:00"), tasks=(gym,))
    assert not check({"includes_habit": "gimnasio"}, move("t-gym", "2030-01-11"), tasks=(gym,))
    assert not check({"includes_habit": "gimnasio"}, create("Leer", "2030-01-10"))


def test_includes_or_left_out_y_left_out_or_absent():
    out = {"left_out": [{"habit": "gimnasio", "line": "hoy no cabe"}]}
    assert check({"includes_or_left_out": "gimnasio"}, plan=out)
    assert not check({"includes_or_left_out": "gimnasio"})
    assert check({"left_out_or_absent": "gimnasio"})
    assert not check({"left_out_or_absent": "gimnasio"}, create("Gimnasio", "2030-01-10"))


def test_un_habito_desconocido_es_fallo():
    with pytest.raises(CheckError):
        check({"includes_habit": "natacion"})


def test_max_new():
    assert check({"max_new": 1}, create("A", "2030-01-10"))
    assert not check({"max_new": 1}, create("A", "2030-01-10"), create("B", "2030-01-10"))


def test_no_duplicates_por_titulo_y_sin_contar_lo_borrado():
    assert not check("no_duplicates", create("comida", "2030-01-10"))
    assert not check("no_duplicates", create("Reunión  de equipo", "2030-01-10"))
    assert not check("no_duplicates", create("A", "2030-01-10"), create("a", "2030-01-11"))
    assert check("no_duplicates", create("Comida", "2030-01-10"), delete("t-comida"))
    assert check("no_duplicates", create("Cena", "2030-01-10"))


def test_due_from_now():
    assert check("due_from_now", create("A", "2030-01-10T07:45"), create("B", "2030-01-10"))
    assert not check("due_from_now", create("A", "2030-01-10T07:44"))
    assert not check("due_from_now", move("t1", "2030-01-09"))


def test_no_move_no_item_on_deletes_exactly_no_delete_y_no_deletes():
    assert check({"no_move": "t1"}, delete("t1")) and not check(
        {"no_move": "t1"}, move("t1", "2030-01-11")
    )
    assert not check({"no_item_on": ["t1", "t2"]}, delete("t2"))
    assert check({"no_item_on": ["t1", "t2"]}, move("t3", "2030-01-11"))
    assert check({"deletes_exactly": ["t1", "t2"]}, delete("t2"), delete("t1"))
    assert not check({"deletes_exactly": ["t1", "t2"]}, delete("t1"))
    assert not check({"deletes_exactly": ["t1"]}, delete("t1"), delete("t2"))
    assert check({"no_delete": "t1"}, delete("t2")) and not check({"no_delete": "t1"}, delete("t1"))
    assert check("no_deletes", create("A", "2030-01-10")) and not check("no_deletes", delete("t1"))


@pytest.mark.parametrize(
    ("due", "target", "ok"),
    [
        ("2030-01-10T09:50", "09:50", True),
        ("2030-01-10T09:50", "9:50", True),
        ("2030-01-10T10:00", "09:50", False),
        ("2030-01-10", "hoy", True),
        ("2030-01-11T08:00", "mañana", True),
        ("2030-01-12", "mañana", False),
    ],
)
def test_moves(due, target, ok):
    assert check({"moves": ["t1", target]}, move("t1", due)) is ok


def test_no_create_title_contiene_sin_mayusculas():
    assert not check({"no_create_title": "Gimnasio"}, create("gimnasio pierna", "2030-01-10"))
    assert check({"no_create_title": ["Gimnasio", "Cena"]}, create("Leer", "2030-01-10"))


def test_creates_con_su_hora_y_su_fin():
    raw = {"creates": ["Revisar ofertas", "14:30", "15:00"]}
    assert check(raw, create("Revisar ofertas", "2030-01-10T14:30", 30))
    assert not check(raw, create("Revisar ofertas", "2030-01-10T14:30", 45))
    assert not check(raw, create("Revisar ofertas", "2030-01-10T14:00", 30))


def test_las_lineas_del_mensaje_pintado_y_la_memoria():
    painted = "¡Pedro! Hola\n\n🧠 Desde ayer he aprendido: x\n🏋️ Toca pierna"
    assert check("learned_line", painted=painted)
    assert not check("learned_line", painted="¡Pedro! Hola")
    plan = {"training_line": "Toca pierna"}
    assert check("training_line", plan=plan, painted=painted)
    assert not check("training_line", painted=painted)
    assert check("no_memory_proposal") and not check("no_memory_proposal", memory_proposals=1)


@pytest.mark.parametrize("raw", ["inventada", {"max_new": [1, 2]}, {"creates": ["a"]}, 3])
def test_una_comprobacion_fuera_del_catalogo_o_mal_escrita_se_rechaza(raw):
    with pytest.raises(CheckError):
        parse_check(raw)


# --- La carga ----------------------------------------------------------------------------------


def test_el_fichero_real_carga_los_diez_escenarios_seis_del_piloto_y_el_e02_y_la_reserva():
    suite = load_plan_suite(REAL_FILE)
    assert [scenario.id for scenario in suite.scenarios] == [f"p{n:02d}" for n in range(1, 11)] + [
        "r01",
        "r02",
        "r03",
    ]
    origins = [scenario.origin or "" for scenario in suite.scenarios]
    assert origins.count("nuevo") == 3
    assert origins.count("reserva") == 3  # el conjunto de reserva del chat
    assert sorted(o for o in origins if o not in ("nuevo", "reserva")) == [
        "e01",
        "e02",
        "e03",
        "e04",
        "e06",
        "e07",
        "e09",
    ]


def variant(tmp_path: Path, extra: str) -> Path:
    path = tmp_path / "plan.yaml"
    path.write_text(REAL_FILE.read_text(encoding="utf-8") + extra, encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("\n  - {id: p99, title: x, checks: [inventada]}\n", "comprobación desconocida"),
        ("\n  - {id: p99, title: x, checks_after: [no_overlap]}\n", "checks_after sin mensajes"),
        (
            "\n  - {id: p99, title: x, checks_after: [learned_line],"
            " after: [{at: '9:00', text: a}]}\n",
            "solo vale en el plan",
        ),
        ("\n  - {id: p01, title: x, checks: [no_overlap]}\n", "repetido"),
        ("\n  - {id: p99, title: x}\n", "checks"),
    ],
)
def test_un_fichero_que_no_vale_falla_antes_de_gastar(tmp_path, extra, message):
    with pytest.raises(EvalError, match=message):
        load_plan_suite(variant(tmp_path, extra))


def test_cambiar_las_comprobaciones_cambia_la_huella_solo_de_ese_escenario(tmp_path):
    """La comparación con el informe anterior se hace solo sobre lo que no cambió (1.9-e)."""
    original = REAL_FILE.read_text(encoding="utf-8")
    old = "      - {max_new: 4}\n      - no_overlap\n      - no_duplicates"
    assert original.count(old) == 1
    changed = tmp_path / "plan.yaml"
    changed.write_text(
        original.replace(old, "      - {max_new: 3}\n      - no_overlap\n      - no_duplicates"),
        encoding="utf-8",
    )
    before = {s.id: s.fingerprint for s in load_plan_suite(REAL_FILE).scenarios}
    after = {s.id: s.fingerprint for s in load_plan_suite(changed).scenarios}
    assert [id for id in before if before[id] != after[id]] == ["p01"]


# --- Una pasada entera, con el doble del agente ------------------------------------------------

GOOD_PLAN = {
    "greeting": "Jueves de pierna",
    "training_line": "Pierna · fuerza a mediodía",
    "items": [
        {
            "action": "create",
            "title": "Gimnasio",
            "due": "2030-01-10T12:00",
            "duration_minutes": 75,
            "icon": "🏋️",
            "reason": "toca pierna",
        },
        {
            "action": "create",
            "title": "Revisar ofertas",
            "due": "2030-01-10T17:00",
            "duration_minutes": 30,
            "icon": "💼",
            "reason": "hábito de la tarde",
        },
    ],
}


def agent(plan: dict) -> FakeAgent:
    return FakeAgent(
        [
            ToolCall("read_calendar", {}),
            ToolCall("read_tasks", {}),
            ToolCall("read_apex_today", {}),
            ToolCall("propose_plan", plan),
            result_message("Listo"),
        ]
    )


def one_pass(plan: dict, scenario_id: str = "p01"):
    suite = load_plan_suite(REAL_FILE)
    (scenario,) = suite.select([scenario_id])
    settings = Settings.model_validate(VALID_ENV)
    fake = agent(plan)
    return asyncio.run(run_pass(settings, suite, scenario, fake)), fake


def test_una_pasada_buena_cumple_todo_y_pinta_el_plan():
    result, fake = one_pass(GOOD_PLAN)
    assert result.code == "agent" and result.ok, result.failed
    assert result.painted.startswith("¡Pedro! Jueves de pierna")
    assert "💼 Revisar ofertas · 17:00–17:30 (hábito de la tarde)" in result.painted
    prompt, options = fake.runs[0]
    assert prompt.startswith("[jue 10/01 07:45] (JimiC) Toca proponer el plan del día")
    # El motor del agente guarda sus ficheros en la carpeta temporal de la pasada.
    assert "/jimic-plan-" in options.env["CLAUDE_CONFIG_DIR"]


def test_una_pasada_que_pisa_la_reunion_falla_por_esa_comprobacion():
    bad = {**GOOD_PLAN, "items": [{**GOOD_PLAN["items"][0], "due": "2030-01-10T10:30"}]}
    result, _ = one_pass(bad)
    assert not result.ok
    assert set(result.failed) == {"includes_habit(ofertas)", "no_overlap"}


def test_si_sale_el_plan_minimo_la_pasada_falla_aunque_cumpla():
    broken = FakeAgent([result_message(None, api_status=529, is_error=True)])
    suite = load_plan_suite(REAL_FILE)
    (scenario,) = suite.select(["p01"])
    result = asyncio.run(run_pass(Settings.model_validate(VALID_ENV), suite, scenario, broken))
    assert result.code == "minimal:agent:api:529" and not result.ok
    assert result.failed[0] == "minimal:agent:api:529"


def test_la_pasada_escribe_los_cambios_de_ayer_y_sale_la_linea_aprendida():
    plan = {**GOOD_PLAN, "items": [GOOD_PLAN["items"][0]]}
    result, _ = one_pass(plan, "p06")
    assert "🧠 Desde ayer he aprendido: preferencias.md · cambiado" in result.painted
    assert result.ok, result.failed


# --- El comando y el informe -------------------------------------------------------------------


def evaluate(plan: dict = GOOD_PLAN, ids=("p01",), passes=1):
    return command.run_plan(
        os.environ,
        list(ids),
        passes,
        eval_file=REAL_FILE,
        run_agent=agent(plan),
        clock=FixedClock(START),
    )


def test_con_prod_se_niega_sin_leer_el_env(monkeypatch):
    monkeypatch.setenv("JIMIC_ENTORNO", "prod")
    with pytest.raises(EvalError, match="solo corre con ENTORNO=dev"):
        evaluate()


def test_la_cli_tiene_evaluar_plan_y_se_niega_con_prod(monkeypatch, capsys):
    monkeypatch.setenv("JIMIC_ENTORNO", "prod")
    write_env(VALID_ENV)
    assert jimic.cli.main(["evaluar", "plan", "--escenario", "p01"]) == 2
    assert "solo corre con ENTORNO=dev" in capsys.readouterr().err


def test_el_informe_tiene_sus_dos_ficheros_con_el_plan_pintado_y_compara(monkeypatch):
    use_dev(monkeypatch)
    first = evaluate()
    assert first.json_path.name == "plan-20300120-1030.json"
    assert "Nota: 100,0 % (1/1 pasadas cumplen todas sus comprobaciones) ✅" in first.screen
    document = json.loads(first.json_path.read_text(encoding="utf-8"))
    (scenario,) = document["scenarios"]
    assert scenario["passes"][0]["painted"].startswith("¡Pedro!")
    assert "```text\n¡Pedro! Jueves de pierna" in first.md_path.read_text(encoding="utf-8")
    second = evaluate({**GOOD_PLAN, "items": GOOD_PLAN["items"][:1]})
    assert second.json_path.name == "plan-20300120-1030-2.json"
    assert "Comparación con plan-20300120-1030.json:" in second.screen
    assert "100,0 % → 0,0 %" in second.screen and "⚠️ Regresión" in second.screen
    assert "p01 (0/1 bien): includes_habit(ofertas)" in second.screen
    assert not (resolve_paths(os.environ).data_dir / "jimic.sqlite3").exists()


def test_la_regresion_es_de_mas_de_cinco_puntos():
    before = [PlanOutcome("a", "h", (True,) * 20)]
    exactly = [PlanOutcome("a", "h", (True,) * 19 + (False,))]
    assert not compare("x", exactly, before).worse
    worse = [PlanOutcome("a", "h", (True,) * 18 + (False,) * 2)]
    assert compare("x", worse, before).worse
    assert plan_score(worse).right == 18


def test_un_escenario_cambiado_no_se_compara():
    before = [PlanOutcome("a", "h1", (True,)), PlanOutcome("b", "h", (True,))]
    now = [PlanOutcome("a", "h2", (False,)), PlanOutcome("c", "h", (True,))]
    result = compare("x", now, before)
    assert (result.changed, result.new, result.removed, result.before) == (
        ("a",),
        ("c",),
        ("b",),
        None,
    )


def test_con_el_plan_minimo_no_se_puntua_lo_que_el_agente_propuso(monkeypatch):
    """Hallazgo de la revisión propia: si el plan del agente no cabe y sale el mínimo, la pasada
    falla por su código y sus comprobaciones no miran elementos que Pedro no vio."""
    import jimic.plan.runner as runner
    from jimic.channel.message import MessageTooLong
    from jimic.plan import minimal

    real = runner.paint

    def too_long(content, *args):
        if content.greeting != minimal.GREETING:
            raise MessageTooLong("inventado")
        return real(content, *args)

    monkeypatch.setattr(runner, "paint", too_long)
    result, _ = one_pass(GOOD_PLAN)
    assert result.code == "minimal:too_long" and not result.ok
    assert result.plan is not None and result.plan.items == ()
