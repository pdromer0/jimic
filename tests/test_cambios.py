"""Los cambios de memoria: validar, guardar tras el botón, registrar en `changes` y deshacer.

Todo con ficheros inventados en una carpeta temporal (`memory_data`): ningún test toca los de
Pedro.
"""

import logging
import os
import stat
from datetime import datetime

import pytest
from conftest import FixedClock
from memory_data import (
    DELETIONS_LINE,
    PREFERENCES,
    QUIET_LINE,
    file_sha,
    write_personal,
)

from jimic.memory import changes
from jimic.memory.changes import ChangeRefused, Proposed
from jimic.memory.database import open_database
from jimic.memory.personal import check, template

NOW = datetime.fromisoformat("2030-01-07T09:00:00+00:00")
SECRET = "texto-secreto-zx31"


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "data" / "jimic.db")
    yield connection
    connection.close()


@pytest.fixture
def config_dir(tmp_path):
    return write_personal(tmp_path / "config")


CLOCK = FixedClock(NOW)


def replace(file: str, before: str, after: str) -> Proposed:
    return Proposed(file, "replace", before, after)


def quiet(to: str) -> Proposed:
    return replace("limites.yaml", QUIET_LINE, QUIET_LINE.replace('"07:00"', f'"{to}"'))


# --- Validación ---------------------------------------------------------------------------------


def test_un_cambio_valido_de_limites_se_comprueba_sin_escribir(config_dir):
    before = file_sha(config_dir / "limites.yaml")
    checked = changes.check(config_dir, quiet("08:00"))
    assert '"08:00"' in checked.new_text
    assert file_sha(config_dir / "limites.yaml") == before


@pytest.mark.parametrize(
    ("proposed", "code"),
    [
        (replace("intenciones.yaml", "type: daily_plan", "type: inventado"), "invalid"),
        (replace("intenciones.yaml", "priority: high", "priority: alta"), "changed"),
        (replace("limites.yaml", DELETIONS_LINE, "max_deletions_per_day: cinco"), "invalid"),
        (replace("limites.yaml", "budget:", "budget: ["), "invalid"),
        (replace("rutina.md", "## Comidas", "## Almuerzos"), "invalid"),
        (replace("objetivos.md", "- [medible] Entrenar", "- Entrenar"), "invalid"),
        (replace("preferencias.md", "| Gimnasio | 🏋️ |", "| Gimnasio |"), "invalid"),
    ],
    ids=[
        "tipo-que-no-existe",
        "antes-repetido",
        "tipo-mal",
        "yaml-roto",
        "rutina",
        "objetivos",
        "iconos",
    ],
)
def test_un_cambio_que_no_valida_se_rechaza_en_cada_fichero(config_dir, proposed, code):
    """Un cambio que no valida no se ofrece: `check` lo rechaza con su motivo, para el agente."""
    with pytest.raises(ChangeRefused) as raised:
        changes.check(config_dir, proposed)
    assert raised.value.code == code
    assert raised.value.for_agent


def test_solo_tipos_que_existen_en_intenciones(config_dir):
    with pytest.raises(ChangeRefused) as raised:
        changes.check(
            config_dir, replace("intenciones.yaml", "type: day_close", "type: borrar_todo")
        )
    assert "intenciones.yaml" in raised.value.for_agent


def test_max_deletions_de_5_a_3_se_ofrece_y_de_5_a_8_se_rechaza_con_su_explicacion(config_dir):
    changes.check(config_dir, replace("limites.yaml", DELETIONS_LINE, "max_deletions_per_day: 3"))
    with pytest.raises(ChangeRefused) as raised:
        changes.check(
            config_dir, replace("limites.yaml", DELETIONS_LINE, "max_deletions_per_day: 8")
        )
    assert raised.value.code == "relaxes"
    assert "max_deletions_per_day" in raised.value.for_agent
    assert "a mano" in raised.value.for_agent


def test_un_cambio_de_limites_que_solo_toca_un_comentario_se_rechaza(config_dir):
    proposed = replace("limites.yaml", "# tope de mensajes proactivos al día", "# otra cosa")
    with pytest.raises(ChangeRefused) as raised:
        changes.check(config_dir, proposed)
    assert raised.value.code == "no_value"


def test_un_antes_que_no_esta_o_esta_dos_veces_se_rechaza(config_dir):
    for before in ("max_deletions_per_day: 9", "free"):
        with pytest.raises(ChangeRefused) as raised:
            changes.check(config_dir, replace("limites.yaml", before, "x"))
        assert raised.value.code == "changed"


def test_un_fichero_que_no_existe_se_rechaza_salvo_ideas(config_dir):
    (config_dir / "rutina.md").unlink()
    with pytest.raises(ChangeRefused) as raised:
        changes.check(config_dir, Proposed("rutina.md", "add", None, "- Algo"))
    assert raised.value.code == "missing"
    checked = changes.check(config_dir, Proposed("ideas.md", "add", None, "- una idea"))
    assert checked.new_text == "- una idea\n"


def test_un_enlace_simbolico_se_rechaza(config_dir, tmp_path):
    """Pendiente de la 1.3: escribir a través de él saldría de la raíz de configuración."""
    outside = tmp_path / "fuera.md"
    outside.write_text(PREFERENCES, encoding="utf-8")
    (config_dir / "preferencias.md").unlink()
    (config_dir / "preferencias.md").symlink_to(outside)
    (config_dir / "ideas.md").symlink_to(outside)
    for proposed in (
        Proposed("preferencias.md", "add", None, "- Algo"),
        Proposed("ideas.md", "add", None, "- una idea"),
    ):
        with pytest.raises(ChangeRefused) as raised:
            changes.check(config_dir, proposed)
        assert raised.value.code == "symlink"
    assert outside.read_text(encoding="utf-8") == PREFERENCES


def test_en_ideas_solo_se_anade(config_dir):
    with pytest.raises(ChangeRefused):
        changes.check(config_dir, Proposed("ideas.md", "remove", "x", None))


def test_un_fichero_que_no_es_de_memoria_se_rechaza(config_dir):
    with pytest.raises(ChangeRefused) as raised:
        changes.check(config_dir, Proposed(".env", "add", None, "X=1"))
    assert raised.value.code == "unknown_file"


# --- Guardar ------------------------------------------------------------------------------------


def test_guardar_escribe_solo_el_fragmento_y_conserva_los_comentarios(conn, config_dir):
    """Fuera del fragmento, el fichero no cambia ni un byte: los 21 comentarios de la plantilla
    siguen, y `jimic config check` lo da por bueno."""
    original = template("limites.yaml")
    changes.save(conn, CLOCK, config_dir, quiet("08:00"), "No me escribas antes de las 8", 7)
    written = (config_dir / "limites.yaml").read_text(encoding="utf-8")
    assert written == original.replace(QUIET_LINE, QUIET_LINE.replace('"07:00"', '"08:00"'))
    assert written.count("#") == original.count("#")
    assert {r.name: r.status for r in check(config_dir)}["limites.yaml"] == "ok"
    assert stat.S_IMODE((config_dir / "limites.yaml").stat().st_mode) == 0o600


def test_guardar_deja_una_fila_con_el_parche_las_huellas_y_el_origen(conn, config_dir):
    before_sha = file_sha(config_dir / "limites.yaml")
    change_id = changes.save(conn, CLOCK, config_dir, quiet("08:00"), "No antes de las 8", 7)
    row = conn.execute(
        "SELECT created_at, file, before, after, source_message, operation, position,"
        " file_before_sha, file_after_sha, approval_id, reverted_at FROM changes WHERE id = ?",
        (change_id,),
    ).fetchone()
    text = template("limites.yaml")
    assert row == (
        "2030-01-07T09:00:00+00:00",
        "limites.yaml",
        QUIET_LINE,
        QUIET_LINE.replace('"07:00"', '"08:00"'),
        "No antes de las 8",
        "replace",
        text.index(QUIET_LINE),
        before_sha,
        file_sha(config_dir / "limites.yaml"),
        7,
        None,
    )


def test_guardar_con_el_fichero_cambiado_entre_medias_no_escribe(conn, config_dir):
    """Pedro editó a mano la línea: el «antes» ya no está."""
    path = config_dir / "limites.yaml"
    path.write_text(
        template("limites.yaml").replace(QUIET_LINE, QUIET_LINE.replace("23:00", "22:30")),
        encoding="utf-8",
    )
    edited = file_sha(path)
    with pytest.raises(ChangeRefused) as raised:
        changes.save(conn, CLOCK, config_dir, quiet("08:00"), "origen", 1)
    assert raised.value.result == "ha cambiado: no lo guardo"
    assert file_sha(path) == edited
    assert conn.execute("SELECT COUNT(*) FROM changes").fetchone() == (0,)


def test_guardar_revalida_el_fichero_entero_tal_como_esta_ahora(conn, config_dir):
    """El «antes» sigue, pero Pedro estropeó otra línea a mano: el resultado ya no vale."""
    path = config_dir / "limites.yaml"
    changes.check(config_dir, quiet("08:00"))
    path.write_text(
        template("limites.yaml").replace("max_insist_per_task: 3", "max_insist_per_task: tres"),
        encoding="utf-8",
    )
    edited = file_sha(path)
    with pytest.raises(ChangeRefused) as raised:
        changes.save(conn, CLOCK, config_dir, quiet("08:00"), "origen", 1)
    assert raised.value.result == "ya no vale: no lo guardo"
    assert file_sha(path) == edited


def test_una_escritura_que_falla_no_deja_fila(conn, config_dir, monkeypatch):
    def broken(path, text):
        raise OSError("disco lleno")

    monkeypatch.setattr(changes, "write_private", broken)
    with pytest.raises(OSError):
        changes.save(conn, CLOCK, config_dir, quiet("08:00"), "origen", 1)
    assert conn.execute("SELECT COUNT(*) FROM changes").fetchone() == (0,)


def test_la_primera_idea_crea_ideas_privado(conn, config_dir):
    """Ajuste 6: en una instalación nueva, [📝 Apuntar] crea `ideas.md` con 0600."""
    changes.save(conn, CLOCK, config_dir, Proposed("ideas.md", "add", None, "- una"), "o", 1)
    path = config_dir / "ideas.md"
    assert path.read_text(encoding="utf-8") == "- una\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_crlf_y_bom_se_conservan(conn, config_dir):
    path = config_dir / "preferencias.md"
    original = "\ufeff" + PREFERENCES.replace("\n", "\r\n")
    path.write_bytes(original.encode("utf-8"))
    changes.save(
        conn, CLOCK, config_dir, replace("preferencias.md", "Mensajes cortos", "Cortos"), "o", 1
    )
    assert path.read_bytes().decode("utf-8") == original.replace("Mensajes cortos", "Cortos")


# --- Deshacer -----------------------------------------------------------------------------------


def test_deshacer_restaura_exactamente_y_queda_registrado(conn, config_dir):
    path = config_dir / "limites.yaml"
    original = path.read_bytes()
    change_id = changes.save(conn, CLOCK, config_dir, quiet("08:00"), "origen", 1)
    assert changes.undo(conn, CLOCK, config_dir, change_id) == "undone"
    assert path.read_bytes() == original
    rows = conn.execute(
        "SELECT id, operation, revert_of, source_message, reverted_at IS NOT NULL"
        " FROM changes ORDER BY id"
    ).fetchall()
    assert rows == [
        (change_id, "replace", None, "origen", 1),
        (change_id + 1, "undo", change_id, "[Deshacer]", 0),
    ]
    assert [row.id for row in changes.recent(conn)] == [change_id + 1]


def test_deshacer_dos_veces_no_hace_nada_la_segunda(conn, config_dir):
    change_id = changes.save(conn, CLOCK, config_dir, quiet("08:00"), "origen", 1)
    changes.undo(conn, CLOCK, config_dir, change_id)
    assert changes.undo(conn, CLOCK, config_dir, change_id) == "already"


def test_deshacer_con_el_fichero_editado_dice_que_ha_cambiado(conn, config_dir):
    """Exige el fichero entero como lo dejó el cambio: una edición a mano, aunque sea en otra
    línea, lo bloquea."""
    path = config_dir / "limites.yaml"
    change_id = changes.save(conn, CLOCK, config_dir, quiet("08:00"), "origen", 1)
    path.write_text(path.read_text(encoding="utf-8") + "# nota a mano\n", encoding="utf-8")
    edited = file_sha(path)
    assert changes.undo(conn, CLOCK, config_dir, change_id) == "changed"
    assert file_sha(path) == edited
    assert conn.execute("SELECT COUNT(*) FROM changes").fetchone() == (1,)


def test_deshacer_del_ultimo_al_primero_en_un_mismo_fichero(conn, config_dir):
    first = changes.save(conn, CLOCK, config_dir, quiet("08:00"), "uno", 1)
    second = changes.save(
        conn,
        CLOCK,
        config_dir,
        replace("limites.yaml", DELETIONS_LINE, "max_deletions_per_day: 3"),
        "dos",
        2,
    )
    assert changes.undo(conn, CLOCK, config_dir, first) == "changed"
    assert changes.undo(conn, CLOCK, config_dir, second) == "undone"
    assert changes.undo(conn, CLOCK, config_dir, first) == "undone"
    assert (config_dir / "limites.yaml").read_text(encoding="utf-8") == template("limites.yaml")


def test_deshacer_un_cambio_de_limites_que_relaja_si_se_permite(conn, config_dir):
    """Devuelve lo que Pedro tenía a mano antes del cambio conversado, y solo con su botón."""
    change_id = changes.save(
        conn,
        CLOCK,
        config_dir,
        replace("limites.yaml", DELETIONS_LINE, "max_deletions_per_day: 3"),
        "o",
        1,
    )
    assert changes.undo(conn, CLOCK, config_dir, change_id) == "undone"
    assert DELETIONS_LINE in (config_dir / "limites.yaml").read_text(encoding="utf-8")


def test_deshacer_una_reversion_vuelve_a_aplicar_el_cambio(conn, config_dir):
    change_id = changes.save(conn, CLOCK, config_dir, quiet("08:00"), "o", 1)
    changes.undo(conn, CLOCK, config_dir, change_id)
    after_undo = (config_dir / "limites.yaml").read_text(encoding="utf-8")
    assert changes.undo(conn, CLOCK, config_dir, change_id + 1) == "undone"
    assert '"08:00"' in (config_dir / "limites.yaml").read_text(encoding="utf-8")
    assert after_undo == template("limites.yaml")


def test_deshacer_la_primera_idea_deja_el_fichero_vacio(conn, config_dir):
    """Ajuste 6: el «antes» de la primera idea es un fichero vacío."""
    change_id = changes.save(
        conn, CLOCK, config_dir, Proposed("ideas.md", "add", None, "- una"), "o", 1
    )
    assert changes.undo(conn, CLOCK, config_dir, change_id) == "undone"
    assert (config_dir / "ideas.md").read_text(encoding="utf-8") == ""


def test_en_ideas_solo_se_deshace_la_ultima(conn, config_dir):
    first = changes.save(
        conn, CLOCK, config_dir, Proposed("ideas.md", "add", None, "- una"), "o", 1
    )
    changes.save(conn, CLOCK, config_dir, Proposed("ideas.md", "add", None, "- dos"), "o", 2)
    assert changes.undo(conn, CLOCK, config_dir, first) == "changed"


def test_deshacer_un_cambio_que_no_existe(conn, config_dir):
    assert changes.undo(conn, CLOCK, config_dir, 99) == "unknown"


def test_recent_da_los_ultimos_10_sin_revertir(conn, config_dir):
    ids = [
        changes.save(
            conn, CLOCK, config_dir, Proposed("ideas.md", "add", None, f"- idea {n}"), "o", n
        )
        for n in range(12)
    ]
    assert [row.id for row in changes.recent(conn)] == ids[::-1][:10]


def test_los_logs_no_llevan_contenido(conn, config_dir, caplog):
    caplog.set_level(logging.DEBUG)
    proposed = Proposed("preferencias.md", "add", None, f"- {SECRET}")
    change_id = changes.save(conn, CLOCK, config_dir, proposed, f"origen {SECRET}", 1)
    changes.undo(conn, CLOCK, config_dir, change_id)
    with pytest.raises(ChangeRefused):
        changes.save(conn, CLOCK, config_dir, replace("preferencias.md", SECRET, "x"), SECRET, 2)
    assert caplog.records
    assert SECRET not in caplog.text
    assert "preferencias.md" in caplog.text


def test_se_lee_el_texto_exacto_sin_traducir_saltos(config_dir):
    (config_dir / "rutina.md").write_bytes(b"a\r\nb")
    assert changes.read_text(config_dir, "rutina.md") == "a\r\nb"
    assert os.path.exists(config_dir / "rutina.md")
