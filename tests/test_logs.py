"""Logs: a la salida de errores y a un fichero diario de 7 días, sin el contenido nunca."""

import logging
from datetime import datetime
from logging.handlers import TimedRotatingFileHandler
from zoneinfo import ZoneInfo

from conftest import FixedClock

from jimic.logs import LOG_FILE, LOGGER_NAME, logging_to
from jimic.memory.database import open_database
from jimic.memory.retention import purge

KNOWN_TITLE = "Recoger el paraguas inventado del tinte Zq7"
"""Título de tarea inventado y fácil de buscar en un log."""


def test_el_fichero_rota_a_medianoche_y_guarda_siete_dias(tmp_path):
    """Pilla una rotación distinta o sin límite, y 7 copias más el fichero del día (8 días)."""
    logs_dir = tmp_path / "logs"
    with logging_to(logs_dir) as logger:
        files = [h for h in logger.handlers if isinstance(h, TimedRotatingFileHandler)]
        streams = [h for h in logger.handlers if type(h) is logging.StreamHandler]
        assert len(files) == 1
        assert len(streams) == 1
        assert files[0].when == "MIDNIGHT"
        assert files[0].backupCount == 6
        assert files[0].baseFilename == str(logs_dir / LOG_FILE)


def test_al_salir_vacia_y_quita_los_manejadores(tmp_path, capsys):
    """Pilla un log que se pierde al parar o manejadores que se acumulan en cada arranque."""
    logs_dir = tmp_path / "logs"
    with logging_to(logs_dir) as logger:
        logger.info("evento de prueba 1")
    assert logging.getLogger(LOGGER_NAME).handlers == []
    assert "evento de prueba 1" in (logs_dir / LOG_FILE).read_text()
    assert "evento de prueba 1" in capsys.readouterr().err


def test_el_titulo_de_una_tarea_borrada_no_aparece_en_el_log(tmp_path):
    """Pilla un log con contenido: la purga de una tarea con título conocido solo da recuentos."""
    logs_dir = tmp_path / "logs"
    with logging_to(logs_dir):
        conn = open_database(tmp_path / "jimic.sqlite3")
        conn.execute(
            "INSERT INTO deleted_tasks (deleted_at, todoist_task_id, content, description,"
            " project_id, priority, reason) VALUES (?, 'tarea-falsa', ?, ?, 'proyecto-falso',"
            " 1, ?)",
            ("2026-01-01T00:00:00+00:00", KNOWN_TITLE, KNOWN_TITLE, KNOWN_TITLE),
        )
        now = datetime(2026, 11, 19, 9, 0, tzinfo=ZoneInfo("Europe/Madrid"))
        assert purge(conn, FixedClock(now))["deleted_tasks"] == 1
        conn.close()
    log = (logs_dir / LOG_FILE).read_text()
    assert "deleted_tasks=1" in log
    assert KNOWN_TITLE not in log
    assert "Zq7" not in log
