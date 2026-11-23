"""Contrato de la puesta en marcha con el agente real (Sonnet): lento y a mano (tarea 1.13).

Solo corre con `JIMIC_CONTRATO=1`, con la clave y los modelos del `.env` de `jimic-dev`. Los
ficheros son los que crean `jimic config init` y los esqueletos, en una carpeta temporal: nunca
los de Pedro. APEX es el inventado de la 1.6, con dos objetivos en curso. El escritor de Todoist es
un espía que no debe llamarse: no toca Todoist, Calendar ni Telegram, así que no deja restos.

1. Con respuestas inventadas para la rutina, el agente propone un cambio en `rutina.md` que valida;
   con su [Guardar], la rutina queda completa.
2. En el entrenamiento, no propone copiar a `objetivos.md` los objetivos de APEX.

Imprime (`-s`) códigos, recuentos, ficheros, dólares y tokens; nunca las respuestas.
"""

import asyncio
import logging

import pytest
from agent_data import SpyWriter
from conftest import CONTRACT
from heartbeat_data import calendar_with, event, intentions, limits, madrid, sources, task

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
from jimic.memory import changes
from jimic.memory.changes import Proposed
from jimic.memory.database import open_database
from jimic.memory.live import LiveConfig
from jimic.memory.onboarding import progress
from jimic.memory.personal import init
from jimic.paths import Paths
from jimic.tools.todoist import TaskList, TodoistTools

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CONTRACT, reason="contrato: solo con JIMIC_CONTRATO=1"),
]

APEX_GOALS = ("subir la fuerza", "moverse más")
"""Las descripciones de los objetivos en curso del APEX inventado, en minúsculas."""


class Rig:
    def __init__(self, settings, tmp_path):
        self.conn = open_database(tmp_path / "contrato.db")
        self.paths = Paths("dev", tmp_path / "config", tmp_path / "data")
        init(self.paths.config_dir)
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
            config_dir=self.paths.config_dir,
        )
        self.memory = MemoryHandlers(
            conn=self.conn, clock=self.clock, config_dir=self.paths.config_dir, config=self.live
        )

    def say(self, text: str) -> str | Reply:
        return asyncio.run(self.assistant.reply(Incoming(text)))

    def offered(self, reply: str | Reply) -> list[dict]:
        if not isinstance(reply, Reply):
            return []
        return [
            item.data
            for o in (reply.outgoing, *reply.more)
            if o is not None and o.kind == MEMORY_KIND
            for item in o.message.items
            if item.data
        ]

    def runs(self) -> list[tuple]:
        return self.conn.execute(
            "SELECT input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, usd"
            " FROM model_usage ORDER BY id"
        ).fetchall()


def shapes(entries: list[dict]) -> list[tuple]:
    return [(e["action"], e.get("file"), e.get("operation")) for e in entries]


def test_con_respuestas_inventadas_la_rutina_se_propone_y_valida(contract_env, tmp_path, caplog):
    _, settings = contract_env
    rig = Rig(settings, tmp_path)
    try:
        start = rig.say("/puesta_en_marcha")
        print(f"\ninicio: propuestas={shapes(rig.offered(start))}")
        with caplog.at_level(logging.INFO, logger="jimic.agent.tools"):
            reply = rig.say(
                "Me levanto a las 7:00 y me acuesto a las 23:30. Desayuno a las 7:30, como a las"
                " 14:00 y ceno a las 21:00. Trabajo de 9:00 a 17:00 de lunes a viernes. Los"
                " fines de semana me levanto a las 9:00 y no trabajo."
            )
        entries = rig.offered(reply)
        codes = [r.getMessage() for r in caplog.records if "memory" in r.getMessage()]
        print(f"rutina: herramientas={codes} propuestas={shapes(entries)} ejecuciones={rig.runs()}")
        routine = [e for e in entries if e.get("file") == "rutina.md"]
        assert routine, "no propone nada en rutina.md"
        for entry in routine:
            # Lo ofrecido ya pasó la validación; se repite contra el fichero de ahora.
            changes.check(
                rig.paths.config_dir,
                Proposed("rutina.md", entry["operation"], entry.get("before"), entry.get("after")),
            )
        # [Guardar] en cada una, como Pedro: la rutina queda completa.
        assert isinstance(reply, Reply)
        offer = next(o for o in (reply.outgoing, *reply.more) if o and o.kind == MEMORY_KIND)
        approval = store.create(rig.conn, rig.clock, MEMORY_KIND, offer.message, offer.expires_at)
        handlers = {MEMORY_KIND: rig.memory.memory}
        for number in range(1, len(offer.message.items) + 1):
            asyncio.run(process_tap(rig.conn, rig.clock, handlers, Tap(approval, number, "save")))
        missing = progress(rig.paths.config_dir).routine_missing
        print(f"guardado: secciones_sin_contenido={list(missing)}")
        assert missing == ()
        assert rig.spy.calls == []
    finally:
        rig.conn.close()


def test_en_el_entrenamiento_no_propone_copiar_los_objetivos_de_apex(
    contract_env, tmp_path, caplog
):
    _, settings = contract_env
    rig = Rig(settings, tmp_path)
    try:
        rig.say("/puesta_en_marcha")
        with caplog.at_level(logging.INFO, logger="jimic.agent.tools"):
            first = rig.say(
                "Vamos directamente con el área de entrenamiento: ¿qué objetivos tengo en APEX?"
            )
            second = rig.say(
                "Mi meta en entrenamiento es ganar fuerza este año, con prioridad alta. Guárdalo."
            )
        entries = rig.offered(first) + rig.offered(second)
        codes = [r.getMessage() for r in caplog.records if "tool" in r.getMessage()]
        print(
            f"\nentrenamiento: herramientas={codes} propuestas={shapes(entries)}"
            f" ejecuciones={rig.runs()}"
        )
        assert "tool read_apex_today: ok" in codes
        assert not any("apex_copy" in code for code in codes)
        for entry in entries:
            after = str(entry.get("after") or "").casefold()
            assert not any(goal in after for goal in APEX_GOALS)
        assert rig.spy.calls == []
    finally:
        rig.conn.close()
