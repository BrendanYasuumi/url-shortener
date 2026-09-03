"""SQLite connection management and schema initialization."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sqlite3


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS urls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_url TEXT NOT NULL,
    short_code TEXT UNIQUE,
    clicks INTEGER NOT NULL DEFAULT 0 CHECK (clicks >= 0),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_urls_short_code ON urls(short_code);
"""


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


def initialize_database(database_path: str | Path) -> None:
    """Create the database directory, URL table, and lookup index when absent.

    The operation is idempotent: running it repeatedly preserves existing data
    because the schema uses ``IF NOT EXISTS``.
    """
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with database_connection(path) as connection:
        # WAL is a persistent database setting. Configuring it during
        # initialization avoids asking every request to change journal modes.
        connection.execute("PRAGMA journal_mode = WAL")
        connection.executescript(SCHEMA_SQL)


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
