"""SQLite access: open the database and bring its schema up to date.

The schema version lives in `PRAGMA user_version`. Migrations are numbered SQL files in
`migrations/` (`0001_initial.sql`, ...); each runs in its own transaction together with the
version bump, so a failure leaves neither half-applied.
"""

import logging
import re
import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

logger = logging.getLogger(__name__)

MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")


class MigrationError(Exception):
    """The migrations or the database version do not fit; the message is shown to the user."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str


@contextmanager
def transaction(conn: sqlite3.Connection) -> Generator[None]:
    """Run the block in one explicit transaction: commit on success, roll back on any error.

    Connections are opened with `autocommit=True`, so transactions are always explicit.
    """
    conn.execute("BEGIN")
    try:
        yield
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")


def load_migrations(directory: Traversable | None = None) -> list[Migration]:
    """Return the migrations in `directory` (the package's by default), checked and in order."""
    if directory is None:
        directory = files("jimic.memory").joinpath("migrations")
    migrations = []
    for entry in directory.iterdir():
        if not entry.name.endswith(".sql"):
            continue
        match = MIGRATION_NAME.match(entry.name)
        if match is None:
            raise MigrationError(f"nombre de migración no válido: {entry.name}")
        migrations.append(Migration(int(match[1]), entry.name, entry.read_text(encoding="utf-8")))
    migrations.sort(key=lambda m: m.version)
    versions = [m.version for m in migrations]
    if versions != list(range(1, len(migrations) + 1)):
        raise MigrationError(f"las migraciones deben ir de 1 en 1 desde 0001: {versions}")
    return migrations


def schema_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def migrate(conn: sqlite3.Connection, migrations: list[Migration]) -> int:
    """Apply the migrations newer than the database; return how many were applied."""
    current = schema_version(conn)
    latest = migrations[-1].version if migrations else 0
    if current > latest:
        raise MigrationError(
            f"la base está en la versión {current} y este código solo llega a la {latest}"
        )
    applied = 0
    for migration in migrations:
        if migration.version <= current:
            continue
        with transaction(conn):
            conn.executescript(migration.sql)
            conn.execute(f"PRAGMA user_version = {migration.version}")
        logger.info("migration applied: %s", migration.name)
        applied += 1
    return applied


def open_database(path: Path) -> sqlite3.Connection:
    """Open (or create) the database at `path` and migrate it to the latest version."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    conn = sqlite3.connect(path, autocommit=True)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        migrate(conn, load_migrations())
    except BaseException:
        conn.close()
        raise
    logger.info("database open at schema version %d", schema_version(conn))
    return conn
