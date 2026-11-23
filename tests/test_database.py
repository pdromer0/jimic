"""SQLite: la base sube a la última versión, no repite migraciones y una que falla no deja nada."""

import sqlite3

import pytest

from jimic.memory.database import (
    Migration,
    MigrationError,
    load_migrations,
    migrate,
    open_database,
    schema_version,
)

TABLES = {
    "decisions",
    "notices",
    "approvals",
    "changes",
    "day_plans",
    "day_ratings",
    "plan_items",
    "deleted_tasks",
    "model_usage",
    "system_notices",
    "handovers",
    "conversations",
}
LATEST = 6
"""La última migración del paquete (0006, la de la puesta en marcha)."""


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {name for (name,) in rows}


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "datos" / "jimic.sqlite3"


def test_una_base_vacia_sube_a_la_ultima_version_con_todas_las_tablas(db_path):
    """Pilla una tabla que falta o sobra y un migrador que no anota la versión."""
    conn = open_database(db_path)
    try:
        assert table_names(conn) == TABLES
        assert schema_version(conn) == LATEST
    finally:
        conn.close()


def test_arrancar_dos_veces_no_repite_ninguna_migracion(db_path):
    """Pilla un migrador que no sube `user_version`: la segunda vez repetiría `CREATE TABLE`."""
    open_database(db_path).close()
    conn = open_database(db_path)
    try:
        assert migrate(conn, load_migrations()) == 0
        assert schema_version(conn) == LATEST
    finally:
        conn.close()


def test_una_migracion_que_falla_a_medias_no_deja_nada(db_path):
    """Pilla migraciones sin transacción: ni la tabla nueva ni la versión deben quedar."""
    conn = open_database(db_path)
    broken = Migration(
        LATEST + 1, "0005_broken.sql", "CREATE TABLE extra (x);\nCREATE TABLE decisions (y);"
    )
    try:
        with pytest.raises(sqlite3.OperationalError, match="already exists"):
            migrate(conn, [*load_migrations(), broken])
        assert schema_version(conn) == LATEST
        assert "extra" not in table_names(conn)
        assert not conn.in_transaction
    finally:
        conn.close()


def test_una_base_mas_nueva_que_el_codigo_es_un_error(tmp_path):
    """Pilla que un código viejo abra (y quizá estropee) una base de una versión posterior."""
    conn = sqlite3.connect(tmp_path / "nueva.sqlite3", autocommit=True)
    try:
        conn.execute(f"PRAGMA user_version = {LATEST + 1}")
        with pytest.raises(MigrationError, match=f"versión {LATEST + 1}"):
            migrate(conn, load_migrations())
    finally:
        conn.close()


def test_las_claves_ajenas_estan_activas(db_path):
    """Pilla `foreign_keys` apagado: un elemento del plan sin su plan se aceptaría."""
    conn = open_database(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):
            conn.execute(
                "INSERT INTO plan_items (day, todoist_task_id, status) VALUES (?, ?, ?)",
                ("2026-11-19", "tarea-falsa-1", "done"),
            )
    finally:
        conn.close()


def test_los_estados_fuera_de_la_lista_se_rechazan(db_path):
    """Pilla un `CHECK` que falta: un estado mal escrito entraría en la base."""
    conn = open_database(db_path)
    try:
        conn.execute(
            "INSERT INTO day_plans (day, confirmed_at, plan) VALUES (?, ?, ?)",
            ("2026-11-19", "2026-11-19T07:00:00+00:00", "[]"),
        )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute(
                "INSERT INTO plan_items (day, todoist_task_id, status) VALUES (?, ?, ?)",
                ("2026-11-19", "tarea-falsa-1", "hecha"),
            )
    finally:
        conn.close()


def test_las_migraciones_del_paquete_van_de_uno_en_uno():
    """Pilla un fichero de migración que no se empaqueta o mal numerado."""
    migrations = load_migrations()
    assert migrations[0].name == "0001_initial.sql"
    assert [m.version for m in migrations] == list(range(1, len(migrations) + 1))


def test_un_hueco_en_la_numeracion_es_un_error(tmp_path):
    """Pilla que se salte una migración perdida: 0001 y 0003 sin 0002."""
    (tmp_path / "0001_a.sql").write_text("CREATE TABLE a (x);")
    (tmp_path / "0003_c.sql").write_text("CREATE TABLE c (x);")
    with pytest.raises(MigrationError, match=r"\[1, 3\]"):
        load_migrations(tmp_path)


def test_un_nombre_de_migracion_no_valido_es_un_error(tmp_path):
    """Pilla un `.sql` que no sigue `NNNN_nombre.sql` y se ignoraría o se ordenaría mal."""
    (tmp_path / "1_a.sql").write_text("CREATE TABLE a (x);")
    with pytest.raises(MigrationError, match="1_a.sql"):
        load_migrations(tmp_path)


@pytest.mark.parametrize(
    "created_at",
    [
        "2026-04-16 10:00:00",
        "2026-04-16T10:00:00Z",
        "2026-04-16T12:00:00+02:00",
        "2026-04-16T10:00:00.500000+00:00",
    ],
)
def test_un_momento_que_no_es_el_de_to_db_se_rechaza(db_path, created_at):
    """Pilla un momento con otro formato, que la purga (comparando texto) ordenaría mal."""
    conn = open_database(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute(
                "INSERT INTO decisions (created_at, speak, reason) VALUES (?, 0, 'motivo')",
                (created_at,),
            )
        conn.execute(
            "INSERT INTO decisions (created_at, speak, reason) VALUES (?, 0, 'motivo')",
            ("2026-04-16T10:00:00+00:00",),
        )
    finally:
        conn.close()


def test_un_dia_que_no_es_yyyy_mm_dd_se_rechaza(db_path):
    """Pilla un día con otro formato en las tablas que se purgan por día."""
    conn = open_database(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute(
                "INSERT INTO day_ratings (day, rating, rated_at) VALUES (?, 3, ?)",
                ("19/11/2026", "2026-11-19T20:00:00+00:00"),
            )
    finally:
        conn.close()


def test_la_0004_anade_el_mensaje_de_telegram_a_approvals_sin_tocar_lo_que_habia(tmp_path):
    """Pilla una 0004 que no añade la columna o que pierde las filas de la 1.10."""
    conn = open_database(tmp_path / "jimic.db")
    try:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(approvals)")]
        assert columns[-1] == "telegram_message_id"
        conn.execute(
            "INSERT INTO approvals (created_at, expires_at, kind, payload)"
            " VALUES ('2030-01-07T09:00:00+00:00', '2030-01-07T23:00:00+00:00', 'plan', '{}')"
        )
        (value,) = conn.execute("SELECT telegram_message_id FROM approvals").fetchone()
        assert value is None
    finally:
        conn.close()


def test_la_0005_rehace_los_avisos_del_sistema_sin_perder_filas_y_admite_los_nuevos(tmp_path):
    """Pilla una 0005 que pierde las filas de `system_notices` o no admite las clases nuevas."""
    from jimic.memory.database import load_migrations, migrate

    conn = sqlite3.connect(tmp_path / "jimic.db", autocommit=True)
    try:
        migrate(conn, load_migrations()[:4])
        conn.execute(
            "INSERT INTO system_notices (kind, period, detail, detected_at)"
            " VALUES ('budget', '2030-01', '', '2030-01-07T09:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO changes (created_at, file, before, after, source_message)"
            " VALUES ('2030-01-07T09:00:00+00:00', 'rutina.md', 'a', 'b', 'o')"
        )
        migrate(conn, load_migrations())
        assert conn.execute("SELECT kind, period FROM system_notices").fetchall() == [
            ("budget", "2030-01")
        ]
        for kind in ("config_invalid", "memory_clipped"):
            conn.execute(
                "INSERT INTO system_notices (kind, period, detail, detected_at)"
                " VALUES (?, '2030-01-07', 'limites', '2030-01-07T09:00:00+00:00')",
                (kind,),
            )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO system_notices (kind, period, detected_at)"
                " VALUES ('otra', '2030-01-07', '2030-01-07T09:00:00+00:00')"
            )
        assert conn.execute("SELECT file, operation FROM changes").fetchall() == [
            ("rutina.md", None)
        ]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(deleted_tasks)")}
        assert {"restored_at", "restored_task_id"} <= columns
    finally:
        conn.close()


def test_la_0006_anade_el_bit_de_la_sugerencia_sin_perder_las_conversaciones(tmp_path):
    """Pilla una 0006 que pierde las filas de `conversations` o admite otro valor que 0 y 1."""
    from jimic.memory.database import load_migrations, migrate

    conn = sqlite3.connect(tmp_path / "jimic.db", autocommit=True)
    try:
        migrate(conn, load_migrations()[:5])
        conn.execute(
            "INSERT INTO conversations (day, last_message_at)"
            " VALUES ('2030-01-07', '2030-01-07T09:00:00+00:00')"
        )
        migrate(conn, load_migrations())
        assert conn.execute("SELECT day, onboarding_suggested FROM conversations").fetchall() == [
            ("2030-01-07", 0)
        ]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE conversations SET onboarding_suggested = 2")
    finally:
        conn.close()
