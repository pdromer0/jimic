"""The three sources of the day, built from the configuration and closed when done.

`open_sources` is the one place that builds them for the service: Todoist through
`build_todoist` (so with `ENTORNO=dev` the guard is always there), Calendar with the permission
of this copy, and APEX from its local file. Leaving the block closes the Todoist connection,
also after an error.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from jimic.clock import Clock
from jimic.config import Settings
from jimic.paths import Paths
from jimic.tools.apex import ApexReader, LocalApexReader
from jimic.tools.calendar import CalendarReader, build_calendar
from jimic.tools.todoist import TodoistApi, TodoistTools, build_todoist


@dataclass(frozen=True)
class Sources:
    todoist: TodoistTools
    calendar: CalendarReader
    apex: ApexReader


@asynccontextmanager
async def open_sources(
    settings: Settings, paths: Paths, clock: Clock, todoist_api: TodoistApi | None = None
) -> AsyncGenerator[Sources]:
    """Build the three sources; `todoist_api` stands in for the real client in tests."""
    todoist = build_todoist(settings, clock, todoist_api)
    try:
        yield Sources(
            todoist=todoist,
            calendar=build_calendar(paths, settings.timezone),
            apex=LocalApexReader(settings.apex_path, clock, settings.timezone),
        )
    finally:
        await todoist.close()
