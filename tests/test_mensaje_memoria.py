"""El mensaje «Para tu memoria» y sus botones: [Guardar] [Cambiar] [No] y [📝 Apuntar].

Los toques pasan por `process_tap`, como en Telegram, con ficheros inventados en una carpeta
temporal.
"""

import asyncio
import stat
from datetime import datetime

import pytest
from conftest import FixedClock
from heartbeat_data import LIMITS
from memory_data import DELETIONS_LINE, QUIET_LINE, file_sha, write_personal

from jimic.channel import store
from jimic.channel.message import MessageTooLong, Tap, render, telegram_length
from jimic.channel.taps import process_tap
from jimic.execution.memory_message import (
    MEMORY_KIND,
    MemoryHandlers,
    memory_message,
)
from jimic.memory.database import open_database
from jimic.memory.live import LiveConfig
from jimic.memory.personal import template

NOW = datetime.fromisoformat("2030-01-07T09:00:00+00:00")
END = datetime.fromisoformat("2030-01-07T23:00:00+00:00")
PEDRO = "No me escribas antes de las 8"


def change(**changes) -> dict:
    return {
        "action": "change",
        "file": "limites.yaml",
        "operation": "replace",
        "before": QUIET_LINE,
        "after": QUIET_LINE.replace('"07:00"', '"08:00"'),
        "reason": "r",
        "origin": PEDRO,
        "after_external_content": False,
    } | changes


def idea(text: str = "Probar la app de notas") -> dict:
    return {
        "action": "idea",
        "text": text,
        "line": f"- 2030-01-07 · {text}",
        "origin": "Apunta que…",
        "after_external_content": False,
    }


class World:
    def __init__(self, tmp_path):
        self.conn = open_database(tmp_path / "data" / "jimic.db")
        self.clock = FixedClock(NOW)
        self.config_dir = write_personal(tmp_path / "config")
        self.config = LiveConfig.load(self.config_dir)
        self.handlers = MemoryHandlers(
            conn=self.conn, clock=self.clock, config_dir=self.config_dir, config=self.config
        )

    def show(self, *entries: dict) -> int:
        return store.create(self.conn, self.clock, MEMORY_KIND, memory_message(list(entries)), END)

    def tap(self, approval_id: int, item: int, option: str):
        return asyncio.run(
            process_tap(
                self.conn,
                self.clock,
                {MEMORY_KIND: self.handlers.memory},
                Tap(approval_id, item, option),
            )
        )

    def status(self, approval_id: int) -> str:
        approval = store.load(self.conn, approval_id)
        assert approval is not None
        return approval.status

    def change_rows(self) -> list[tuple]:
        return self.conn.execute("SELECT file, operation, source_message FROM changes").fetchall()


@pytest.fixture
def w(tmp_path):
    world = World(tmp_path)
    yield world
    world.conn.close()


# --- El mensaje ---------------------------------------------------------------------------------


def test_el_cambio_se_ensena_con_el_antes_y_el_despues_exactos_y_escapados():
    entry = change(before="a < b & c", after="a > b")
    view = render(memory_message([entry]), 1)
    assert view.text == (
        "<b>Para tu memoria</b>\n\n"
        "1. 📝 limites.yaml · cambiar\nAntes: a &lt; b &amp; c\nDespués: a &gt; b"
    )
    assert [[b.label for b in row] for row in view.keyboard] == [
        ["1 💾 Guardar", "1 ✏️ Cambiar", "1 ✖️ No"]
    ]


def test_add_y_remove_dicen_nada_donde_no_hay():
    view = render(
        memory_message(
            [
                change(file="preferencias.md", operation="add", before=None, after="- Café"),
                change(file="rutina.md", operation="remove", before="- Libres", after=None),
            ]
        ),
        1,
    )
    assert "1. 📝 preferencias.md · añadir\nAntes: (nada)\nDespués: - Café" in view.text
    assert "2. 📝 rutina.md · quitar\nAntes: - Libres\nDespués: (nada)" in view.text


def test_la_idea_lleva_apuntar_y_no_y_no_hay_fila_global():
    view = render(memory_message([idea()]), 1)
    assert "1. 💡 Probar la app de notas" in view.text
    assert [[b.label for b in row] for row in view.keyboard] == [["1 📝 Apuntar", "1 ✖️ No"]]


def test_el_cambio_de_las_8_ocupa_355_en_su_peor_estado():
    """Valor a mano del plan (con 40 `&` de estado), con la línea entera de `quiet_hours`."""
    from jimic.channel.message import worst_length

    assert worst_length(memory_message([change()])) == 355


@pytest.mark.parametrize(
    ("file", "most"),
    [
        ("intenciones.yaml", 1909),
        ("preferencias.md", 1910),
        ("limites.yaml", 1911),
        ("objetivos.md", 1911),
        ("rutina.md", 1913),
    ],
)
def test_el_tope_de_tamano_es_por_fichero(file, most):
    """Ajuste 12: «antes» y «después» ASCII iguales caben hasta `most` cada uno (valores del
    revisor), y uno más ya no. Nunca se recorta lo que se enseña."""
    fits = memory_message([change(file=file, before="x" * most, after="x" * most)])
    assert all(not item.title.endswith("…") for item in fits.items)
    with pytest.raises(MessageTooLong):
        memory_message([change(file=file, before="x" * (most + 1), after="x" * (most + 1))])


def test_2000_y_2000_no_caben():
    with pytest.raises(MessageTooLong):
        memory_message([change(before="a" * 2000, after="a" * 2000)])
    assert telegram_length("📝") == 2


# --- [Guardar] ----------------------------------------------------------------------------------


def test_guardar_escribe_registra_el_origen_y_recarga(w):
    approval = w.show(change())
    result = w.tap(approval, 1, "save")
    assert result.code == "resolved" and w.status(approval) == "approved"
    assert result.view is not None and "1. 📝 limites.yaml" in result.view.text
    assert "💾 guardado" in result.view.text and result.view.keyboard == ()
    assert '"08:00"' in (w.config_dir / "limites.yaml").read_text(encoding="utf-8")
    assert w.change_rows() == [("limites.yaml", "replace", PEDRO)]
    assert w.config.limits.quiet_hours.end.hour == 8


def test_guardar_con_el_fichero_cambiado_no_escribe_y_queda_rechazada(w):
    """Ajuste 11: «ha cambiado» deja la aprobación en `rejected`, no en `approved`."""
    approval = w.show(change())
    path = w.config_dir / "limites.yaml"
    path.write_text(
        template("limites.yaml").replace(QUIET_LINE, QUIET_LINE.replace("23:00", "22:00")),
        encoding="utf-8",
    )
    edited = file_sha(path)
    result = w.tap(approval, 1, "save")
    assert result.view is not None and "ha cambiado: no lo guardo" in result.view.text
    assert w.status(approval) == "rejected"
    assert file_sha(path) == edited and w.change_rows() == []
    assert w.config.limits.quiet_hours.start.hour == 23  # sin recargar: la última buena


def test_guardar_lo_que_ya_no_vale_queda_rechazada(w):
    entry = change(before=DELETIONS_LINE, after="max_deletions_per_day: 3")
    approval = w.show(entry)
    path = w.config_dir / "limites.yaml"
    path.write_text(
        template("limites.yaml").replace("max_insist_per_task: 3", "max_insist_per_task: x"),
        encoding="utf-8",
    )
    result = w.tap(approval, 1, "save")
    assert result.view is not None and "ya no vale: no lo guardo" in result.view.text
    assert w.status(approval) == "rejected" and w.change_rows() == []


def test_guardar_dos_veces_escribe_una(w):
    approval = w.show(change(file="preferencias.md", operation="add", before=None, after="- A"))
    w.tap(approval, 1, "save")
    assert w.tap(approval, 1, "save").code == "already_resolved"
    assert len(w.change_rows()) == 1


def test_guardar_tras_caducar_no_escribe(w):
    approval = w.show(change())
    w.clock.moment = END
    assert w.tap(approval, 1, "save").code == "expired"
    assert w.change_rows() == []


def test_cambiar_cierra_la_propuesta_y_pide_como(w):
    approval = w.show(change())
    result = w.tap(approval, 1, "edit")
    assert result.view is not None and "✏️ dime cómo lo quieres" in result.view.text
    assert w.status(approval) == "rejected" and w.change_rows() == []


def test_no_descarta(w):
    approval = w.show(change())
    w.tap(approval, 1, "no")
    assert w.status(approval) == "rejected" and w.change_rows() == []


def test_un_cambio_de_markdown_no_recarga_los_yaml(w, monkeypatch):
    calls = []
    monkeypatch.setattr(w.config, "reload", lambda: calls.append(1) or ())
    approval = w.show(change(file="preferencias.md", operation="add", before=None, after="- A"))
    w.tap(approval, 1, "save")
    assert calls == []


# --- [📝 Apuntar] (1.12-b) ----------------------------------------------------------------------


def test_apuntar_anade_la_linea_y_queda_en_changes(w):
    approval = w.show(idea())
    result = w.tap(approval, 1, "note")
    assert result.view is not None and "📝 apuntada" in result.view.text
    path = w.config_dir / "ideas.md"
    assert path.read_text(encoding="utf-8") == "- 2030-01-07 · Probar la app de notas\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert w.change_rows() == [("ideas.md", "add", "Apunta que…")]


def test_sin_apuntar_ideas_no_se_toca(w):
    w.show(idea())
    assert not (w.config_dir / "ideas.md").exists()


def test_apuntar_tras_ideas_que_ya_tenian_lineas(w):
    path = w.config_dir / "ideas.md"
    path.write_text("# Ideas\n- 2030-01-01 · una anterior", encoding="utf-8")
    w.tap(w.show(idea("Otra 🎯")), 1, "note")
    assert path.read_text(encoding="utf-8") == (
        "# Ideas\n- 2030-01-01 · una anterior\n- 2030-01-07 · Otra 🎯\n"
    )


def test_una_opcion_que_no_es_del_elemento_falla(w):
    approval = w.show(idea())
    assert w.tap(approval, 1, "save").code == "nothing_to_do"


def test_los_limites_de_la_prueba_son_los_de_la_plantilla():
    """Los valores que usan estos tests (23:00–07:00, 5 borrados) son los de la plantilla."""
    assert LIMITS["quiet_hours"] == {"from": "23:00", "to": "07:00"}
    assert DELETIONS_LINE in template("limites.yaml")
