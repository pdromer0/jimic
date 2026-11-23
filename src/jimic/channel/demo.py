"""`jimic telegram demo`: an invented proposal in the test bot, to see the buttons on the phone.

Only with `ENTORNO=dev`: the demo refuses before it reads the `.env` or builds anything. It
sends six messages, as the assistant would: the plan of the day as the code paints it, with
`[n ✅] [n ✏️] [n ⏭]` and `[✅ Créalo todo] [⏭ Hoy sin plan]` (task 1.14); a proposal with a
create, a move and a delete under an answer, with the global row `[✅ Créalo todo] [⏭ Nada]`;
what was done at once with its [Deshacer] (task 1.11); a step of the onboarding, the meals of the
routine with [Guardar] (task 1.13); a change of memory with [Guardar] [Cambiar] [No] and an idea
with [📝 Apuntar]; a list of `/deshacer` (task 1.12); and two notices of key tasks (task 1.15),
one alone and two in one message with a notice, with their buttons and their answers. The taps
edit the messages and touch nothing in Todoist or in the files: the handlers only say what would
have happened. A text from
Pedro gets the fixed phrase. It is a process that does not end (stop it with Ctrl+C): **Pedro
launches it, never the agent**.
"""

import asyncio
import logging
import sqlite3
from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from typing import Any

from jimic.channel.bot import TelegramChannel, build_channel
from jimic.channel.message import InteractiveMessage, Item
from jimic.channel.taps import Answer, Followup, HandlerOutcome, Show, TapHandler
from jimic.clock import Clock, SystemClock
from jimic.config import Settings, load_settings
from jimic.execution.commands import UNDO, UNDO_HEADER, UNDO_LIST_KIND
from jimic.execution.key_tasks import (
    DONE,
    DONE_TEXT,
    GO_TEXT,
    GOING,
    LATER,
    LATER_TEXT,
    SKIP_TEXT,
    SKIPPED,
    key_undo_message,
    still_open,
)
from jimic.execution.memory_message import MEMORY_KIND, memory_message
from jimic.execution.plan_message import PLAN_KIND, UNDO_KIND, plan_message, undo_message
from jimic.heartbeat.key_message import DONE as DONE_OPTION
from jimic.heartbeat.key_message import GO, KEY_KIND, SKIP, TRAINED
from jimic.heartbeat.key_message import LATER as LATER_OPTION
from jimic.paths import Paths, resolve_paths
from jimic.plan.day import day_view
from jimic.plan.message import PlanContent, paint
from jimic.service import ChannelFactory, serve_until_signal
from jimic.tools.calendar import CalendarDay, CalendarEvent
from jimic.tools.todoist import TaskList, TodoistTask

logger = logging.getLogger(__name__)

TTL = timedelta(hours=1)
"""How long the demo waits for its buttons: it only has to outlast a screenshot."""
DAY = date(2030, 1, 7)
"""The invented day of the demo: «hoy» in what it shows."""
ANSWER = "Te propongo meter el gimnasio, mover el webinar y quitar un duplicado (inventado)."
RESULTS = {
    ("create", "ok"): "✓ creada",
    ("create", "edit"): "✏️ ¿cómo la quieres?",
    ("create", "no"): "⏭ descartada",
    ("move", "ok"): "✓ movida",
    ("move", "no"): "🆗 se queda",
    ("delete", "del"): "✓ borrada",
    ("delete", "keep"): "🆗 se queda",
}


class DemoError(Exception):
    """The demo cannot run here; the message says why."""


def _snapshot(title: str, due: str) -> dict[str, Any]:
    return {"title": title, "due": due, "project_id": "demo", "is_recurring": False}


ENTRIES: list[dict[str, Any]] = [
    {
        "action": "create",
        "title": "🏋️ Gimnasio",
        "due": "2030-01-07T12:00",
        "duration_minutes": 75,
        "reason": "inventado",
    },
    {
        "action": "move",
        "task_id": "demo-1",
        "due": "2030-01-07T09:50",
        "reason": "inventado",
        "snapshot": _snapshot("Webinar", "2030-01-07T10:00:00+01:00"),
    },
    {
        "action": "delete",
        "task_id": "demo-2",
        "reason": "inventado",
        "snapshot": _snapshot("Andar 30 min", "2030-01-06"),
    },
]


PLAN_ENTRIES: list[dict[str, Any]] = [
    {
        "action": "create",
        "title": "Gimnasio",
        "due": "2030-01-07T12:00",
        "duration_minutes": 75,
        "icon": "🏋️",
        "reason": "toca empuje",
    },
    {
        "action": "create",
        "title": "Estiramientos",
        "due": "2030-01-07T21:30",
        "duration_minutes": 15,
        "icon": "🧘",
        "reason": "hábito de la noche",
    },
    {
        "action": "move",
        "task_id": "demo-1",
        "due": "2030-01-07T09:50",
        "icon": "⚠️",
        "reason": "manda el calendario",
        "snapshot": _snapshot("Webinar", "2030-01-07T10:00:00+01:00"),
    },
    {
        "action": "delete",
        "task_id": "demo-2",
        "reason": "duplica «Paseo 30 min» de hoy",
        "snapshot": _snapshot("Andar 30 min", "2030-01-06"),
    },
]
PLAN_TABLE = {"comida": "🍽️", "trabajo en el proyecto": "🤖"}


def demo_plan_message(settings: Settings) -> InteractiveMessage:
    """The plan of the day of an invented Monday at 7:45, painted by the code (task 1.14)."""
    now = datetime.combine(DAY, time(7, 45), tzinfo=settings.timezone)
    calendar = CalendarDay(
        available=True,
        events=[
            CalendarEvent(
                title="Trabajo en el proyecto",
                start=now.replace(hour=15, minute=30),
                end=now.replace(hour=19, minute=0),
                all_day=False,
                organizer_is_me=True,
                declined=False,
            )
        ],
    )
    tasks = TaskList(
        available=True,
        tasks=[
            TodoistTask(
                id="demo-3",
                title="Comida",
                project_id="demo",
                due=now.replace(hour=14, minute=0),
                is_recurring=False,
                has_subtasks=False,
                duration_minutes=60,
            )
        ],
    )
    content = PlanContent(
        greeting="Lunes de empuje, ¿listo? (inventado)",
        entries=PLAN_ENTRIES,
        learned="🧠 Desde ayer he aprendido: preferencias.md · cambiado (inventado)",
        training="Empuje · fuerza: 75 minutos a mediodía (inventado)",
    )
    plan = paint(content, day_view(calendar, tasks, now, late=False), False, now, PLAN_TABLE)
    assert isinstance(plan, InteractiveMessage)
    return plan


QUIET = 'quiet_hours: {from: "23:00", to: "07:00"}'
MEMORY_ENTRIES: list[dict[str, Any]] = [
    {
        "action": "change",
        "file": "limites.yaml",
        "operation": "replace",
        "before": QUIET,
        "after": QUIET.replace('"07:00"', '"08:00"'),
        "reason": "inventado",
        "origin": "No me escribas antes de las 8 (inventado)",
    },
    {
        "action": "idea",
        "text": "Una app para apuntar lo que leo (inventado)",
        "line": "- 2030-01-07 · Una app para apuntar lo que leo (inventado)",
        "origin": "inventado",
    },
]
MEMORY_ANSWER = "Te propongo no escribirte antes de las 8 y apuntar tu idea (inventado)."
ONBOARDING_ENTRY: dict[str, Any] = {
    "action": "change",
    "file": "rutina.md",
    "operation": "replace",
    "before": "## Comidas",
    "after": "## Comidas\n\n- Desayuno a las 7:30, como a las 14:00 y ceno a las 21:00",
    "reason": "inventado",
    "origin": "Desayuno a las 7:30, como a las 14:00 y ceno a las 21:00 (inventado)",
}
ONBOARDING_ANSWER = (
    "Las comidas, así en tu rutina. Después vamos con la jornada de trabajo (inventado)."
)
UNDO_ITEMS = (
    Item(
        title="📝 06/01 preferencias.md · añadir: - Mensajes cortos (inventado)",
        options=(UNDO,),
        data={"change": 1},
    ),
    Item(title="🗑️ 05/01 Andar 30 min (inventado)", options=(UNDO,), data={"deleted": 1}),
)


GYM_ITEM = Item(
    title="🏋️ Gimnasio",
    options=(GO, LATER_OPTION, TRAINED, SKIP),
    icon="🏋️",
    data={"intent": "insistir_gimnasio", "habit": "Gimnasio"},
)
OFFERS_ITEM = Item(
    title="🛒 Revisar ofertas",
    options=(GO, LATER_OPTION, DONE_OPTION, SKIP),
    icon="🛒",
    data={"intent": "insistir_ofertas", "habit": "Revisar ofertas"},
)
KEY_ONE = InteractiveMessage(
    body="Oye, que la pesa te echa de menos 😄 ¿Te da tiempo antes de comer? (inventado)",
    items=(GYM_ITEM,),
    style="key",
)
KEY_TWO = InteractiveMessage(
    body=(
        "Segundo toque: el gimnasio sigue esperando y las ofertas también 💪 (inventado)"
        "\n\nY de paso, la basura a la calle, que hoy toca."
    ),
    items=(GYM_ITEM, OFFERS_ITEM),
    style="key",
)


def demo_messages(settings: Settings) -> list[tuple[str, InteractiveMessage]]:
    """The invented plan of the day (task 1.14), the invented proposal, the invented [Deshacer],
    a step of the onboarding with [Guardar] (task 1.13), a change of memory with [Guardar] and a
    list of `/deshacer` (task 1.12): nothing of them is real."""
    proposal = plan_message(ENTRIES, DAY, settings.timezone).model_copy(update={"body": ANSWER})
    undo = undo_message("➕ 🧘 Estiramientos · hoy 21:30", {"action": "create", "task_id": "x"})
    undo = undo.model_copy(update={"body": "Apuntado (inventado)."})
    onboarding = memory_message([ONBOARDING_ENTRY]).model_copy(update={"body": ONBOARDING_ANSWER})
    memory = memory_message(MEMORY_ENTRIES).model_copy(update={"body": MEMORY_ANSWER})
    undo_list = InteractiveMessage(header=UNDO_HEADER, items=UNDO_ITEMS)
    return [
        (PLAN_KIND, demo_plan_message(settings)),
        (PLAN_KIND, proposal),
        (UNDO_KIND, undo),
        (MEMORY_KIND, onboarding),
        (MEMORY_KIND, memory),
        (UNDO_LIST_KIND, undo_list),
        (KEY_KIND, KEY_ONE),
        (KEY_KIND, KEY_TWO),
    ]


async def demo_plan(item: Item, option: str, approval_id: int) -> HandlerOutcome:
    """Only marks the result: the demo touches nothing."""
    action = str((item.data or {}).get("action"))
    return RESULTS.get((action, option), "hecho")


async def demo_undo(item: Item, option: str, approval_id: int) -> HandlerOutcome:
    return "↩️ deshecho"


MEMORY_RESULTS = {"save": "💾 guardado", "edit": "✏️ dime cómo lo quieres", "note": "📝 apuntada"}


async def demo_memory(item: Item, option: str, approval_id: int) -> HandlerOutcome:
    """Only marks the result: the demo writes no file."""
    return MEMORY_RESULTS.get(option, "✖️ descartado")


async def demo_undo_list(item: Item, option: str, approval_id: int) -> HandlerOutcome:
    if "deleted" in (item.data or {}):
        return Show("He recreado «Andar 30 min» con un ID nuevo (inventado).", "↩️ recreada")
    return "↩️ deshecho"


def key_handler(clock: Clock) -> TapHandler:
    """The answers of the real buttons (task 1.15), without marks or Todoist; the [Deshacer]
    lasts as long as the demo's other buttons."""

    async def demo_key(item: Item, option: str, approval_id: int) -> HandlerOutcome:
        habit = str((item.data or {}).get("habit", "")).lower()
        if option == "go":
            return Answer(GOING, GO_TEXT, then=still_open(item))
        if option == "later":
            return Answer(LATER, LATER_TEXT, then=still_open(item))
        body, result = (
            (DONE_TEXT, DONE) if option == "done" else (SKIP_TEXT.format(habit=habit), SKIPPED)
        )
        undo = key_undo_message(body, item, {"action": option})
        return Answer(result, Followup(UNDO_KIND, undo, clock.now() + TTL, body))

    return demo_key


HANDLERS: dict[str, TapHandler] = {
    PLAN_KIND: demo_plan,
    UNDO_KIND: demo_undo,
    MEMORY_KIND: demo_memory,
    UNDO_LIST_KIND: demo_undo_list,
}


def refuse_unless_dev(paths: Paths) -> None:
    """Raise `DemoError` unless the copy is `dev` (`JIMIC_ENTORNO=dev`)."""
    if paths.environment != "dev":
        raise DemoError(
            f"la demo solo corre con ENTORNO=dev, y esta copia es {paths.environment}:"
            " lánzala con JIMIC_ENTORNO=dev"
        )


def demo_factory(settings: Settings, clock: Clock) -> ChannelFactory:
    """The channel of the demo: it sends its messages as soon as it is listening."""

    async def send_demo(channel: TelegramChannel) -> None:
        for kind, message in demo_messages(settings):
            await channel.send(kind, message, clock.now() + TTL)
        logger.info("demo sent; press Ctrl+C to stop")

    def factory(conn: sqlite3.Connection) -> TelegramChannel:
        return build_channel(
            settings,
            conn,
            clock,
            handlers=HANDLERS | {KEY_KIND: key_handler(clock)},
            on_started=send_demo,
        )

    return factory


def run(environ: Mapping[str, str]) -> None:
    """Run the demo until SIGINT or SIGTERM.

    Raises `DemoError` outside `dev` (before anything else), and `PathsError`, `ConfigError`,
    `MigrationError` or `ChannelError` like `jimic run`.
    """
    paths = resolve_paths(environ)
    refuse_unless_dev(paths)
    settings = load_settings(paths)
    clock = SystemClock(settings.timezone)
    asyncio.run(serve_until_signal(paths, clock, channel_factory=demo_factory(settings, clock)))
