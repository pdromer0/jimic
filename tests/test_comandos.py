"""`/memoria` y `/deshacer` (CU-15): los atiende el código, sin el agente.

Con ficheros personales inventados en una carpeta temporal, Todoist sobre su doble y Haiku sobre
el suyo. Los toques pasan por `process_tap`, como en Telegram.
"""

import asyncio
import json
import logging
from datetime import timedelta
from functools import partial

import pytest
from execution_data import PROJECT, ExecutionWorld, todoist_task
from heartbeat_data import FakeMessages, api_down, api_message, intentions, madrid
from memory_data import DELETIONS_LINE, QUIET_LINE, write_personal
from todoist_data import http_status_error

from jimic.channel import store
from jimic.channel.bot import Reply
from jimic.channel.message import Tap, render
from jimic.channel.taps import process_tap
from jimic.clock import to_db
from jimic.execution.commands import (
    NO_SUMMARY,
    NOTHING_TO_UNDO,
    UNDO_LIST_KIND,
    VIEW_KIND,
    Commands,
    command_of,
)
from jimic.execution.summary import summarize
from jimic.memory import changes
from jimic.memory.changes import Proposed
from jimic.memory.database import open_database
from jimic.memory.live import LiveConfig
from jimic.memory.personal import template
from jimic.tools.todoist import guard

SECRET = "contenido-que-no-va-al-log"
SUMMARY = {
    "routine": "Se levanta a las 7 y trabaja de 9 a 17.",
    "goals": "Entrenar tres veces por semana.",
    "preferences": "Mensajes cortos.",
    "intentions": "Plan del día y cierre.",
}


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


class World(ExecutionWorld):
    def __init__(self, conn, tmp_path, tasks=None, haiku=None, todoist=None):
        super().__init__(conn, tasks)
        self.config_dir = write_personal(tmp_path / "config")
        self.live = LiveConfig(intentions(), self.limits, self.config_dir)
        self.haiku = haiku if haiku is not None else FakeMessages(api_message(SUMMARY))
        self.commands = Commands(
            conn=conn,
            clock=self.clock,
            timezone=madrid(NOW).tzinfo,  # type: ignore[arg-type]  # the zone of `madrid` is a ZoneInfo
            config_dir=self.config_dir,
            config=self.live,
            todoist=todoist if todoist is not None else self.todoist,
            summarize=partial(summarize, self.haiku.create, "claude-haiku-5-5"),
            summary_model="claude-haiku-5-5",
        )

    def command(self, text: str) -> str | Reply:
        command = command_of(text)
        assert command is not None
        return asyncio.run(self.commands.reply(command))

    def send(self, reply: str | Reply) -> list[int]:
        assert isinstance(reply, Reply)
        ids = []
        for outgoing in (reply.outgoing, *reply.more):
            assert outgoing is not None
            ids.append(
                store.create(
                    self.conn, self.clock, outgoing.kind, outgoing.message, outgoing.expires_at
                )
            )
        return ids

    def tap(self, approval_id: int, item: int | None, option: str = "undo"):
        return asyncio.run(
            process_tap(
                self.conn, self.clock, self.commands.handlers, Tap(approval_id, item, option)
            )
        )

    def save(self, proposed: Proposed) -> int:
        return changes.save(self.conn, self.clock, self.config_dir, proposed, "origen", 1)

    def photo(self, task_id: str, title: str, deleted_at, **extra) -> int:
        cursor = self.conn.execute(
            "INSERT INTO deleted_tasks (deleted_at, todoist_task_id, content, description, due,"
            " duration, project_id, labels, priority, reason)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'duplicada')",
            (
                to_db(deleted_at),
                task_id,
                title,
                extra.get("description", "Notas"),
                extra.get("due", "2030-01-06"),
                extra.get("duration", "30m"),
                extra.get("project_id", PROJECT),
                json.dumps(extra.get("labels", ["casa"])),
                extra.get("priority", 3),
            ),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid


NOW = "2030-01-07T10:15"
QUIET_8 = Proposed("limites.yaml", "replace", QUIET_LINE, QUIET_LINE.replace('"07:00"', '"08:00"'))


@pytest.mark.parametrize(
    ("text", "command"),
    [
        ("/memoria", "/memoria"),
        ("  /deshacer ", "/deshacer"),
        ("/memoria@jimic_bot", "/memoria"),
        ("/memoria ya", None),
        ("memoria", None),
        ("/otra", None),
    ],
)
def test_un_comando_es_el_mensaje_entero(text, command):
    assert command_of(text) == command


# --- /memoria -----------------------------------------------------------------------------------


def test_memoria_resume_con_haiku_y_despliega_cada_seccion(conn, tmp_path):
    w = World(conn, tmp_path)
    reply = w.command("/memoria")
    assert isinstance(reply, Reply) and reply.outgoing is not None
    assert reply.outgoing.kind == VIEW_KIND
    titles = [item.title for item in reply.outgoing.message.items]
    assert titles[:4] == [
        f"📖 Rutina: {SUMMARY['routine']}",
        f"📖 Metas: {SUMMARY['goals']}",
        f"📖 Preferencias: {SUMMARY['preferences']}",
        f"📖 Intenciones: {SUMMARY['intentions']}",
    ]
    assert titles[4].startswith("📖 Límites: 8 mensajes al día, silencio de 23:00 a 07:00")
    assert titles[5] == "📖 Últimos cambios: ninguno"
    (request,) = w.haiku.requests
    content = request["messages"][0]["content"]
    assert "## Horario base" in content and "intentions:" in content
    assert "max_messages_per_day" not in content  # los límites los resume el código
    rows = conn.execute("SELECT model, input_tokens FROM model_usage").fetchall()
    assert len(rows) == 1 and rows[0][0] == "claude-haiku-5-5"


def test_memoria_sin_haiku_ensena_las_secciones_sin_resumen(conn, tmp_path):
    w = World(conn, tmp_path, haiku=FakeMessages(api_down()))
    reply = w.command("/memoria")
    assert isinstance(reply, Reply) and reply.outgoing is not None
    message = reply.outgoing.message
    assert message.body == NO_SUMMARY
    assert message.items[0].title == "📖 Rutina"
    assert conn.execute("SELECT COUNT(*) FROM model_usage").fetchone() == (0,)


def test_memoria_con_una_respuesta_que_no_vale_cuenta_el_gasto(conn, tmp_path):
    w = World(conn, tmp_path, haiku=FakeMessages(api_message("no es json")))
    reply = w.command("/memoria")
    assert isinstance(reply, Reply) and reply.outgoing is not None
    assert reply.outgoing.message.body == NO_SUMMARY
    assert conn.execute("SELECT COUNT(*) FROM model_usage").fetchone() == (1,)


def test_ver_envia_el_texto_del_fichero_y_el_boton_sigue(conn, tmp_path):
    w = World(conn, tmp_path)
    (approval,) = w.send(w.command("/memoria"))
    result = w.tap(approval, 5, "view")
    assert result.code == "shown"
    (text,) = result.send
    assert text == "limites.yaml:\n\n" + template("limites.yaml")
    stored = store.load(conn, approval)
    assert stored is not None and stored.message.items[4].pending
    assert w.tap(approval, 5, "view").send == (text,)


def test_ver_los_ultimos_cambios(conn, tmp_path):
    w = World(conn, tmp_path)
    w.save(QUIET_8)
    (approval,) = w.send(w.command("/memoria"))
    (text,) = w.tap(approval, 6, "view").send
    assert "limites.yaml · cambiar" in text and '"08:00"' in text


def test_la_zona_protegida_se_ve_pero_no_se_cambia_desde_memoria(conn, tmp_path):
    w = World(conn, tmp_path)
    reply = w.command("/memoria")
    assert isinstance(reply, Reply) and reply.outgoing is not None
    for item in reply.outgoing.message.items:
        assert [option.key for option in item.options] == ["view"]


# --- /deshacer de cambios -----------------------------------------------------------------------


def test_deshacer_sin_nada(conn, tmp_path):
    assert World(conn, tmp_path).command("/deshacer") == NOTHING_TO_UNDO


def test_deshacer_restaura_registra_y_recarga_al_momento(conn, tmp_path):
    """Ajuste 4: tras deshacer en `limites.yaml`, la configuración viva ya tiene el valor
    restaurado, sin esperar al latido."""
    w = World(conn, tmp_path)
    w.save(QUIET_8)
    w.live.reload()
    assert w.live.limits.quiet_hours.end.hour == 8
    reply = w.command("/deshacer")
    (approval,) = w.send(reply)
    result = w.tap(approval, 1)
    assert result.view is not None and "↩️ deshecho" in result.view.text
    assert (w.config_dir / "limites.yaml").read_text(encoding="utf-8") == template("limites.yaml")
    assert w.live.limits.quiet_hours.end.hour == 7
    assert conn.execute("SELECT operation FROM changes ORDER BY id").fetchall() == [
        ("replace",),
        ("undo",),
    ]


def test_deshacer_con_el_fichero_cambiado_no_toca_nada(conn, tmp_path):
    w = World(conn, tmp_path)
    w.save(QUIET_8)
    (approval,) = w.send(w.command("/deshacer"))
    path = w.config_dir / "limites.yaml"
    path.write_text(path.read_text(encoding="utf-8") + "# a mano\n", encoding="utf-8")
    result = w.tap(approval, 1)
    assert result.view is not None and "ha cambiado: edítalo a mano" in result.view.text
    assert path.read_text(encoding="utf-8").endswith("# a mano\n")


def test_deshacer_lista_los_ultimos_10_cambios_en_mensajes_de_8(conn, tmp_path):
    w = World(conn, tmp_path)
    for n in range(12):
        w.save(Proposed("ideas.md", "add", None, f"- idea {n}"))
    reply = w.command("/deshacer")
    assert isinstance(reply, Reply) and reply.outgoing is not None
    messages = [reply.outgoing.message, *(o.message for o in reply.more)]
    assert [len(m.items) for m in messages] == [8, 2]
    assert [m.header for m in messages] == ["Para deshacer (1/2)", "Para deshacer (2/2)"]
    assert "idea 11" in messages[0].items[0].title
    assert all(o.kind == UNDO_LIST_KIND for o in [reply.outgoing, *reply.more])


# --- /deshacer de borrados ----------------------------------------------------------------------


def test_recrear_un_borrado_da_un_id_nuevo_y_lo_avisa(conn, tmp_path):
    w = World(conn, tmp_path)
    row = w.photo("t9", "Comprar pan", madrid(NOW) - timedelta(days=1))
    (approval,) = w.send(w.command("/deshacer"))
    result = w.tap(approval, 1)
    assert result.send == (
        "He recreado «Comprar pan» con un ID nuevo; los comentarios no vuelven.",
    )
    assert result.view is not None and "↩️ recreada con un ID nuevo" in result.view.text
    (call,) = [args for name, args in w.api.calls if name == "add_task"]
    assert call["content"] == "Comprar pan" and call["description"] == "Notas"
    assert call["labels"] == ["casa"] and call["priority"] == 3
    assert call["project_id"] == PROJECT and call["duration"] == 30
    assert call["auto_parse_labels"] is False
    new_id = w.api.tasks[-1]["id"]
    assert new_id != "t9"
    assert conn.execute(
        "SELECT restored_task_id FROM deleted_tasks WHERE id = ?", (row,)
    ).fetchone() == (new_id,)
    assert w.command("/deshacer") == NOTHING_TO_UNDO


def test_dos_fotos_de_la_misma_tarea_dan_una_entrada(conn, tmp_path):
    """Ajuste 5: un borrado reintentado dejó dos fotos; se ofrece la más reciente."""
    w = World(conn, tmp_path)
    w.photo("t9", "Versión vieja", madrid(NOW) - timedelta(days=2))
    w.photo("t9", "Versión nueva", madrid(NOW) - timedelta(days=1))
    reply = w.command("/deshacer")
    assert isinstance(reply, Reply) and reply.outgoing is not None
    (item,) = reply.outgoing.message.items
    assert "Versión nueva" in item.title
    (approval,) = w.send(reply)
    w.tap(approval, 1)
    assert w.command("/deshacer") == NOTHING_TO_UNDO


def test_una_tarea_que_sigue_en_todoist_no_se_recrea_ni_se_vuelve_a_ofrecer(conn, tmp_path):
    w = World(conn, tmp_path, tasks=[todoist_task("t9", "Sigue aquí")])
    w.photo("t9", "Sigue aquí", madrid(NOW) - timedelta(days=1))
    (approval,) = w.send(w.command("/deshacer"))
    result = w.tap(approval, 1)
    assert result.view is not None and "sigue en Todoist: no la recreo" in result.view.text
    assert [name for name, _ in w.api.calls if name == "add_task"] == []
    assert w.command("/deshacer") == NOTHING_TO_UNDO


def test_dos_listas_no_recrean_dos_veces(conn, tmp_path):
    w = World(conn, tmp_path)
    w.photo("t9", "Comprar pan", madrid(NOW) - timedelta(days=1))
    (first,) = w.send(w.command("/deshacer"))
    (second,) = w.send(w.command("/deshacer"))
    w.tap(first, 1)
    result = w.tap(second, 1)
    assert result.view is not None and "ya estaba recreada" in result.view.text
    assert len([name for name, _ in w.api.calls if name == "add_task"]) == 1


def test_si_todoist_falla_se_puede_reintentar(conn, tmp_path):
    w = World(conn, tmp_path)
    w.photo("t9", "Comprar pan", madrid(NOW) - timedelta(days=1))
    (approval,) = w.send(w.command("/deshacer"))
    w.api.error = http_status_error(503)
    result = w.tap(approval, 1)
    assert result.code == "handler_failed"
    w.api.error = None
    assert w.tap(approval, 1).code == "resolved"


def test_en_dev_la_guardia_la_lleva_al_proyecto_de_pruebas(conn, tmp_path):
    base = World(conn, tmp_path)
    guarded = guard(base.todoist.reader, base.todoist.writer, "proyecto-de-pruebas", base.api.close)
    w = World(conn, tmp_path / "otra", todoist=guarded)
    w.api = base.api
    w.photo("t9", "Comprar pan", madrid(NOW) - timedelta(days=1), project_id="otro-proyecto")
    (approval,) = w.send(w.command("/deshacer"))
    w.tap(approval, 1)
    (call,) = [args for name, args in base.api.calls if name == "add_task"]
    assert call["project_id"] == "proyecto-de-pruebas"


def test_solo_los_borrados_de_los_ultimos_30_dias(conn, tmp_path):
    """El corte es absoluto: ahora menos 30 días justos entra; un segundo antes, no."""
    w = World(conn, tmp_path)
    cut = madrid(NOW) - timedelta(days=30)
    w.photo("dentro", "Entra", cut)
    w.photo("fuera", "No entra", cut - timedelta(seconds=1))
    reply = w.command("/deshacer")
    assert isinstance(reply, Reply) and reply.outgoing is not None
    assert [item.title.split(" ", 2)[2] for item in reply.outgoing.message.items] == ["Entra"]


def test_mas_de_24_borrados_se_cuentan(conn, tmp_path):
    w = World(conn, tmp_path)
    for n in range(26):
        w.photo(f"t{n}", f"Tarea {n}", madrid(NOW) - timedelta(minutes=n + 1))
    reply = w.command("/deshacer")
    assert isinstance(reply, Reply) and reply.outgoing is not None
    assert reply.text == "Y 2 borrados más antiguos, que no enseño."
    assert sum(len(o.message.items) for o in [reply.outgoing, *reply.more]) == 24


def test_un_comando_cuenta_como_mensaje_de_pedro(conn, tmp_path):
    from jimic.memory.conversations import last_message_at

    w = World(conn, tmp_path)
    w.command("/deshacer")
    assert last_message_at(conn) == w.clock.now()


def test_los_logs_no_llevan_contenido(conn, tmp_path, caplog):
    caplog.set_level(logging.DEBUG)
    w = World(conn, tmp_path)
    w.save(Proposed("ideas.md", "add", None, f"- {SECRET}"))
    w.photo("t9", SECRET, madrid(NOW) - timedelta(days=1))
    (approval,) = w.send(w.command("/deshacer"))
    w.tap(approval, 1)
    w.tap(approval, 2)
    (view,) = w.send(w.command("/memoria"))
    w.tap(view, 6, "view")
    rendered = render(store.load(conn, approval).message, approval)  # type: ignore[union-attr]  # it exists
    assert SECRET in rendered.text  # se enseña a Pedro…
    assert caplog.records and SECRET not in caplog.text  # …pero no va al log
    assert DELETIONS_LINE in template("limites.yaml")
