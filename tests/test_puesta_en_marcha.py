"""La puesta en marcha sin el agente (tarea 1.13): los esqueletos y el progreso, que se deduce de
los propios ficheros.

Todo con ficheros inventados en una carpeta temporal: ningún test toca los de Pedro.
"""

import os
import stat
from datetime import datetime

import pytest
from conftest import FixedClock
from memory_data import (
    AREA_NAMES,
    HABIT,
    PREFERENCES,
    ROUTINE,
    area_text,
    finished_objectives,
    objectives,
    write_finished,
    write_personal,
)

from jimic.memory import changes, onboarding
from jimic.memory.changes import ChangeRefused, Proposed
from jimic.memory.database import open_database
from jimic.memory.onboarding import create_skeletons, progress
from jimic.memory.personal import check, init

SKELETONS = ("rutina.md", "objetivos.md", "preferencias.md")


@pytest.fixture
def config_dir(tmp_path):
    folder = tmp_path / "config"
    init(folder)
    return folder


@pytest.fixture
def conn(tmp_path):
    connection = open_database(tmp_path / "jimic.db")
    yield connection
    connection.close()


@pytest.fixture
def clock():
    return FixedClock(datetime.fromisoformat("2030-01-07T09:00:00+00:00"))


def save(conn, clock, config_dir, name, before, after):
    return changes.save(
        conn, clock, config_dir, Proposed(name, "replace", before, after), "inventado", None
    )


# --- Los esqueletos ---------------------------------------------------------------------------


def test_los_esqueletos_pasan_config_check_recien_creados(config_dir):
    created = create_skeletons(config_dir)

    assert created == [(name, True) for name in SKELETONS]
    assert {report.name: report.status for report in check(config_dir)} == {
        "intenciones.yaml": "ok",
        "limites.yaml": "ok",
        **dict.fromkeys(SKELETONS, "ok"),
    }


def test_los_esqueletos_se_crean_con_0600(config_dir):
    create_skeletons(config_dir)

    for name in SKELETONS:
        assert stat.S_IMODE(os.stat(config_dir / name).st_mode) == 0o600


def test_los_esqueletos_nunca_sobrescriben_un_fichero_que_existe(config_dir):
    (config_dir / "rutina.md").write_text(ROUTINE, encoding="utf-8")

    created = create_skeletons(config_dir)
    again = create_skeletons(config_dir)

    assert dict(created) == {"rutina.md": False, "objetivos.md": True, "preferencias.md": True}
    assert dict(again) == dict.fromkeys(SKELETONS, False)
    assert (config_dir / "rutina.md").read_text(encoding="utf-8") == ROUTINE


def test_los_esqueletos_no_siguen_un_enlace_simbolico(config_dir, tmp_path):
    outside = tmp_path / "fuera.md"
    outside.write_text("de otro (inventado)\n", encoding="utf-8")
    (config_dir / "objetivos.md").symlink_to(outside)

    created = create_skeletons(config_dir)

    assert dict(created)["objetivos.md"] is False
    assert outside.read_text(encoding="utf-8") == "de otro (inventado)\n"


def test_los_esqueletos_llevan_la_estructura_y_nada_de_pedro(config_dir):
    create_skeletons(config_dir)

    objectives_text = (config_dir / "objetivos.md").read_text(encoding="utf-8")
    for name in AREA_NAMES:
        assert f"\n## {name}\n" in objectives_text
    preferences = (config_dir / "preferencias.md").read_text(encoding="utf-8")
    assert "## Iconos" in preferences and "| Tarea | Icono |" in preferences
    # Recién creados, no hay nada hecho: solo estructura y pistas en comentarios.
    state = progress(config_dir)
    assert state.done_parts() == []
    assert state.started and state.in_progress and not state.finished


def test_propose_change_sobre_un_markdown_antes_y_despues_de_los_esqueletos(config_dir):
    proposed = Proposed("rutina.md", "replace", "## Comidas", "## Comidas\n\n- A las 14:00")

    with pytest.raises(ChangeRefused) as refused:
        changes.check(config_dir, proposed)
    assert refused.value.code == "missing"
    assert "aún no existe" in refused.value.for_agent

    create_skeletons(config_dir)
    checked = changes.check(config_dir, proposed)
    assert "- A las 14:00" in checked.new_text


# --- El progreso ------------------------------------------------------------------------------


def test_sin_objetivos_la_puesta_en_marcha_no_ha_empezado(config_dir):
    state = progress(config_dir)

    assert not state.started and not state.in_progress and not state.finished
    assert set(state.invalid) == set(SKELETONS)


def test_reanudable_con_la_rutina_y_dos_areas_sigue_por_la_tercera(config_dir, conn, clock):
    create_skeletons(config_dir)
    (config_dir / "rutina.md").write_text(ROUTINE, encoding="utf-8")
    for name in AREA_NAMES[:2]:
        save(
            conn, clock, config_dir, "objetivos.md", f"## {name}", f"## {name}\n\n{area_text(name)}"
        )

    state = progress(config_dir)

    assert state.missing_parts()[0] == "el área «Hábitos» de los objetivos"
    assert state.done_parts() == [
        "la rutina",
        "el área «Gestión del tiempo»",
        "el área «Entrenamiento»",
    ]
    assert not any("Gestión del tiempo" in part for part in state.missing_parts())


def test_tras_deshacer_un_area_esa_area_vuelve_a_faltar(config_dir, conn, clock):
    create_skeletons(config_dir)
    first, second = AREA_NAMES[:2]
    save(
        conn, clock, config_dir, "objetivos.md", f"## {first}", f"## {first}\n\n{area_text(first)}"
    )
    last = save(
        conn,
        clock,
        config_dir,
        "objetivos.md",
        f"## {second}",
        f"## {second}\n\n{area_text(second)}",
    )
    assert progress(config_dir).missing_areas[0] == "Hábitos"

    assert changes.undo(conn, clock, config_dir, last) == "undone"

    assert progress(config_dir).missing_areas[0] == "Entrenamiento"


def test_una_edicion_a_mano_cuenta_sin_pasar_por_changes(config_dir):
    create_skeletons(config_dir)
    (config_dir / "rutina.md").write_text(ROUTINE, encoding="utf-8")

    assert progress(config_dir).routine_missing == ()


# --- «Terminada» ------------------------------------------------------------------------------


def test_terminada_con_todo_lo_de_la_definicion(tmp_path):
    state = progress(write_finished(tmp_path / "config"))

    assert state.finished and not state.in_progress
    assert state.missing_parts() == []


def _without_routine_content(config_dir):
    text = ROUTINE.replace("- Libres\n", "<!-- pendiente -->\n")
    (config_dir / "rutina.md").write_text(text, encoding="utf-8")


def _area_change(name, text):
    def change(config_dir):
        areas = {area: area_text(area) for area in AREA_NAMES}
        areas[name] = text
        (config_dir / "objetivos.md").write_text(objectives(areas), encoding="utf-8")

    return change


def _without_area(config_dir):
    areas = {area: area_text(area) for area in AREA_NAMES if area != "Empleo"}
    (config_dir / "objetivos.md").write_text(objectives(areas), encoding="utf-8")


def _without_habits(config_dir):
    path = config_dir / "intenciones.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace(HABIT.rstrip("\n"), "habits: []"),
        encoding="utf-8",
    )


def _without_tone(config_dir):
    text = PREFERENCES.replace("- Mensajes cortos\n\n", "")
    (config_dir / "preferencias.md").write_text(text, encoding="utf-8")


def _only_icons_header(config_dir):
    text = PREFERENCES.replace("| Gimnasio | 🏋️ |\n", "")
    (config_dir / "preferencias.md").write_text(text, encoding="utf-8")


def _invalid_limits(config_dir):
    (config_dir / "limites.yaml").write_text("max_messages_per_day: 8\n", encoding="utf-8")


@pytest.mark.parametrize(
    "break_it",
    [
        pytest.param(_invalid_limits, id="un-fichero-no-pasa-config-check"),
        pytest.param(_without_routine_content, id="una-seccion-de-la-rutina-vacia"),
        pytest.param(_without_area, id="falta-la-seccion-de-un-area"),
        pytest.param(_area_change("Salud", "Meta: dormir más\n"), id="un-area-sin-prioridad"),
        pytest.param(
            _area_change("Salud", "Meta: dormir más\nPrioridad: urgente\n"),
            id="una-prioridad-que-no-vale",
        ),
        pytest.param(_area_change("Salud", "Meta:\nPrioridad: media\n"), id="un-area-sin-meta"),
        pytest.param(
            _area_change("Gestión del tiempo", "Meta: llegar a todo\nPrioridad: alta\n"),
            id="tiempo-sin-medible",
        ),
        pytest.param(
            _area_change(
                "Gestión del tiempo",
                "Meta: llegar a todo\nPrioridad: alta\n\n- [declarado] Algo\n  - [medible] Sub\n",
            ),
            id="tiempo-con-medible-solo-de-subelemento",
        ),
        pytest.param(_without_habits, id="sin-habitos"),
        pytest.param(_without_tone, id="sin-tono"),
        pytest.param(_only_icons_header, id="iconos-solo-con-la-cabecera"),
    ],
)
def test_no_esta_terminada_si_falta_una_pieza(tmp_path, break_it):
    config_dir = write_finished(tmp_path / "config")
    assert progress(config_dir).finished

    break_it(config_dir)

    state = progress(config_dir)
    assert not state.finished
    assert state.in_progress
    assert state.missing_parts()


def test_un_area_en_ahora_no_no_necesita_meta_ni_medible(tmp_path):
    config_dir = write_finished(tmp_path / "config")
    _area_change("Gestión del tiempo", "Prioridad: ahora no\n")(config_dir)

    assert progress(config_dir).finished


def test_entrenamiento_no_necesita_objetivos_porque_van_en_apex(tmp_path):
    config_dir = write_finished(tmp_path / "config")
    text = (config_dir / "objetivos.md").read_text(encoding="utf-8")

    assert "Meta: ganar fuerza (inventado)\nPrioridad: alta\n\n## Hábitos" in text
    assert progress(config_dir).finished


def test_la_prioridad_se_normaliza(tmp_path):
    state = progress(write_finished(tmp_path / "config"))

    social = next(area for area in state.areas if area.name == "Social")
    assert social.priority == "ahora no"  # «Ahora no.» en el fichero


def test_el_resumen_agrupa_las_metas_por_prioridad(tmp_path):
    state = progress(write_finished(tmp_path / "config"))

    lines = state.summary().splitlines()

    assert lines[0] == "Tus metas por prioridad:"
    assert lines[1] == (
        "Alta: Gestión del tiempo, llegar a todo sin correr (inventado);"
        " Entrenamiento, ganar fuerza (inventado)."
    )
    assert lines[2] == "Media: Hábitos, leer cada noche."
    assert lines[3].startswith("Baja: Nutrición, algo de nutrición (inventado); Salud,")
    assert lines[4] == "Ahora no: Ocio, Social."
    assert len(lines) == 5


def test_el_orden_de_las_areas_es_el_de_la_entrevista():
    assert onboarding.AREAS == AREA_NAMES
    assert finished_objectives().index("## Gestión del tiempo") < finished_objectives().index(
        "## Social"
    )


def test_con_los_ficheros_de_la_1_12_el_progreso_no_falla(tmp_path):
    """Los inventados de la 1.12 (una sola área) son una puesta en marcha a medias."""
    state = progress(write_personal(tmp_path / "config"))

    assert state.in_progress
    assert state.missing_areas[0] == "Gestión del tiempo"


# --- El bit de la sugerencia (1.13-b) ---------------------------------------------------------


def test_la_sugerencia_se_reclama_una_vez_por_dia_y_sobrevive_a_un_reinicio(tmp_path, clock):
    from datetime import date

    from jimic.memory import conversations

    path = tmp_path / "jimic.db"
    first_conn = open_database(path)
    day = date(2030, 1, 7)
    conversations.note_message(first_conn, day, clock.now())
    assert conversations.claim_suggestion(first_conn, day) is True
    assert conversations.claim_suggestion(first_conn, day) is False
    first_conn.close()

    restarted = open_database(path)
    try:
        assert conversations.claim_suggestion(restarted, day) is False
        tomorrow = date(2030, 1, 8)
        assert conversations.claim_suggestion(restarted, tomorrow) is False  # sin fila aún
        conversations.note_message(restarted, tomorrow, clock.now())
        assert conversations.claim_suggestion(restarted, tomorrow) is True
    finally:
        restarted.close()


# --- La guarda de APEX (decisión 74) ----------------------------------------------------------

APEX_GOALS = ["Subir la fuerza", "Moverse más", "Fuerza"]


@pytest.mark.parametrize(
    ("before", "after", "copied"),
    [
        (None, "- [medible] Subir la fuerza", True),
        (None, "- [declarado] subir  LA  Fuerza este año", True),
        ("## Salud", "## Salud\n\n- moverse más", True),
        # Una descripción de una sola palabra no se compara: sería una coincidencia.
        (None, "Meta: ganar fuerza en sentadilla", False),
        # Palabras enteras: «subir la fuerzas» no es «subir la fuerza».
        (None, "- subir la fuerzas", False),
        # Ya estaba en el fichero (una edición a mano): cambiar al lado no la copia otra vez.
        (
            "## Entrenamiento\n- Subir la fuerza\nPrioridad: media",
            "## Entrenamiento\n- Subir la fuerza\nPrioridad: alta",
            False,
        ),
        (
            "## Entrenamiento\n- Subir la fuerza",
            "## Entrenamiento\n- Subir la fuerza\n- Subir la fuerza",
            True,
        ),
    ],
)
def test_la_guarda_de_apex_solo_ve_lo_que_el_cambio_anade(before, after, copied):
    assert onboarding.copied_goal(before, after, APEX_GOALS) is copied


@pytest.mark.parametrize(
    "text",
    [
        "**Meta:** llegar a todo\n**Prioridad:** alta\n\n- [medible] Cerrar 5 tareas\n",
        "Meta: **llegar a todo**\nPrioridad: **alta**.\n\n- [medible] Cerrar 5 tareas\n",
        "*Meta*: llegar a todo\n__Prioridad__: Alta\n\n- [medible] Cerrar 5 tareas\n",
    ],
    ids=["etiqueta-en-negrita", "valor-en-negrita", "cursiva-y-subrayado"],
)
def test_la_meta_y_la_prioridad_con_enfasis_cuentan(tmp_path, text):
    config_dir = write_finished(tmp_path / "config")
    _area_change("Gestión del tiempo", text)(config_dir)

    state = progress(config_dir)

    area = next(area for area in state.areas if area.name == "Gestión del tiempo")
    assert (area.priority, area.goal) == ("alta", "llegar a todo")
    assert state.finished


@pytest.mark.parametrize("filler", ["### Detalle\n", "---\n", "### Detalle\n\n***\n"])
def test_una_seccion_de_la_rutina_solo_con_un_subtitulo_o_una_raya_no_cuenta(tmp_path, filler):
    config_dir = write_finished(tmp_path / "config")
    text = ROUTINE.replace("- Como a las 14:00\n", filler)
    (config_dir / "rutina.md").write_text(text, encoding="utf-8")

    assert progress(config_dir).routine_missing == ("Comidas",)
