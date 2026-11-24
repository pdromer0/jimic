"""El cierre de punta a punta (tarea 1.16): el enganche del latido pide la nota al agente, el
código pinta y envía el cierre una vez, anota lo hecho del plan y, a la mañana siguiente, calcula
la cifra definitiva de «plan cumplido».

El agente es un doble que llama a las herramientas como el motor; Todoist, el doble detrás del
lector de verdad. Datos del e08 inventado (`close_data.py`).
"""

import pytest
from agent_data import FakeAgent, ToolCall, result_message
from close_data import (
    FRIDAY,
    THURSDAY,
    CloseRunWorld,
    at,
    raw_done,
    raw_e08,
)
from todoist_data import raw_task

from jimic.channel.bot import SendError
from jimic.channel.message import render
from jimic.close.message import CLOSE_KIND, FIXED_NOTE
from jimic.heartbeat.key_tasks import KeyTaskStatus
from jimic.memory import conversations, day_plans, key_marks
from jimic.memory.database import open_database
from jimic.tools.todoist import TaskList

NOTE = "Tres de siete y la comida antes de hora: qué ansia 😄"


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


def noting(text: str = NOTE) -> FakeAgent:
    return FakeAgent([result_message(text)])


def only_close(w: CloseRunWorld) -> str:
    ((kind, message, _),) = w.sender.delivered
    assert kind == CLOSE_KIND
    return render(message, 1).text


def statuses(conn) -> dict[str, tuple]:
    return {
        i.task_id: (i.status, i.marked_at, i.marked_early) for i in day_plans.items(conn, THURSDAY)
    }


# --- El enganche --------------------------------------------------------------------------


def test_el_e08_sale_una_vez_con_la_nota_del_agente_y_cuenta_en_el_cupo(conn, tmp_path):
    """Pilla un cierre sin enviar o sin anotar en `notices`, la nota que no es la del agente o
    un aviso al agente con títulos de tareas (contenido externo)."""
    w = CloseRunWorld(conn, tmp_path, noting())
    w.with_plan()
    w.handover()
    text = only_close(w)
    assert f"🎯 {NOTE}" in text
    assert "📈 Por ahora: 3 de 7 del plan" in text
    assert "🔮 Mañana: calendario libre · 🏋️ Pierna · fuerza" in text
    prompt, options = w.agent.runs[-1]
    assert prompt.startswith("[jue 10/01 21:30] (JimiC) Toca la nota del cierre del día")
    assert "3 de 7 tareas del plan hechas" in prompt and "mañana toca Pierna · fuerza" in prompt
    for title in ("Revisar ofertas", "Cena", "Comida", "Paseo"):
        assert title not in prompt
    assert conn.execute("SELECT intent, telegram_message_id FROM notices").fetchall() == [
        ("cierre_del_dia", 951)
    ]
    assert conversations.last_message_at(conn) is None  # no es «Pedro conversa»
    assert w.writes() == []


def test_el_cierre_caduca_a_la_manana_siguiente(conn, tmp_path):
    """Valor a mano: `2030-01-11T06:00Z` (las 07:00, fin de las horas de silencio)."""
    w = CloseRunWorld(conn, tmp_path, noting())
    w.handover()
    ((_, _, expires),) = w.sender.delivered
    assert expires.isoformat() == "2030-01-11T07:00:00+01:00"


def test_lo_hecho_del_plan_queda_hecho_con_la_comida_marcada_antes_de_su_hora(conn, tmp_path):
    """Las notas del e08: Comida `done` y `marked_early`. Pilla `marked_early` sin la hora de
    Todoist, o lo hecho del plan que se queda pendiente."""
    w = CloseRunWorld(conn, tmp_path, noting())
    w.with_plan()
    w.handover()
    got = statuses(conn)
    assert got["t-comida"] == ("done", at("2030-01-10T13:20"), True)
    assert got["t-meditacion"] == ("done", at("2030-01-10T09:30"), False)
    assert got["t-trabajo"] == ("done", at("2030-01-10T16:45"), False)
    assert got["t-cena"] == ("pending", None, False)


def test_un_solo_cierre_al_dia(conn, tmp_path):
    """Pilla un segundo cierre el mismo día si el latido lo vuelve a pasar."""
    w = CloseRunWorld(conn, tmp_path, noting())
    w.handover()
    w.clock.moment = at("2030-01-10T22:15")
    w.handover()
    assert len(w.sender.delivered) == 1 and len(w.agent.runs) == 1


def test_sin_plan_ni_tareas_no_hay_cierre(conn, tmp_path):
    """Versión incorrecta 11. Pilla un cierre que molesta sin nada que cerrar, o que gasta el
    agente."""
    w = CloseRunWorld(conn, tmp_path, noting(), tasks=[])
    w.handover()
    assert w.sender.delivered == [] and w.agent.runs == []
    assert conn.execute("SELECT COUNT(*) FROM notices").fetchone() == (0,)


def test_solo_con_tareas_hay_cierre_sin_cifra(conn, tmp_path):
    w = CloseRunWorld(conn, tmp_path, noting(), tasks=raw_e08())
    w.handover()
    text = only_close(w)
    assert "⏳ Sin marcar" in text and "📈" not in text


def test_solo_con_lo_completado_hoy_tambien_hay_cierre(conn, tmp_path):
    w = CloseRunWorld(conn, tmp_path, noting(), tasks=raw_done())
    w.handover()
    assert "✅ Hecho\n▫️ Meditación" in only_close(w)


@pytest.mark.parametrize(
    "script",
    [
        [result_message(None, api_status=529, is_error=True)],
        [result_message("   \n  ")],
        RuntimeError("se cae"),
    ],
)
def test_si_el_agente_no_puede_sale_la_nota_fija(conn, tmp_path, script):
    w = CloseRunWorld(conn, tmp_path, FakeAgent(script))
    w.handover()
    assert f"🎯 {FIXED_NOTE}" in only_close(w)


def test_con_el_tope_diario_sale_la_nota_fija_sin_llamar_al_sdk(conn, tmp_path):
    w = CloseRunWorld(conn, tmp_path, noting())
    conn.execute(
        "INSERT INTO model_usage (day, model, input_tokens, output_tokens, cache_read_tokens,"
        " cache_write_tokens, usd) VALUES ('2030-01-10', 'm', 0, 0, 0, 0, '9')"
    )
    w.handover()
    assert w.agent.runs == [] and f"🎯 {FIXED_NOTE}" in only_close(w)


def test_la_nota_larga_se_recorta_a_dos_lineas(conn, tmp_path):
    w = CloseRunWorld(conn, tmp_path, noting("Una.\nDos.\nTres.\nCuatro."))
    w.handover()
    assert "🎯 Una.\nDos.\n🔮 Mañana" in only_close(w)


def test_en_el_cierre_el_agente_no_tiene_origen_y_un_cambio_de_memoria_se_rechaza(conn, tmp_path):
    """Pilla un cierre con origen: el agente podría proponer un cambio de memoria."""
    change = {"file": "preferencias.md", "operation": "add", "after": "- x", "reason": "r"}
    agent = FakeAgent(
        [
            ToolCall("propose_change", change),
            ToolCall("propose_plan", {"items": []}),
            result_message(NOTE),
        ]
    )
    w = CloseRunWorld(conn, tmp_path, agent)
    built = w.build_close()
    assert built.code == "agent"
    assert [o.is_error for o in w.agent.tool_outcomes] == [True, True]
    assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone() == (0,)


def test_con_telegram_siempre_caido_un_agente_un_reintento_la_fija_y_nada_mas(conn, tmp_path):
    w = CloseRunWorld(conn, tmp_path, noting())
    w.sender.fail = "NetworkError"
    for _ in range(3):
        with pytest.raises(SendError):
            w.handover()
    w.handover()
    assert len(w.agent.runs) == 2 and w.sender.tries == 3
    assert conn.execute("SELECT COUNT(*) FROM notices").fetchone() == (0,)


def test_con_todoist_caido_sale_con_la_valoracion_y_lo_dice(conn, tmp_path):
    w = CloseRunWorld(conn, tmp_path, noting())
    w.api.error = ConnectionError("caído")
    w.handover()
    text = only_close(w)
    assert "Ahora no puedo leer tu Todoist" in text and text.endswith("¿Cómo ha ido el día?")


def test_una_tarea_clave_hecha_por_apex_sale_hecha_y_cuenta_en_el_plan(conn, tmp_path):
    """Pilla el gimnasio hecho por APEX sin salir en «Hecho», o fuera de la cifra provisional."""
    gym = raw_task(
        "t-gimnasio", "Gimnasio", project_id="proyecto-inventado", due="2030-01-10T12:00:00"
    )
    w = CloseRunWorld(conn, tmp_path, noting(), tasks=[*raw_e08(), *raw_done(), gym])
    w.with_plan(("t-gimnasio", "t-cena"))
    status = key_status(w, "t-gimnasio", done_by="apex")
    built = w.build_close([status])
    assert built.message is not None
    text = render(built.message, 1).text
    assert "▫️ Gimnasio\n" in text and "Gimnasio (12:00)" not in text
    assert "📈 Por ahora: 1 de 2 del plan" in text


def key_status(w: CloseRunWorld, task_id: str, done_by=None) -> KeyTaskStatus:
    import asyncio

    task = asyncio.run(w.todoist.reader.get_task(task_id))
    return KeyTaskStatus(
        intent="insistir_gimnasio",
        task=task,
        done_by=done_by,
        rescheduled=False,
        not_today=False,
        paused_until=None,
        insist_from=at("2030-01-10T12:45"),
        tone_level=1,
        sent_today=0,
    )


# --- La cifra definitiva (H1-14) -----------------------------------------------------------


def closed_e08(w: CloseRunWorld) -> None:
    w.with_plan()
    w.handover()
    day_plans.set_status(w.conn, THURSDAY, "t-proyecto", "will_do")


def test_la_definitiva_cuenta_lo_marcado_despues_del_cierre(conn, tmp_path):
    """Valor a mano: la cena marcada en la app a las 22:15 → 4 de 7. Versión incorrecta 8 (la
    definitiva se fija en el cierre)."""
    w = CloseRunWorld(conn, tmp_path, noting())
    closed_e08(w)
    raw = w.api.find("t-cena")
    assert raw is not None
    raw |= {"checked": True, "completed_at": "2030-01-10T21:15:00.000000Z"}
    w.clock.moment = at("2030-01-11T07:00")
    w.settle()
    figures = day_plans.figures(conn, THURSDAY)
    assert figures is not None and (figures.fulfilled, figures.planned) == (4, 7)
    assert statuses(conn)["t-cena"] == ("done", at("2030-01-10T22:15"), False)


def test_la_hare_no_cuenta_en_la_definitiva(conn, tmp_path):
    """Versión incorrecta 9. «Proyecto personal» declarado y abierto: 3 de 7."""
    w = CloseRunWorld(conn, tmp_path, noting())
    closed_e08(w)
    w.clock.moment = at("2030-01-11T07:00")
    w.settle()
    figures = day_plans.figures(conn, THURSDAY)
    assert figures is not None and figures.fulfilled == 3
    assert statuses(conn)["t-proyecto"][0] == "will_do"


def test_antes_de_la_manana_no_se_asienta(conn, tmp_path):
    w = CloseRunWorld(conn, tmp_path, noting())
    closed_e08(w)
    w.clock.moment = at("2030-01-11T06:59")
    w.settle()
    assert day_plans.figures(conn, THURSDAY) is None


def test_una_recurrente_marcada_por_el_boton_cuenta_aunque_no_salga_completada(conn, tmp_path):
    """Una recurrente completada pasa a mañana y nunca sale completada (contrato del
    23-nov-2026): cuenta si se marcó hecha."""
    gym = raw_task(
        "t-gimnasio",
        "Gimnasio",
        project_id="proyecto-inventado",
        due="2030-01-11T12:00:00",
        recurring=True,
    )
    w = CloseRunWorld(conn, tmp_path, noting(), tasks=[gym])
    w.with_plan(("t-gimnasio",))
    day_plans.set_status(conn, THURSDAY, "t-gimnasio", "done", at("2030-01-10T13:00"))
    w.clock.moment = at("2030-01-11T07:00")
    w.settle()
    figures = day_plans.figures(conn, THURSDAY)
    assert figures is not None and figures.fulfilled == 1


def test_una_hecha_que_se_reabrio_ya_no_cuenta(conn, tmp_path):
    w = CloseRunWorld(conn, tmp_path, noting(), tasks=raw_e08())
    w.with_plan(("t-cena",))
    day_plans.set_status(conn, THURSDAY, "t-cena", "done", at("2030-01-10T21:00"))
    w.clock.moment = at("2030-01-11T07:00")
    w.settle()
    figures = day_plans.figures(conn, THURSDAY)
    assert figures is not None and figures.fulfilled == 0
    assert statuses(conn)["t-cena"][0] == "pending"


def test_si_todoist_falla_se_espera_al_siguiente_latido(conn, tmp_path):
    w = CloseRunWorld(conn, tmp_path, noting())
    closed_e08(w)
    w.clock.moment = at("2030-01-11T07:00")
    w.api.error = ConnectionError("caído")
    w.settle()
    assert day_plans.figures(conn, THURSDAY) is None
    w.api.error = None
    w.settle()
    assert day_plans.figures(conn, THURSDAY) is not None


def test_hoy_no_se_asienta_y_un_dia_viejo_si(conn, tmp_path):
    w = CloseRunWorld(conn, tmp_path, noting(), tasks=raw_e08())
    w.with_plan(("t-cena",))
    day_plans.record_plan(conn, FRIDAY, at("2030-01-11T07:45"), [("t-cena", None)], {})
    w.clock.moment = at("2030-01-12T10:00")
    w.settle()
    assert day_plans.figures(conn, THURSDAY) is not None
    assert day_plans.figures(conn, FRIDAY) is not None
    assert day_plans.unsettled(conn, at("2030-01-13T00:00").date()) == []


def test_una_recurrente_cerrada_por_el_boton_sale_hecha_y_cuenta_en_la_provisional(conn, tmp_path):
    """La recurrente que cerró [Ya entrené] ya está en mañana y no sale entre las completadas:
    pilla un cierre que no la cuenta (la marca `done` lleva su id)."""
    gym = raw_task(
        "t-gimnasio",
        "Gimnasio",
        project_id="proyecto-inventado",
        due="2030-01-11T12:00:00",
        recurring=True,
    )
    w = CloseRunWorld(conn, tmp_path, noting(), tasks=[*raw_e08(), gym])
    w.with_plan(("t-gimnasio", "t-cena"))
    key_marks.record(
        conn, THURSDAY, "insistir_gimnasio", "done", at("2030-01-10T13:30"), "t-gimnasio"
    )
    built = w.build_close(
        [key_status(w, "t-cena", done_by="button").model_copy(update={"task": None})]
    )
    assert built.message is not None
    text = render(built.message, 1).text
    assert "✅ Hecho\n▫️ Gimnasio" in text and "📈 Por ahora: 1 de 2 del plan" in text


def test_sin_plan_con_solo_una_tarea_clave_hecha_por_apex_hay_cierre(conn, tmp_path):
    """Revisión propia: su tarea sigue abierta pero ya no está «sin marcar»; «Hecho» la enseña.
    Pilla «nada que cerrar» mirando solo las completadas en Todoist."""
    gym = raw_task(
        "t-gimnasio", "Gimnasio", project_id="proyecto-inventado", due="2030-01-10T12:00:00"
    )
    w = CloseRunWorld(conn, tmp_path, noting(), tasks=[gym])
    built = w.build_close([key_status(w, "t-gimnasio", done_by="apex")])
    assert built.message is not None and "✅ Hecho\n▫️ Gimnasio" in render(built.message, 1).text


def test_sin_plan_y_con_las_completadas_caidas_hay_cierre(conn, tmp_path):
    """No se sabe si hubo algo hecho: más vale el cierre que callar."""
    w = CloseRunWorld(conn, tmp_path, noting(), tasks=[])

    async def down() -> TaskList:
        return TaskList.unavailable("timeout")

    setattr(w.todoist.reader, "read_completed_today", down)  # noqa: B010 - un doble de un método
    built = w.build_close()
    assert built.message is not None
