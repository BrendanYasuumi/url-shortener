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
    "custom_alias",
    ["abc", "portfolio", "portfolio-2026", "a" * 32, "123456"],
)
def test_shorten_request_accepts_valid_custom_aliases(custom_alias: str) -> None:
    """Aliases should use the documented lowercase URL-safe format."""
    request = ShortenRequest.model_validate(
        {"url": "https://example.com/custom", "custom_alias": custom_alias}
    )

    assert request.custom_alias == custom_alias


@pytest.mark.parametrize(
    "custom_alias",
    [
        "ab",
        "a" * 33,
        "-leading",
        "trailing-",
        "under_score",
        "UPPERCASE",
        "contains space",
        "docs",
        "redoc",
        "shorten",
        "analytics",
    ],
)
def test_shorten_request_rejects_invalid_or_reserved_aliases(
    custom_alias: str,
) -> None:
    """Malformed and API-owned path names should fail validation."""
    with pytest.raises(ValidationError):
        ShortenRequest.model_validate(
            {"url": "https://example.com/custom", "custom_alias": custom_alias}
        )


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
