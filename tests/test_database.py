"""Tests for SQLite schema and connection lifecycle guarantees."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
from threading import Barrier

import pytest

from app.database import (
    LATEST_SCHEMA_VERSION,
    UnsupportedDatabaseVersionError,
    create_connection,
    database_connection,
    get_schema_version,
    initialize_database,
    migrate_database,
)


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    """Return a unique database path for each test."""
    return tmp_path / "data" / "test_urls.db"


def test_initialize_database_creates_expected_schema(database_path: Path) -> None:
    """Initialization should create every required URL column."""
    initialize_database(database_path)

    with database_connection(database_path) as connection:
        rows = connection.execute("PRAGMA table_info('urls')").fetchall()

    columns = {row["name"] for row in rows}
    assert columns == {
        "id",
        "original_url",
        "short_code",
        "clicks",
        "created_at",
    }


def test_initialize_database_records_latest_schema_version(
    database_path: Path,
) -> None:
    """A new database should record the migration version it reached."""
    initialize_database(database_path)

    with database_connection(database_path) as connection:
        version = get_schema_version(connection)

    assert version == LATEST_SCHEMA_VERSION


def test_initialize_database_is_idempotent(database_path: Path) -> None:
    """Repeated initialization should not delete or duplicate stored rows."""
    initialize_database(database_path)
    with database_connection(database_path) as connection:
        connection.execute(
            "INSERT INTO urls (original_url, short_code) VALUES (?, ?)",
            ("https://example.com/first", "000001"),
        )

    initialize_database(database_path)
    with database_connection(database_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM urls").fetchone()[0]

    assert count == 1


def test_migration_adopts_unversioned_schema_without_losing_rows(
    database_path: Path,
) -> None:
    """Version-zero databases from earlier commits should upgrade in place."""
    database_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(database_path)
    try:
        connection.executescript(
            """
            CREATE TABLE urls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                original_url TEXT NOT NULL,
                short_code TEXT UNIQUE,
                clicks INTEGER NOT NULL DEFAULT 0 CHECK (clicks >= 0),
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            INSERT INTO urls (original_url, short_code, clicks)
            VALUES ('https://example.com/existing', '000001', 17);
            """
        )
        connection.commit()
    finally:
        connection.close()

    initialize_database(database_path)

    with database_connection(database_path) as connection:
        row = connection.execute(
            "SELECT original_url, short_code, clicks FROM urls"
        ).fetchone()
        version = get_schema_version(connection)
        indexes = connection.execute("PRAGMA index_list('urls')").fetchall()

    assert row is not None
    assert dict(row) == {
        "original_url": "https://example.com/existing",
        "short_code": "000001",
        "clicks": 17,
    }
    assert version == LATEST_SCHEMA_VERSION
    assert "idx_urls_short_code" in {index["name"] for index in indexes}


def test_migrations_apply_consecutive_versions_in_order(tmp_path: Path) -> None:
    """A database should advance through every missing migration."""
    connection = create_connection(tmp_path / "ordered.db")
    migrations = {
        1: ("CREATE TABLE example (id INTEGER PRIMARY KEY)",),
        2: ("ALTER TABLE example ADD COLUMN name TEXT",),
    }

    try:
        final_version = migrate_database(connection, migrations)
        columns = connection.execute("PRAGMA table_info('example')").fetchall()
    finally:
        connection.close()

    assert final_version == 2
    assert [column["name"] for column in columns] == ["id", "name"]


def test_failed_migration_rolls_back_only_its_version(tmp_path: Path) -> None:
    """Completed versions remain while partial work from a failure disappears."""
    connection = create_connection(tmp_path / "rollback.db")
    migrations = {
        1: ("CREATE TABLE stable (id INTEGER PRIMARY KEY)",),
        2: (
            "CREATE TABLE partial (id INTEGER PRIMARY KEY)",
            "THIS IS NOT VALID SQL",
        ),
    }

    try:
        with pytest.raises(sqlite3.OperationalError):
            migrate_database(connection, migrations)

        version = get_schema_version(connection)
        tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    finally:
        connection.close()

    table_names = {table["name"] for table in tables}
    assert version == 1
    assert "stable" in table_names
    assert "partial" not in table_names


def test_initialize_rejects_newer_database_version(database_path: Path) -> None:
    """Older application code must not modify an unknown future schema."""
    database_path.parent.mkdir(parents=True)
    connection = sqlite3.connect(database_path)
    future_version = LATEST_SCHEMA_VERSION + 1
    try:
        connection.execute(f"PRAGMA user_version = {future_version}")
        connection.execute("CREATE TABLE future_data (value TEXT)")
        connection.execute("INSERT INTO future_data VALUES ('preserve me')")
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        UnsupportedDatabaseVersionError,
        match="newer than supported",
    ):
        initialize_database(database_path)

    connection = sqlite3.connect(database_path)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        value = connection.execute("SELECT value FROM future_data").fetchone()[0]
    finally:
        connection.close()

    assert version == future_version
    assert value == "preserve me"


def test_concurrent_migration_runners_do_not_apply_version_twice(
    tmp_path: Path,
) -> None:
    """A migration lock should serialize simultaneous application startups."""
    database_path = tmp_path / "concurrent.db"
    start_together = Barrier(2)
    migrations = {
        1: ("CREATE TABLE example (id INTEGER PRIMARY KEY)",),
        2: ("ALTER TABLE example ADD COLUMN name TEXT",),
    }

    def migrate_from_one_process() -> int:
        connection = create_connection(database_path)
        try:
            start_together.wait()
            return migrate_database(connection, migrations)
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(migrate_from_one_process) for _ in range(2)]
        versions = [future.result() for future in futures]

    with database_connection(database_path) as connection:
        columns = connection.execute("PRAGMA table_info('example')").fetchall()

    assert versions == [2, 2]
    assert [column["name"] for column in columns] == ["id", "name"]


def test_connection_uses_row_factory(database_path: Path) -> None:
    """Query results should support access by descriptive column names."""
    initialize_database(database_path)

    connection = create_connection(database_path)
    try:
        row = connection.execute("SELECT 42 AS answer").fetchone()
    finally:
        connection.close()

    assert row is not None
    assert row["answer"] == 42


def test_database_connection_commits_successful_work(database_path: Path) -> None:
    """Changes should persist after a context exits normally."""
    initialize_database(database_path)
    with database_connection(database_path) as connection:
        connection.execute(
            "INSERT INTO urls (original_url, short_code) VALUES (?, ?)",
            ("https://example.com/committed", "000001"),
        )

    with database_connection(database_path) as connection:
        row = connection.execute(
            "SELECT original_url FROM urls WHERE short_code = ?",
            ("000001",),
        ).fetchone()

    assert row is not None
    assert row["original_url"] == "https://example.com/committed"


def test_database_connection_rolls_back_failed_work(database_path: Path) -> None:
    """An exception should prevent partial changes from being persisted."""
    initialize_database(database_path)

    with pytest.raises(RuntimeError, match="force rollback"):
        with database_connection(database_path) as connection:
            connection.execute(
                "INSERT INTO urls (original_url, short_code) VALUES (?, ?)",
                ("https://example.com/rolled-back", "000001"),
            )
            raise RuntimeError("force rollback")

    with database_connection(database_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM urls").fetchone()[0]

    assert count == 0


def test_schema_applies_defaults(database_path: Path) -> None:
    """New URLs should begin with zero clicks and a creation timestamp."""
    initialize_database(database_path)
    with database_connection(database_path) as connection:
        cursor = connection.execute(
            "INSERT INTO urls (original_url, short_code) VALUES (?, ?)",
            ("https://example.com/defaults", "000001"),
        )
        row = connection.execute(
            "SELECT id, clicks, created_at FROM urls WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()

    assert row is not None
    assert row["id"] == 1
    assert row["clicks"] == 0
    assert row["created_at"] is not None


def test_short_code_must_be_unique(database_path: Path) -> None:
    """The database should reject two rows with the same short code."""
    initialize_database(database_path)
    with database_connection(database_path) as connection:
        connection.execute(
            "INSERT INTO urls (original_url, short_code) VALUES (?, ?)",
            ("https://example.com/first", "000001"),
        )

    with pytest.raises(sqlite3.IntegrityError):
        with database_connection(database_path) as connection:
            connection.execute(
                "INSERT INTO urls (original_url, short_code) VALUES (?, ?)",
                ("https://example.com/second", "000001"),
            )


def test_short_code_has_explicit_index(database_path: Path) -> None:
    """The schema should include the named short-code lookup index."""
    initialize_database(database_path)
    with database_connection(database_path) as connection:
        rows = connection.execute("PRAGMA index_list('urls')").fetchall()

    index_names = {row["name"] for row in rows}
    assert "idx_urls_short_code" in index_names


def test_short_code_lookup_uses_an_index(database_path: Path) -> None:
    """SQLite's query planner should avoid a full table scan for code lookup."""
    initialize_database(database_path)
    with database_connection(database_path) as connection:
        rows = connection.execute(
            "EXPLAIN QUERY PLAN SELECT * FROM urls WHERE short_code = ?",
            ("000001",),
        ).fetchall()

    query_details = " ".join(row["detail"] for row in rows)
    assert "USING INDEX" in query_details


def test_click_count_cannot_be_negative(database_path: Path) -> None:
    """The database constraint should reject invalid analytics state."""
    initialize_database(database_path)

    with pytest.raises(sqlite3.IntegrityError):
        with database_connection(database_path) as connection:
            connection.execute(
                """
                INSERT INTO urls (original_url, short_code, clicks)
                VALUES (?, ?, ?)
                """,
                ("https://example.com/invalid", "000001", -1),
            )
