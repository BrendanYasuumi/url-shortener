"""Tests for data entering the API boundary."""

import pytest
from pydantic import ValidationError

from app.schemas import ShortenRequest


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/articles/fastapi?source=test",
        "http://localhost:8000/resource",
    ],
)
def test_shorten_request_accepts_http_urls(url: str) -> None:
    """HTTP and HTTPS destinations should pass request validation."""
    request = ShortenRequest.model_validate({"url": url})

    assert str(request.url) == url


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"url": "not a url"},
        {"url": "ftp://example.com/file"},
        {"url": "https://example.com", "unknown_field": True},
    ],
)
def test_shorten_request_rejects_invalid_payloads(
    payload: dict[str, object],
) -> None:
    """Missing, malformed, unsupported, or unexpected input should fail."""
    with pytest.raises(ValidationError):
        ShortenRequest.model_validate(payload)
