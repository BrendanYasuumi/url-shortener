"""SQLite connection management, schema initialization, and URL persistence."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from app.utils import encode_base62


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
