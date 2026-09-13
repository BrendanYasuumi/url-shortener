"""SQLite connection management, schema migrations, and URL persistence."""

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
import sqlite3
from types import MappingProxyType
from typing import Final

from app.utils import encode_base62


# Keys are target schema versions and values are the SQL statements needed to
# reach that version. Never edit a released migration: add the next numbered
# entry so existing databases follow the same history as new databases.
MIGRATIONS: Final[Mapping[int, tuple[str, ...]]] = MappingProxyType({
    1: (
        """
        CREATE TABLE IF NOT EXISTS urls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_url TEXT NOT NULL,
            short_code TEXT UNIQUE,
            clicks INTEGER NOT NULL DEFAULT 0 CHECK (clicks >= 0),
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_urls_short_code ON urls(short_code)",
    ),
})
LATEST_SCHEMA_VERSION: Final[int] = max(MIGRATIONS)


class UnsupportedDatabaseVersionError(RuntimeError):
    """Raised when a database was created by newer application code."""


def create_connection(database_path: str | Path) -> sqlite3.Connection:
    """Open and configure a connection to a SQLite database file.

    A new connection should be created for each independent unit of work. The
    caller owns the returned connection and is responsible for closing it.

    Args:
        database_path: Location of the SQLite database file.

    Returns:
        A configured SQLite connection whose rows support column-name access.
    """
    connection = sqlite3.connect(
        str(database_path),
        timeout=10.0,
        # FastAPI may execute synchronous dependency setup and route handling in
        # different worker threads. Each request will still own one connection.
        check_same_thread=False,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    return connection


def get_schema_version(connection: sqlite3.Connection) -> int:
    """Return the application schema version stored in SQLite's file header."""
    row = connection.execute("PRAGMA user_version").fetchone()
    if row is None:
        raise sqlite3.DatabaseError("SQLite did not return a schema version")
    return int(row[0])


def migrate_database(
    connection: sqlite3.Connection,
    migrations: Mapping[int, tuple[str, ...]] | None = None,
) -> int:
    """Apply pending schema migrations in order and return the final version.

    Each version runs in its own ``BEGIN IMMEDIATE`` transaction. The write
    lock is acquired before checking ``user_version``, preventing concurrent
    application startups from applying the same migration. A failed version is
    rolled back without undoing previously completed versions.

    Args:
        connection: Open SQLite connection with no active transaction.
        migrations: Optional migration plan used by focused tests. Production
            callers use the module's immutable migration history.

    Raises:
        UnsupportedDatabaseVersionError: If the database is newer than the
            supplied migration plan.
        ValueError: If migration versions are not consecutive from version 1.
        sqlite3.DatabaseError: If a migration statement fails.
    """
    migration_plan = MIGRATIONS if migrations is None else migrations
    latest_version = max(migration_plan, default=0)
    expected_versions = set(range(1, latest_version + 1))
    if set(migration_plan) != expected_versions:
        raise ValueError("migration versions must be consecutive starting at 1")
    if connection.in_transaction:
        raise sqlite3.ProgrammingError(
            "migrations require a connection without an active transaction"
        )

    while True:
        connection.execute("BEGIN IMMEDIATE")
        try:
            current_version = get_schema_version(connection)
            if current_version > latest_version:
                raise UnsupportedDatabaseVersionError(
                    "database schema version "
                    f"{current_version} is newer than supported version "
                    f"{latest_version}"
                )
            if current_version == latest_version:
                connection.commit()
                return current_version

            target_version = current_version + 1
            for statement in migration_plan[target_version]:
                connection.execute(statement)

            # SQLite does not accept bound parameters in PRAGMA assignments.
            # target_version comes only from the trusted migration mapping.
            connection.execute(f"PRAGMA user_version = {target_version}")
            connection.commit()
            if target_version == latest_version:
                return target_version
        except Exception:
            connection.rollback()
            raise


def initialize_database(database_path: str | Path) -> None:
    """Create or migrate the configured SQLite database to the latest schema.

    Version zero represents either a new file or the original unversioned
    schema. Migration 1 uses idempotent DDL, so both cases are upgraded without
    deleting existing rows.
    """
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    connection = create_connection(path)
    try:
        # Reject future schemas before changing the persistent journal mode.
        current_version = get_schema_version(connection)
        if current_version > LATEST_SCHEMA_VERSION:
            raise UnsupportedDatabaseVersionError(
                "database schema version "
                f"{current_version} is newer than supported version "
                f"{LATEST_SCHEMA_VERSION}"
            )

        # WAL is a persistent database setting. Configuring it during
        # initialization avoids asking every request to change journal modes.
        connection.execute("PRAGMA journal_mode = WAL")
        migrate_database(connection)
    finally:
        connection.close()


def create_url_record(
    connection: sqlite3.Connection,
    original_url: str,
) -> sqlite3.Row:
    """Insert an original URL, derive its Base62 code, and return the new row.

    The caller controls the transaction. Inserting first lets SQLite assign the
    unique integer ID; that ID is then encoded and saved on the same row.

    Args:
        connection: Open SQLite connection used for the transaction.
        original_url: Validated destination URL to persist.

    Returns:
        The complete database row, including its short code and timestamp.

    Raises:
        sqlite3.DatabaseError: If SQLite cannot provide or retrieve the new row.
        ValueError: If the generated ID exceeds six-character Base62 capacity.
    """
    cursor = connection.execute(
        "INSERT INTO urls (original_url) VALUES (?)",
        (original_url,),
    )
    url_id = cursor.lastrowid
    if url_id is None:
        raise sqlite3.DatabaseError("SQLite did not return an inserted row ID")

    short_code = encode_base62(url_id)
    connection.execute(
        "UPDATE urls SET short_code = ? WHERE id = ?",
        (short_code, url_id),
    )

    row = connection.execute(
        "SELECT * FROM urls WHERE id = ?",
        (url_id,),
    ).fetchone()
    if row is None:
        raise sqlite3.DatabaseError("Inserted URL could not be retrieved")
    return row


def find_url_by_short_code(
    connection: sqlite3.Connection,
    short_code: str,
) -> sqlite3.Row | None:
    """Return the URL row matching a short code through the indexed column.

    Args:
        connection: Open SQLite connection used for the query.
        short_code: Code whose stored metadata should be retrieved.

    Returns:
        The matching row, or ``None`` when the code does not exist.
    """
    return connection.execute(
        "SELECT * FROM urls WHERE short_code = ?",
        (short_code,),
    ).fetchone()


def record_click_and_get_url(
    connection: sqlite3.Connection,
    short_code: str,
) -> str | None:
    """Increment a short code's click count and return its destination URL.

    Performing ``clicks = clicks + 1`` inside SQLite makes the increment atomic:
    concurrent requests cannot read the same count and overwrite one another.

    Args:
        connection: Open SQLite connection used for the transaction.
        short_code: Code supplied in the redirect request path.

    Returns:
        The original URL when the code exists, otherwise ``None``.
    """
    cursor = connection.execute(
        "UPDATE urls SET clicks = clicks + 1 WHERE short_code = ?",
        (short_code,),
    )
    if cursor.rowcount == 0:
        return None

    row = connection.execute(
        "SELECT original_url FROM urls WHERE short_code = ?",
        (short_code,),
    ).fetchone()
    if row is None:
        raise sqlite3.DatabaseError("Updated URL could not be retrieved")
    return str(row["original_url"])


@contextmanager
def database_connection(
    database_path: str | Path,
) -> Iterator[sqlite3.Connection]:
    """Provide a connection with commit, rollback, and cleanup guarantees.

    Normal completion commits the transaction. If code inside the ``with``
    block raises an exception, every uncommitted change is rolled back before
    the exception continues to its caller. The connection always closes.
    """
    connection = create_connection(database_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
