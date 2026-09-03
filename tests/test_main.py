"""Integration tests for the FastAPI URL creation endpoint."""

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.database import database_connection
from app.main import create_app


@pytest.fixture
def database_path(tmp_path: Path) -> Path:
    """Return a unique SQLite file for each API test."""
    return tmp_path / "api_urls.db"


@pytest.fixture
def client(database_path: Path) -> Iterator[TestClient]:
    """Run the application lifespan around an isolated test client."""
    with TestClient(create_app(database_path)) as test_client:
        yield test_client


def test_root_endpoint_remains_available(client: TestClient) -> None:
    """Adding URL creation should not regress the existing root endpoint."""
    response = client.get("/")

    assert response.status_code == 200
    assert response.json() == {"message": "URL Shortener API is running"}


def test_shorten_creates_url_and_returns_201(client: TestClient) -> None:
    """A valid destination should produce a complete creation response."""
    destination = "https://example.com/articles/fastapi?source=test"

    response = client.post("/shorten", json={"url": destination})

    assert response.status_code == 201
    body = response.json()
    assert body["original_url"] == destination
    assert body["short_code"] == "000001"
    assert body["short_url"] == "http://testserver/000001"
    assert datetime.fromisoformat(body["created_at"])


def test_shorten_persists_url_in_database(
    client: TestClient,
    database_path: Path,
) -> None:
    """The response should represent a row committed to SQLite."""
    destination = "https://example.com/persisted"
    response = client.post("/shorten", json={"url": destination})
    code = response.json()["short_code"]

    with database_connection(database_path) as connection:
        row = connection.execute(
            "SELECT original_url, short_code, clicks FROM urls WHERE short_code = ?",
            (code,),
        ).fetchone()

    assert row is not None
    assert row["original_url"] == destination
    assert row["short_code"] == code
    assert row["clicks"] == 0


def test_shorten_assigns_unique_sequential_codes(client: TestClient) -> None:
    """Different inserted IDs should produce different Base62 codes."""
    first = client.post("/shorten", json={"url": "https://example.com/first"})
    second = client.post("/shorten", json={"url": "https://example.com/second"})

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["short_code"] == "000001"
    assert second.json()["short_code"] == "000002"


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"url": "not a url"},
        {"url": "ftp://example.com/file"},
        {"url": "https://example.com", "unknown_field": True},
    ],
)
def test_shorten_returns_422_for_invalid_payloads(
    client: TestClient,
    payload: dict[str, object],
) -> None:
    """FastAPI should reject invalid JSON before executing database logic."""
    response = client.post("/shorten", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]
