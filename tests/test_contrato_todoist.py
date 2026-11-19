"""Contrato de Todoist contra la API real (decisión 1.5-c): lento y a mano.

Solo corre con `JIMIC_CONTRATO=1`, con las claves de `~/.config/jimic-dev/.env` (el proceso de
los tests las usa; el agente no las lee) y siempre en el proyecto de pruebas.

Mitigaciones:
- Ningún título real: las tareas se llaman `jimic-contrato-<uuid>`. Nada se imprime salvo
  booleanos y recuentos.
- Todo lo que se crea se borra en un `finally`, también la recurrente completada (que no
  desaparece: pasa a la siguiente fecha) y el padre con sus subtareas. Al final, el proyecto
  tiene las mismas tareas que al empezar.
- La guardia se prueba con dobles, nunca aquí; aquí se usa la guardada, que además impide
  tocar nada fuera del proyecto de pruebas.
"""

import asyncio
import json
import uuid
from datetime import date, datetime, timedelta

import pytest
from conftest import CONTRACT
from todoist_api_python.api_async import TodoistAPIAsync

from jimic.clock import SystemClock
from jimic.tools.failures import ToolFailure
from jimic.tools.todoist import TodoistTask, build_todoist

pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(not CONTRACT, reason="contrato: solo con JIMIC_CONTRATO=1"),
]

# Lo que el diseño da por cierto; cada uno lo comprueba un paso del contrato. Además se imprime,
# solo informativo, `subtasks_deleted_with_parent` (qué pasa con las subtareas al borrar el padre)
# y `todoist_keeps_duration_field` (falso en el plan gratuito: por eso la duración va también a
# la descripción).
REQUIRED = [
    "datetime_kept",
    "duration_kept",
    "read_project_finds_it",
    "read_today_finds_it",
    "move_works",
    "complete_normal_gone",
    "complete_recurring_next",
    "move_recurring_to_hour",
    "move_recurring_to_day",
    "move_recurring_spanish",
    "move_keeps_recurrence",
    "has_subtasks_flag",
    "delete_gone",
    "project_count_restored",
]


def name(label: str) -> str:
    return f"jimic-contrato-{label}-{uuid.uuid4().hex[:8]}"


def day(task: TodoistTask | None) -> date | None:
    if task is None or task.due is None:
        return None
    return task.due.date() if isinstance(task.due, datetime) else task.due


def test_contrato_de_todoist(contract_env):
    _, settings = contract_env
    project = settings.todoist_test_project
    assert project

    async def scenario() -> dict[str, bool | int]:
        clock = SystemClock(settings.timezone)
        raw = TodoistAPIAsync(settings.todoist_token.get_secret_value())
        tools = build_todoist(settings, clock, api=raw)
        reader, writer = tools.reader, tools.writer
        today = clock.now().date()
        seen: dict[str, bool | int] = {}
        created: list[str] = []  # todo lo que hay que borrar al final

        async def count() -> int:
            listing = await reader.read_project(project)
            assert listing.available, listing.unavailable_reason
            return len(listing.tasks)

        async def active_ids() -> set[str]:
            listing = await reader.read_project(project)
            assert listing.available, listing.unavailable_reason
            return {t.id for t in listing.tasks}

        try:
            before = await count()
            seen["tasks_before"] = before

            # 1. Crear con hora y duración; leer.
            due = datetime(today.year, today.month, today.day, 10, tzinfo=settings.timezone)
            due += timedelta(days=1)
            made = await writer.create(name("hora"), project, due, 75)
            created.append(made.id)
            stored = await raw.get_task(made.id)  # el campo en bruto, sin la descripción
            seen["todoist_keeps_duration_field"] = stored.duration is not None
            read = await reader.get_task(made.id)
            seen["datetime_kept"] = read is not None and read.due == due
            seen["duration_kept"] = read is not None and read.duration_minutes == 75
            seen["read_project_finds_it"] = made.id in await active_ids()

            # 2. Una de hoy, que el lector de hoy debe encontrar.
            todays = await writer.create(name("hoy"), project, today)
            created.append(todays.id)
            listing = await reader.read_today()
            seen["read_today_finds_it"] = listing.available and todays.id in {
                t.id for t in listing.tasks
            }

            # 3. Mover.
            new_due = due + timedelta(days=1, hours=1)
            await writer.move(made.id, new_due)
            moved = await reader.get_task(made.id)
            seen["move_works"] = moved is not None and moved.due == new_due

            # 4. Completar una normal: deja de estar activa.
            await writer.complete(todays.id)
            seen["complete_normal_gone"] = todays.id not in await active_ids()

            # 5. Una recurrente (solo la crea el contrato, con el cliente en bruto): al
            #    completarla pasa a la siguiente fecha y sigue activa.
            recurring = await raw.add_task(name("rec"), project_id=project, due_string="every day")
            created.append(recurring.id)
            first = await reader.get_task(recurring.id)
            await writer.complete(recurring.id)
            after = await reader.get_task(recurring.id)
            first_day, after_day = day(first), day(after)
            seen["complete_recurring_next"] = (
                after is not None
                and after.is_recurring
                and first_day is not None
                and after_day is not None
                and after_day > first_day
            )

            # 6. Mover una recurrente (con hora en su texto): ¿sigue siéndolo, y llega a la hora y
            #    al día pedidos?
            other = await raw.add_task(
                name("rec-mover"), project_id=project, due_string="every day at 9:00"
            )
            created.append(other.id)
            hour = datetime(today.year, today.month, today.day, 18, tzinfo=settings.timezone)
            hour += timedelta(days=3)
            await writer.move(other.id, hour)
            at_hour = await reader.get_task(other.id)
            seen["move_recurring_to_hour"] = (
                at_hour is not None and at_hour.is_recurring and at_hour.due == hour
            )
            await writer.move(other.id, today + timedelta(days=4))
            at_day = await reader.get_task(other.id)
            seen["move_recurring_to_day"] = (
                at_day is not None
                and at_day.is_recurring
                and day(at_day) == today + timedelta(days=4)
            )
            seen["move_keeps_recurrence"] = bool(
                seen["move_recurring_to_hour"] and seen["move_recurring_to_day"]
            )

            # 6b. Una recurrente escrita en español (como las que Pedro crea en la app): el texto
            #     viaja con su idioma y sigue siendo recurrente.
            spanish = await raw.add_task(
                name("rec-es"),
                project_id=project,
                due_string="todos los días a las 9:00",
                due_lang="es",
            )
            created.append(spanish.id)
            await writer.move(spanish.id, hour)
            at_spanish = await reader.get_task(spanish.id)
            seen["move_recurring_spanish"] = (
                at_spanish is not None and at_spanish.is_recurring and at_spanish.due == hour
            )

            # 7. Subtareas: el lector lo sabe, y qué pasa al borrar el padre (anexo B).
            parent = await raw.add_task(name("padre"), project_id=project, due_date=today)
            created.append(parent.id)
            child = await raw.add_task(name("hija"), project_id=project, parent_id=parent.id)
            created.append(child.id)
            with_child = await reader.get_task(parent.id)
            seen["has_subtasks_flag"] = with_child is not None and with_child.has_subtasks
            await writer.delete(parent.id)
            remaining = await active_ids()
            seen["subtasks_deleted_with_parent"] = child.id not in remaining

            # 8. Borrar: después ya no existe (Todoist no tiene papelera).
            doomed = await writer.create(name("borrar"), project, today)
            created.append(doomed.id)
            await writer.delete(doomed.id)
            seen["delete_gone"] = (
                doomed.id not in await active_ids() and await reader.get_task(doomed.id) is None
            )
        finally:
            cleaned = 0
            for task_id in created:
                try:
                    await writer.delete(task_id)
                    cleaned += 1
                except ToolFailure:  # ya no existe: borrada en el propio contrato
                    pass
            seen["cleaned_in_finally"] = cleaned
            seen["project_count_restored"] = await count() == seen.get("tasks_before")
            await tools.close()
            print("\nobservaciones:", json.dumps(seen, sort_keys=True))
        return seen

    seen = asyncio.run(scenario())
    missing = [key for key in REQUIRED if seen.get(key) is not True]
    assert not missing, f"el contrato no confirma: {missing}"
