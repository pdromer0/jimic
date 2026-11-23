"""Contrato de Todoist para las tareas clave (tarea 1.15): lento y a mano, con `JIMIC_CONTRATO=1`.

Comprueba con el plan gratuito lo que la 1.15 da por cierto: que una tarea normal completada hoy
aparece en las completadas de hoy y que reabrirla la devuelve a hoy; y que una recurrente
completada pasa a mañana y vuelve a hoy si se mueve. Si algo de esto falla, la tarea para y
pregunta (notas del chat).

Mitigaciones, como en `test_contrato_todoist.py`: títulos `jimic-contrato-<uuid>`, solo el
proyecto de pruebas (la guardia de `dev`), la recurrente es diaria y **sin hora** (regla de los
tests que no dejan basura) y todo se borra en un `finally`. Solo se imprimen booleanos y recuentos.
"""

import asyncio
import json
import uuid
from datetime import date, datetime

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

REQUIRED = [
    "completed_normal_listed",
    "get_task_of_completed",
    "reopen_normal_today",
    "complete_recurring_tomorrow",
    "recurring_back_by_move",
    "project_count_restored",
]
"""Lo que usa la 1.15: las completadas de hoy para una normal; leer una completada por su id (la
guardia lo hace antes de reabrir); reabrir una normal; y, con una recurrente, que completarla la
pasa a mañana y moverla a hoy la devuelve (el [Deshacer]). Informativo, lo que el contrato del
23-nov-2026 vio que **no** pasa: `completed_recurring_listed` (una recurrente completada no sale
en las completadas, ni a los 10 s ni por fecha de vencimiento: de ahí el salto, decisión 1.15-j)
y `reopen_recurring_today` (reabrirla no la devuelve a hoy)."""


def name(label: str) -> str:
    return f"jimic-contrato-{label}-{uuid.uuid4().hex[:8]}"


def day(task: TodoistTask | None) -> date | None:
    if task is None or task.due is None:
        return None
    return task.due.date() if isinstance(task.due, datetime) else task.due


def test_contrato_de_tareas_clave(contract_env):
    _, settings = contract_env
    project = settings.todoist_test_project
    assert project

    async def scenario() -> dict[str, bool | int]:
        clock = SystemClock(settings.timezone)
        raw = TodoistAPIAsync(settings.todoist_token.get_secret_value())
        tools = build_todoist(settings, clock, api=raw)
        reader, writer = tools.reader, tools.writer
        today = clock.now().astimezone(settings.timezone).date()
        seen: dict[str, bool | int] = {}
        created: list[str] = []

        async def count() -> int:
            listing = await reader.read_project(project)
            assert listing.available, listing.unavailable_reason
            return len(listing.tasks)

        async def completed_ids() -> set[str]:
            listing = await reader.read_completed_today()
            assert listing.available, listing.unavailable_reason
            return {task.id for task in listing.tasks}

        try:
            seen["tasks_before"] = await count()

            # 1. Una normal de hoy: completarla la lleva a las completadas de hoy.
            normal = await writer.create(name("normal"), project, today)
            created.append(normal.id)
            await writer.complete(normal.id)
            seen["completed_normal_listed"] = normal.id in await completed_ids()
            try:
                seen["get_task_of_completed"] = await reader.get_task(normal.id) is not None
            except ToolFailure:
                seen["get_task_of_completed"] = False

            # 2. Reabrirla la devuelve, abierta y de hoy.
            await writer.reopen(normal.id)
            back = await reader.get_task(normal.id)
            seen["reopen_normal_today"] = back is not None and day(back) == today

            # 3. Una recurrente diaria sin hora: completarla la pasa a mañana. ¿Sale en las
            #    completadas de hoy? (informativo)
            recurring = await raw.add_task(name("rec"), project_id=project, due_string="every day")
            created.append(recurring.id)
            first = await reader.get_task(recurring.id)
            seen["recurring_starts_today"] = day(first) == today
            await writer.complete(recurring.id)
            after = await reader.get_task(recurring.id)
            after_day = day(after)
            seen["complete_recurring_tomorrow"] = (
                after is not None
                and after.is_recurring
                and after_day is not None
                and after_day > today
            )
            listing = await reader.read_completed_today()
            assert listing.available, listing.unavailable_reason
            titles = {task.id: task.title for task in listing.tasks}
            seen["completed_recurring_listed"] = any(
                title == recurring.content for title in titles.values()
            )
            # 4. Reabrirla: ¿vuelve a hoy? Si no, moverla a hoy (lo que haría [Deshacer]).
            try:
                await writer.reopen(recurring.id)
                reopened = await reader.get_task(recurring.id)
                seen["reopen_recurring_today"] = day(reopened) == today
                seen["reopen_recurring_keeps_recurrence"] = bool(
                    reopened is not None and reopened.is_recurring
                )
            except ToolFailure:
                seen["reopen_recurring_today"] = False
            if not seen["reopen_recurring_today"]:  # lo que hace [Deshacer] con una recurrente
                await writer.move(recurring.id, today)
                moved = await reader.get_task(recurring.id)
                seen["recurring_back_by_move"] = bool(
                    moved is not None and moved.is_recurring and day(moved) == today
                )
        finally:
            cleaned = 0
            for task_id in created:
                try:
                    await writer.delete(task_id)
                    cleaned += 1
                except ToolFailure:
                    try:  # una completada que la guardia no ve: es nuestra, del proyecto
                        cleaned += int(await raw.delete_task(task_id))
                    except Exception:
                        pass
            seen["cleaned_in_finally"] = cleaned
            seen["project_count_restored"] = await count() == seen.get("tasks_before")
            await tools.close()
            print("\nobservaciones:", json.dumps(seen, sort_keys=True))
        return seen

    seen = asyncio.run(scenario())
    missing = [key for key in REQUIRED if seen.get(key) is not True]
    assert not missing, f"el contrato no confirma: {missing}"
