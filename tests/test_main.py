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


def test_redirect_returns_307_and_preserves_destination(client: TestClient) -> None:
    """A known code should redirect without changing its destination URL."""
    destination = "https://example.com/articles/42?source=shortener#section"
    created = client.post("/shorten", json={"url": destination})
    short_code = created.json()["short_code"]

    response = client.get(f"/{short_code}", follow_redirects=False)

    assert response.status_code == 307
    assert response.headers["location"] == destination


def test_redirect_increments_click_count_atomically(
    client: TestClient,
    database_path: Path,
) -> None:
    """Every successful redirect should add exactly one database click."""
    created = client.post("/shorten", json={"url": "https://example.com/clicks"})
    short_code = created.json()["short_code"]

    for _ in range(3):
        response = client.get(f"/{short_code}", follow_redirects=False)
        assert response.status_code == 307

    with database_connection(database_path) as connection:
        row = connection.execute(
            "SELECT clicks FROM urls WHERE short_code = ?",
            (short_code,),
        ).fetchone()

    assert row is not None
    assert row["clicks"] == 3


@pytest.mark.parametrize("short_code", ["ABC123", "missing", "000000"])
def test_redirect_returns_404_for_unknown_code(
    client: TestClient,
    short_code: str,
) -> None:
    """An unknown code should return a stable JSON error instead of redirecting."""
    response = client.get(f"/{short_code}", follow_redirects=False)

    assert response.status_code == 404
    assert response.json() == {"detail": "Short URL not found."}


def test_analytics_returns_created_url_metadata(client: TestClient) -> None:
    """Analytics should expose the URL, code, initial clicks, and timestamp."""
    destination = "https://example.com/analytics"
    created = client.post("/shorten", json={"url": destination}).json()

    response = client.get(f"/analytics/{created['short_code']}")

    assert response.status_code == 200
    assert response.json() == {
        "original_url": destination,
        "short_code": created["short_code"],
        "clicks": 0,
        "created_at": created["created_at"],
    }


def test_analytics_reports_successful_redirects(client: TestClient) -> None:
    """The analytics total should reflect every successful redirect."""
    created = client.post(
        "/shorten",
        json={"url": "https://example.com/analytics-clicks"},
    ).json()
    short_code = created["short_code"]

    for _ in range(3):
        client.get(f"/{short_code}", follow_redirects=False)

    response = client.get(f"/analytics/{short_code}")

    assert response.status_code == 200
    assert response.json()["clicks"] == 3


def test_reading_analytics_does_not_increment_clicks(client: TestClient) -> None:
    """Analytics reads are not redirects and should not count as visits."""
    created = client.post(
        "/shorten",
        json={"url": "https://example.com/read-only-analytics"},
    ).json()
    endpoint = f"/analytics/{created['short_code']}"

    first = client.get(endpoint)
    second = client.get(endpoint)

    assert first.json()["clicks"] == 0
    assert second.json()["clicks"] == 0


@pytest.mark.parametrize("short_code", ["ABC123", "missing", "000000"])
def test_analytics_returns_404_for_unknown_code(
    client: TestClient,
    short_code: str,
) -> None:
    """Unknown analytics codes should use the standard not-found response."""
    response = client.get(f"/analytics/{short_code}")

    assert response.status_code == 404
    assert response.json() == {"detail": "Short URL not found."}
