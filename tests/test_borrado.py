"""El borrado de una tarea con todas las reglas de H1-2 (decisión 56), por el botón [Bórrala].

Todoist es el doble (`FakeTodoistApi`) detrás del lector y el escritor de verdad. Cada regla
tiene su test, y cada test dice qué error pilla.
"""

import asyncio
import json
from datetime import timedelta

import httpx
import pytest
from execution_data import ExecutionWorld, YieldingApi, todoist_task
from heartbeat_data import madrid

from jimic.channel.message import Tap
from jimic.channel.taps import process_tap
from jimic.memory.database import open_database


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


def world_with(conn, *tasks, **limits):
    return ExecutionWorld(conn, list(tasks), **limits)


def test_un_borrado_aprobado_borra_y_guarda_la_foto_entera(conn):
    """La foto lleva todo lo que `/deshacer` necesita (1.12): contenido, descripción, fecha,
    duración, proyecto, etiquetas, prioridad, motivo y aprobación."""
    raw = todoist_task("t1", "Andar 30 min", description="Por el parque", duration=30)
    raw["labels"], raw["priority"] = ["salud"], 2
    world = ExecutionWorld(
        conn,
        [raw],
    )
    world.api.free_plan = False
    approval, _ = world.show([world.delete_entry("t1", reason="duplica otra")])
    result = world.tap(approval, 1, "del")
    assert result.code == "resolved"
    assert world.message(approval).items[0].result == "✓ borrada"
    assert world.api.find("t1") is None
    assert world.photos() == [
        (
            "t1",
            "Andar 30 min",
            "Por el parque",
            "2030-01-07",
            "30m",
            "proyecto-inventado",
            json.dumps(["salud"]),
            2,
            "duplica otra",
            approval,
        )
    ]
    assert world.status(approval) == "approved"


def test_una_recurrente_no_se_borra_al_ejecutar(conn):
    """Pilla no comprobar la recurrencia antes de borrar (versión incorrecta 3): la foto de la
    propuesta no dice si es recurrente, así que solo la lectura fresca lo ve. Todoist borraría
    la serie entera."""
    world = world_with(conn, todoist_task("t1"))
    approval, _ = world.show([world.delete_entry("t1")])
    world.api.find("t1")["due"]["is_recurring"] = True  # pyright: ignore[reportOptionalSubscript]
    world.tap(approval, 1, "del")
    assert world.message(approval).items[0].result == "recurrente: no la borro"
    assert "delete_task" not in world.writes() and world.photos() == []


def test_una_con_subtareas_no_se_borra(conn):
    """Pilla borrar una tarea con subtareas: se irían con ella."""
    world = world_with(conn, todoist_task("t1"))
    approval, _ = world.show([world.delete_entry("t1")])
    world.api.tasks.append(todoist_task("hija", parent_id="t1"))
    world.tap(approval, 1, "del")
    assert world.message(approval).items[0].result == "tiene subtareas: no la borro"
    assert "delete_task" not in world.writes() and world.photos() == []


@pytest.mark.parametrize("due", ["2030-01-08", "2030-01-08T09:00:00", None])
def test_una_que_no_es_de_hoy_ni_vencida_se_rechaza_remitiendo_a_todoist(conn, due):
    """Pilla borrar lo de mañana o lo que no tiene fecha (solo hoy o vencidas)."""
    world = world_with(conn, todoist_task("t1", due=due))
    approval, _ = world.show([world.delete_entry("t1")])
    world.tap(approval, 1, "del")
    assert world.message(approval).items[0].result == "ni de hoy ni vencida: bórrala en Todoist"
    assert "delete_task" not in world.writes()


@pytest.mark.parametrize("due", ["2030-01-06", "2030-01-07T23:30:00"])
def test_una_vencida_o_de_hoy_con_hora_si_se_borra(conn, due):
    world = world_with(conn, todoist_task("t1", due=due))
    approval, _ = world.show([world.delete_entry("t1")])
    world.tap(approval, 1, "del")
    assert world.message(approval).items[0].result == "✓ borrada"


@pytest.mark.parametrize(
    "change",
    [
        {"content": "Otro título"},
        {"project_id": "otro-proyecto"},
        {"due": {"date": "2030-01-06", "string": "x", "lang": "en", "is_recurring": False}},
    ],
)
def test_si_la_foto_no_coincide_no_borra_y_vuelve_a_preguntar(conn, change):
    """Pilla un borrado de una tarea que cambió desde la propuesta (título, fecha o proyecto):
    no borra, enseña la tarea como está ahora y espera otro «sí»."""
    world = world_with(conn, todoist_task("t1", "Andar 30 min"))
    approval, _ = world.show([world.delete_entry("t1")])
    world.api.find("t1").update(change)  # pyright: ignore[reportOptionalMemberAccess]
    result = world.tap(approval, 1, "del")
    assert result.code == "renewed"
    item = world.message(approval).items[0]
    assert item.pending and item.note == "ha cambiado: ¿la borro así?"
    assert "delete_task" not in world.writes() and world.photos() == []
    assert result.view is not None and result.view.keyboard[0][0].label == "1 🗑️ Bórrala"
    # El segundo «sí», sobre la tarea tal como está ahora, sí borra.
    world.tap(approval, 1, "del")
    assert world.message(approval).items[0].result == "✓ borrada"


def test_la_misma_hora_en_otra_zona_es_la_misma_foto(conn):
    """Todoist puede devolver la hora en UTC y la foto la guarda con la zona: se comparan
    momentos, no textos."""
    world = world_with(conn, todoist_task("t1", due="2030-01-07T09:00:00"))
    approval, _ = world.show([world.delete_entry("t1")])
    world.api.find("t1")["due"]["date"] = "2030-01-07T08:00:00Z"  # pyright: ignore[reportOptionalSubscript]
    world.tap(approval, 1, "del")
    assert world.message(approval).items[0].result == "✓ borrada"


def test_el_sexto_borrado_del_dia_se_para(conn):
    """Pilla un tope que no se aplica o que cuenta mal: con 5 hoy, el 6.º no borra."""
    world = world_with(conn, todoist_task("t1"))
    for minute in range(5):
        world.add_photo(madrid("2030-01-07T00:00") + timedelta(minutes=minute))
    approval, _ = world.show([world.delete_entry("t1")])
    world.tap(approval, 1, "del")
    assert world.message(approval).items[0].result == "tope de borrados de hoy"
    assert "delete_task" not in world.writes()


def test_el_tope_cuenta_el_dia_local(conn):
    """Los de ayer no cuentan: 23:59:59 del 6 (22:59:59Z) fuera, 00:00 del 7 (23:00Z) dentro."""
    world = world_with(conn, todoist_task("t1"))
    for _ in range(3):
        world.add_photo(madrid("2030-01-06T23:59:59"))
    for _ in range(4):
        world.add_photo(madrid("2030-01-07T00:00"))
    approval, _ = world.show([world.delete_entry("t1")])
    world.tap(approval, 1, "del")
    assert world.message(approval).items[0].result == "✓ borrada"


def test_dos_borrados_a_la_vez_con_cuatro_hechos_solo_pasa_uno(conn):
    """Pilla contar y guardar la foto con un `await` en medio: dos borrados de aprobaciones
    distintas (un botón y un «sí») pasarían los dos con 4 hechos."""
    world = ExecutionWorld(conn, [todoist_task("t1"), todoist_task("t2")])
    assert isinstance(world.api, YieldingApi)
    for _ in range(4):
        world.add_photo(madrid("2030-01-07T08:00"))
    first, _ = world.show([world.delete_entry("t1")])
    second, _ = world.show([world.delete_entry("t2")])

    async def both():
        handlers = world.executor.handlers
        return await asyncio.gather(
            process_tap(conn, world.clock, handlers, Tap(first, 1, "del")),
            process_tap(conn, world.clock, handlers, Tap(second, 1, "del")),
        )

    asyncio.run(both())
    results = sorted(world.message(a).items[0].result or "" for a in (first, second))
    assert results == ["tope de borrados de hoy", "✓ borrada"]
    assert len(world.photos()) == 5


class FailsOnDelete(YieldingApi):
    """`delete_task` falla con un 503 las veces que diga `failures` (la lectura sí pasa)."""

    def __init__(self, *args, failures: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.failures = failures

    async def delete_task(self, task_id: str) -> bool:
        if self.failures:
            self.failures -= 1
            self.record("delete_task", task_id=task_id)
            raise httpx.HTTPStatusError(
                "x", request=httpx.Request("DELETE", "https://x"), response=httpx.Response(503)
            )
        return await super().delete_task(task_id)


def test_si_todoist_falla_la_foto_se_quita_y_se_puede_reintentar(conn):
    """Pilla una foto que queda tras un fallo claro (contaría en el tope sin haber borrado) o un
    elemento que no se puede reintentar."""
    world = with_api(conn, FailsOnDelete([todoist_task("t1")], failures=1))
    approval, _ = world.show([world.delete_entry("t1")])
    world.tap(approval, 1, "del")
    item = world.message(approval).items[0]
    assert (item.result, item.note) == (None, "falló (http_503)")
    assert world.photos() == []
    world.tap(approval, 1, "del")
    assert world.message(approval).items[0].result == "✓ borrada"
    assert len(world.photos()) == 1


class TimeoutOnDelete(YieldingApi):
    """`delete_task` hace su efecto (o no) y después da *timeout*, como un fallo de red."""

    def __init__(self, *args, deletes: bool, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.deletes = deletes

    async def delete_task(self, task_id: str) -> bool:
        if self.deletes:
            await super().delete_task(task_id)
        raise httpx.ReadTimeout("x")


def with_api(conn, api) -> ExecutionWorld:
    world = ExecutionWorld(conn, [])
    world.api = api
    world.todoist.reader._api = api  # pyright: ignore[reportAttributeAccessIssue] - the double
    world.todoist.writer._api = api  # pyright: ignore[reportAttributeAccessIssue] - the double
    return world


def test_un_timeout_tras_borrar_de_verdad_deja_la_foto_y_lo_da_por_hecho(conn):
    """Pilla quitar la foto tras un fallo ambiguo: la tarea se habría perdido sin ella."""
    world = with_api(conn, TimeoutOnDelete([todoist_task("t1")], deletes=True))
    approval, _ = world.show([world.delete_entry("t1")])
    world.tap(approval, 1, "del")
    assert world.message(approval).items[0].result == "✓ borrada"
    assert len(world.photos()) == 1


def test_un_timeout_sin_borrar_quita_la_foto_y_queda_fallado(conn):
    world = with_api(conn, TimeoutOnDelete([todoist_task("t1")], deletes=False))
    approval, _ = world.show([world.delete_entry("t1")])
    world.tap(approval, 1, "del")
    item = world.message(approval).items[0]
    assert (item.result, item.note) == (None, "falló (timeout)")
    assert world.photos() == []


def test_una_tarea_que_ya_no_existe_lo_dice(conn):
    world = world_with(conn, todoist_task("t1"))
    approval, _ = world.show([world.delete_entry("t1")])
    world.api.tasks.clear()
    world.tap(approval, 1, "del")
    assert world.message(approval).items[0].result == "ya no existe"


def test_dejala_no_toca_nada(conn):
    world = world_with(conn, todoist_task("t1"))
    approval, _ = world.show([world.delete_entry("t1")])
    world.tap(approval, 1, "keep")
    assert world.message(approval).items[0].result == "🆗 se queda"
    assert world.writes() == [] and world.status(approval) == "rejected"


class GoneOnDelete(YieldingApi):
    """La tarea desaparece entre la foto y el borrado: `delete_task` da 404."""

    async def delete_task(self, task_id: str) -> bool:
        self.tasks.clear()
        return await super().delete_task(task_id)


def test_si_la_tarea_desaparece_antes_de_borrarla_dice_que_ya_no_existe(conn):
    """Hallazgo 3 de la revisión: un 404 al borrar no es «falló», y la foto de algo que no
    borró JimiC no se queda (contaría en el tope)."""
    world = with_api(conn, GoneOnDelete([todoist_task("t1")]))
    approval, _ = world.show([world.delete_entry("t1")])
    world.tap(approval, 1, "del")
    assert world.message(approval).items[0].result == "ya no existe"
    assert world.photos() == []


class Error5xxOnDelete(YieldingApi):
    """Todoist borra y la pasarela contesta 504."""

    async def delete_task(self, task_id: str) -> bool:
        await super().delete_task(task_id)
        raise httpx.HTTPStatusError(
            "x", request=httpx.Request("DELETE", "https://x"), response=httpx.Response(504)
        )


def test_un_504_tras_borrar_de_verdad_tambien_deja_la_foto(conn):
    """Hallazgo 4 de la revisión: un 5xx también es ambiguo; sin releer, la tarea se perdería
    sin foto."""
    world = with_api(conn, Error5xxOnDelete([todoist_task("t1")]))
    approval, _ = world.show([world.delete_entry("t1")])
    world.tap(approval, 1, "del")
    assert world.message(approval).items[0].result == "✓ borrada"
    assert len(world.photos()) == 1
