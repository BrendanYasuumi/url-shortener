"""Tests for structured logging and request-correlation behavior."""

from collections.abc import Iterator
from io import StringIO
import json
import logging
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.logging_config import JsonFormatter, logger
from app.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """Run the API against an isolated database for observability tests."""
    with TestClient(create_app(tmp_path / "observability.db")) as test_client:
        yield test_client


def test_response_contains_generated_request_id_and_timing(
    client: TestClient,
) -> None:
    """Requests without correlation headers should receive generated values."""
    response = client.get("/")

    UUID(response.headers["x-request-id"])
    assert float(response.headers["x-process-time-ms"]) >= 0


def test_valid_client_request_id_is_preserved(client: TestClient) -> None:
    """A safe upstream ID should correlate the request and response."""
    request_id = "gateway.request-123"

    response = client.get("/", headers={"X-Request-ID": request_id})

    assert response.headers["x-request-id"] == request_id


def test_unsafe_client_request_id_is_replaced(client: TestClient) -> None:
    """Values unsuitable for logs and headers should not be reflected."""
    response = client.get(
        "/",
        headers={"X-Request-ID": "request id with spaces"},
    )

    generated_id = response.headers["x-request-id"]
    UUID(generated_id)
    assert generated_id != "request id with spaces"


def test_request_log_contains_structured_context(client: TestClient) -> None:
    """A completed request should emit searchable correlation and timing data."""
    output = StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)

    try:
        response = client.get(
            "/missing",
            headers={"X-Request-ID": "observable-request"},
            follow_redirects=False,
        )
    finally:
        logger.removeHandler(handler)

    records = [json.loads(line) for line in output.getvalue().splitlines()]
    request_record = next(
        record for record in records if record.get("event") == "request_completed"
    )

    assert response.status_code == 404
    assert request_record["request_id"] == "observable-request"
    assert request_record["method"] == "GET"
    assert request_record["path"] == "/missing"
    assert request_record["status_code"] == 404
    assert request_record["duration_ms"] >= 0
    assert request_record["level"] == "WARNING"


def test_unhandled_exception_is_logged_and_returned_safely(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected errors should retain correlation without leaking details."""
    private_message = "private unexpected failure"

    def fail_to_find_url(*_: object) -> None:
        raise RuntimeError(private_message)

    monkeypatch.setattr("app.main.find_url_by_short_code", fail_to_find_url)

    output = StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)

    try:
        response = client.get(
            "/analytics/000001",
            headers={"X-Request-ID": "failed-request"},
        )
    finally:
        logger.removeHandler(handler)

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error."}
    assert response.headers["x-request-id"] == "failed-request"
    assert private_message not in response.text

    records = [json.loads(line) for line in output.getvalue().splitlines()]
    failure_record = next(
        record for record in records if record.get("event") == "request_failed"
    )
    assert failure_record["request_id"] == "failed-request"
    assert failure_record["status_code"] == 500
    assert failure_record["error_type"] == "RuntimeError"
    assert private_message in failure_record["exception"]
