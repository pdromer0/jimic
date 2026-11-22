"""What the service runs between opening and closing the database (task 1.8).

`Runtime.start` opens the sources, the client of the model, the conversation with the agent, the
executor of what Pedro approves and the Telegram channel (Pedro's text goes to the assistant,
the taps to the executor), then sweeps what expired while the service was down and schedules the
heartbeat every 15 minutes (from the next quarter of an hour), the sweep of expired approvals
every day at 00:01 (1.11-c) and the purge once a day: the database and the files of the agent
older than 7 days. `Runtime.stop` undoes it in reverse order, every step even
if one fails: the channel stops first, so no heartbeat runs while the rest closes. The queue of
the group window lives in the `Heartbeat`, as long as the service.
"""

import asyncio
import logging
import sqlite3
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from datetime import time

from jimic.agent.conversation import Conversation
from jimic.agent.transcripts import purge_agent_files
from jimic.channel.bot import TelegramChannel, TextHandler, build_channel
from jimic.channel.taps import TapHandler
from jimic.clock import Clock
from jimic.config import Settings
from jimic.execution.assistant import Assistant
from jimic.execution.execute import Executor
from jimic.heartbeat.beat import INTERVAL, Heartbeat, next_beat
from jimic.heartbeat.decide import HaikuDecider, ModelApi, build_model_api
from jimic.heartbeat.sources import open_sources
from jimic.memory.live import LiveConfig
from jimic.memory.retention import purge
from jimic.memory.schemas import Intentions, Limits
from jimic.paths import Paths
from jimic.tools.todoist import TodoistApi

logger = logging.getLogger(__name__)

PURGE_AT = time(4, 0)
"""Local time of the daily purge, inside the default quiet hours."""
SWEEP_AT = time(0, 1)
"""Local time of the sweep of expired approvals: they expire at midnight (RF-07), and a minute
later leaves room for a job that fires a little early. A tap in between already sees them
expired."""


type ChannelFactory = Callable[
    [Settings, sqlite3.Connection, Clock, TextHandler, Mapping[str, TapHandler]], TelegramChannel
]
type ModelFactory = Callable[[Settings], ModelApi]


def default_channel(
    settings: Settings,
    conn: sqlite3.Connection,
    clock: Clock,
    on_text: TextHandler,
    handlers: Mapping[str, TapHandler],
) -> TelegramChannel:
    return build_channel(settings, conn, clock, handlers=handlers, on_text=on_text)


class Runtime:
    def __init__(
        self,
        settings: Settings,
        paths: Paths,
        clock: Clock,
        conn: sqlite3.Connection,
        intentions: Intentions,
        limits: Limits,
        *,
        channel_factory: ChannelFactory = default_channel,
        model_factory: ModelFactory = build_model_api,
        todoist_api: TodoistApi | None = None,
    ) -> None:
        self._settings = settings
        self._paths = paths
        self._clock = clock
        self._conn = conn
        self._intentions = intentions
        self._limits = limits
        self.config = LiveConfig.fixed(intentions, limits)
        self._channel_factory = channel_factory
        self._model_factory = model_factory
        self._todoist_api = todoist_api
        self._stack = AsyncExitStack()
        self.heartbeat: Heartbeat | None = None
        self.conversation: Conversation | None = None
        self.assistant: Assistant | None = None
        self.executor: Executor | None = None

    async def start(self) -> None:
        """Raises what the channel raises (`ChannelError`), with everything opened closed."""
        stack = self._stack
        try:
            sources = await stack.enter_async_context(
                open_sources(self._settings, self._paths, self._clock, self._todoist_api)
            )
            model = self._model_factory(self._settings)
            stack.push_async_callback(model.close)
            conversation = Conversation(
                settings=self._settings,
                paths=self._paths,
                limits=self._limits,
                clock=self._clock,
                conn=self._conn,
                sources=sources,
                config=self.config,
            )

            executor = Executor(
                conn=self._conn,
                clock=self._clock,
                timezone=self._settings.timezone,
                limits=self._limits,
                todoist=sources.todoist,
                config=self.config,
            )
            assistant = Assistant(
                conversation=conversation,
                executor=executor,
                conn=self._conn,
                clock=self._clock,
                timezone=self._settings.timezone,
            )
            channel = self._channel_factory(
                self._settings, self._conn, self._clock, assistant.reply, executor.handlers
            )
            heartbeat = Heartbeat(
                clock=self._clock,
                timezone=self._settings.timezone,
                limits=self._limits,
                intentions=self._intentions,
                sources=sources,
                conn=self._conn,
                decider=HaikuDecider(
                    model.create, self._settings.heartbeat_model, self._intentions, self.config
                ),
                sender=channel,
                config=self.config,
            )
            await channel.start()
            stack.push_async_callback(channel.stop)
            # What expired while the service was down loses its buttons now (1.11-c).
            await channel.sweep()
            channel.schedule_every(heartbeat.beat, INTERVAL, next_beat(self._clock.now()), "latido")
            channel.schedule_daily(
                channel.sweep, SWEEP_AT.replace(tzinfo=self._settings.timezone), "caducadas"
            )
            channel.schedule_daily(
                self._purge, PURGE_AT.replace(tzinfo=self._settings.timezone), "purga"
            )
            self.heartbeat = heartbeat
            self.conversation = conversation
            self.assistant = assistant
            self.executor = executor
        except BaseException:
            await stack.aclose()
            raise

    async def _purge(self) -> None:
        """Each purge on its own: one that fails does not keep the other from running."""
        try:
            purge(self._conn, self._clock)
        except Exception as error:
            logger.error("purge failed (%s)", type(error).__name__)
        try:
            # Walking the folder of the agent touches the disk: in a thread, off the loop.
            await asyncio.to_thread(purge_agent_files, self._paths.agent_dir, self._clock)
        except Exception as error:
            logger.error("purge of agent files failed (%s)", type(error).__name__)

    async def stop(self) -> None:
        await self._stack.aclose()
