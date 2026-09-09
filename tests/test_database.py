"""Tests for SQLite schema and connection lifecycle guarantees."""

from pathlib import Path
import sqlite3

import pytest

from app.database import create_connection, database_connection, initialize_database


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
