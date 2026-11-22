"""La purga a 7 días de lo que escribe el motor del agente en su carpeta (decisión 23).

Los nombres de las carpetas y de lo que se guarda salen del contrato del 22-nov-2026; las demás
carpetas son las que el motor puede escribir en otros modos. Todo es inventado.
"""

import os
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import FixedClock

from jimic.agent.transcripts import purge_agent_files

NOW = datetime(2030, 1, 7, 4, 0, tzinfo=ZoneInfo("Europe/Madrid"))
CONTENT = [
    "projects/-ruta-del-agente/sesion-inventada.jsonl",
    "backups/.claude.json.backup.123",
    "history.jsonl",
    "todos/a.json",
    "debug/registro.txt",
    "shell-snapshots/b.sh",
    "file-history/c/d",
    "session-env/e",
]
NEEDED = [
    ".claude.json",
    "policy-limits.json",
    "policy-limits.json.signature.json",
    "policy-limits.json.stamp.json",
    "remote-settings.json",
    "remote-settings.json.synced-at",
    "plugins/store/complemento.json",
]


def write(folder, relative: str, days_old: float) -> None:
    path = folder / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("contenido inventado")
    moment = (NOW - timedelta(days=days_old)).timestamp()
    os.utime(path, (moment, moment))


def present(folder) -> set[str]:
    return {str(path.relative_to(folder)) for path in folder.rglob("*") if path.is_file()}


@pytest.mark.parametrize("relative", CONTENT)
def test_lo_que_lleva_contenido_se_borra_a_los_8_dias_y_se_queda_a_los_6(tmp_path, relative):
    """Ajuste 5 del chat: un fichero de 8 días en cada carpeta se va; uno de 6, no. Pilla una
    purga que solo mira `projects/`."""
    write(tmp_path, relative, 8)
    write(tmp_path, relative + ".reciente", 6)
    assert purge_agent_files(tmp_path, FixedClock(NOW)) == 1
    assert present(tmp_path) == {relative + ".reciente"}


def test_lo_que_el_motor_necesita_se_queda_aunque_sea_viejo(tmp_path):
    for relative in NEEDED:
        write(tmp_path, relative, 30)
    assert purge_agent_files(tmp_path, FixedClock(NOW)) == 0
    assert present(tmp_path) == set(NEEDED)


def test_el_corte_es_de_7_dias_justos(tmp_path):
    write(tmp_path, "projects/x/justo.jsonl", 7)
    write(tmp_path, "projects/x/pasado.jsonl", 7 + 1 / 86400)
    purge_agent_files(tmp_path, FixedClock(NOW))
    assert present(tmp_path) == {"projects/x/justo.jsonl"}


def test_las_carpetas_vacias_se_van_y_un_enlace_no_se_sigue(tmp_path):
    outside = tmp_path / "fuera"
    write(outside, "no-tocar.txt", 30)
    agent = tmp_path / "agent"
    write(agent, "projects/vieja/s.jsonl", 9)
    (agent / "enlace").symlink_to(outside, target_is_directory=True)
    purge_agent_files(agent, FixedClock(NOW))
    assert not (agent / "projects" / "vieja").exists()
    assert (outside / "no-tocar.txt").exists()


def test_sin_carpeta_no_hay_nada_que_purgar(tmp_path):
    assert purge_agent_files(tmp_path / "no-existe", FixedClock(NOW)) == 0
