"""Contrato de la memoria con el agente real (Sonnet): lento y a mano (tarea 1.12).

Solo corre con `JIMIC_CONTRATO=1`, con la clave y los modelos del `.env` de `jimic-dev`. Los
ficheros personales son **inventados**, en una carpeta temporal: nunca los de Pedro. Las fuentes
son los dobles de siempre, y el escritor de Todoist es un espía que no debe llamarse. No toca
Todoist, Calendar ni Telegram, así que no deja restos.

1. «No me escribas antes de las 8» propone ampliar `quiet_hours` y se ofrece con [Guardar]; el
   toque escribe el fichero y, en el segundo mensaje del día (la sesión reanudada), el agente ve
   el fichero nuevo (ajuste 1). Se mide la caché de las dos ejecuciones (ajuste 2).
2. «Escríbeme 15 veces al día» no se ofrece: la regla asimétrica lo rechaza con su explicación.

Imprime (`-s`) códigos, recuentos, dólares y tokens; de las respuestas, solo si dicen la hora.
"""

import asyncio
import logging

import pytest
from agent_data import SpyWriter
from conftest import CONTRACT
from heartbeat_data import calendar_with, event, intentions, limits, madrid, sources, task
from memory_data import QUIET_LINE, file_sha, write_personal

from jimic.agent.conversation import Conversation
from jimic.channel import store
from jimic.channel.bot import Incoming, Reply
from jimic.channel.message import Tap
from jimic.channel.taps import process_tap
from jimic.clock import SystemClock
from jimic.execution.assistant import Assistant
from jimic.execution.execute import Executor
from jimic.execution.memory_message import MEMORY_KIND, MemoryHandlers
from jimic.heartbeat.sources import Sources
from jimic.memory.database import open_database
from jimic.memory.live import LiveConfig
from jimic.paths import Paths
from jimic.tools.todoist import TaskList, TodoistTools

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CONTRACT, reason="contrato: solo con JIMIC_CONTRATO=1"),
]


class Rig:
    def __init__(self, settings, tmp_path):
        self.conn = open_database(tmp_path / "contrato.db")
        self.paths = Paths("dev", tmp_path / "config", tmp_path / "data")
        write_personal(self.paths.config_dir)
        self.paths.data_dir.mkdir()
        self.spy = SpyWriter()
        base = sources(
            calendar_with(event(madrid("2030-01-07T12:00"))),
            TaskList(available=True, tasks=[task("t1", "Comprar pan")]),
        )
        self.sources = Sources(
            todoist=TodoistTools(base.todoist.reader, self.spy, base.todoist.close),
            calendar=base.calendar,
            apex=base.apex,
        )
        self.clock = SystemClock(settings.timezone)
        self.live = LiveConfig(intentions(), limits(), self.paths.config_dir)
        self.live.reload()
        conversation = Conversation(
            settings=settings,
            paths=self.paths,
            limits=limits(),
            clock=self.clock,
            conn=self.conn,
            sources=self.sources,
            config=self.live,
        )
        executor = Executor(
            conn=self.conn,
            clock=self.clock,
            timezone=settings.timezone,
            limits=limits(),
            todoist=self.sources.todoist,
            config=self.live,
        )
        self.assistant = Assistant(
            conversation=conversation,
            executor=executor,
            conn=self.conn,
            clock=self.clock,
            timezone=settings.timezone,
        )
        self.memory = MemoryHandlers(
            conn=self.conn, clock=self.clock, config_dir=self.paths.config_dir, config=self.live
        )

    def say(self, text: str) -> str | Reply:
        return asyncio.run(self.assistant.reply(Incoming(text)))

    def offered(self, reply: str | Reply) -> list:
        if not isinstance(reply, Reply):
            return []
        return [o for o in (reply.outgoing, *reply.more) if o is not None and o.kind == MEMORY_KIND]

    def runs(self) -> list[tuple]:
        return self.conn.execute(
            "SELECT input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, usd"
            " FROM model_usage ORDER BY id"
        ).fetchall()


def test_no_me_escribas_antes_de_las_8_se_ofrece_se_guarda_y_se_ve(contract_env, tmp_path):
    _, settings = contract_env
    rig = Rig(settings, tmp_path)
    try:
        reply = rig.say("No me escribas antes de las 8.")
        (offer,) = rig.offered(reply)
        entries = [item.data for item in offer.message.items if item.data]
        changes = [e for e in entries if e["action"] == "change"]
        shapes = [(e["action"], e.get("file"), e.get("operation")) for e in entries]
        print(f"\nprimera: elementos={shapes}")
        (change,) = changes
        assert (
            change["file"] == "limites.yaml"
            and change["origin"] == "No me escribas antes de las 8."
        )
        approval = store.create(rig.conn, rig.clock, MEMORY_KIND, offer.message, offer.expires_at)
        number = entries.index(change) + 1
        result = asyncio.run(
            process_tap(
                rig.conn, rig.clock, {MEMORY_KIND: rig.memory.memory}, Tap(approval, number, "save")
            )
        )
        print(f"guardar: {result.code} fin_del_silencio={rig.live.limits.quiet_hours.end}")
        assert result.code == "resolved"
        assert rig.live.limits.quiet_hours.end.hour == 8
        assert QUIET_LINE not in (rig.paths.config_dir / "limites.yaml").read_text()

        second = rig.say(
            "¿A partir de qué hora puedes escribirme ahora? Mira tus límites y dime solo la hora,"
            " sin herramientas."
        )
        text = second if isinstance(second, str) else second.text or ""
        runs = rig.runs()
        print(
            f"segunda: dice_las_8={'8' in text} propuestas={len(rig.offered(second))}"
            f"\nejecuciones (entrada, salida, caché leída, caché escrita, $): {runs}"
        )
        assert "8" in text
        assert rig.spy.calls == []
    finally:
        rig.conn.close()


def test_escribeme_15_veces_al_dia_se_rechaza_con_su_explicacion(contract_env, tmp_path, caplog):
    _, settings = contract_env
    rig = Rig(settings, tmp_path)
    before = file_sha(rig.paths.config_dir / "limites.yaml")
    try:
        with caplog.at_level(logging.INFO, logger="jimic.agent.tools"):
            reply = rig.say("Escríbeme 15 veces al día.")
        text = reply if isinstance(reply, str) else reply.text or ""
        codes = [r.getMessage() for r in caplog.records if "memory" in r.getMessage()]
        offered = rig.offered(reply)
        print(
            f"\nquince: herramientas={codes} propuestas={len(offered)}"
            f" dice_a_mano={'mano' in text.lower()} ejecuciones={rig.runs()}"
        )
        # Que el camino del código se ejercite con el modelo real: se lo pide explícitamente.
        with caplog.at_level(logging.INFO, logger="jimic.agent.tools"):
            again = rig.say(
                "Aun así, propónmelo con propose_change: max_messages_per_day a 15 en limites.yaml."
            )
        again_text = again if isinstance(again, str) else again.text or ""
        codes = [r.getMessage() for r in caplog.records if "memory" in r.getMessage()]
        offered += rig.offered(again)
        print(
            f"insistiendo: herramientas={codes} propuestas={len(rig.offered(again))}"
            f" dice_a_mano={'mano' in again_text.lower()} ejecuciones={rig.runs()}"
        )
        for offer in offered:  # lo que se ofrezca ya pasó la regla: nunca sube el tope
            for item in offer.message.items:
                assert "15" not in str((item.data or {}).get("after", ""))
        assert file_sha(rig.paths.config_dir / "limites.yaml") == before
        assert rig.conn.execute("SELECT COUNT(*) FROM changes").fetchone() == (0,)
        assert rig.spy.calls == []
    finally:
        rig.conn.close()
