"""Pydantic models that define the API's input and output contracts."""

from datetime import datetime
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator


CUSTOM_ALIAS_PATTERN: Final = r"^[a-z0-9](?:[a-z0-9-]{1,30}[a-z0-9])$"
SHORT_CODE_PATTERN: Final = (
    r"^(?:[0-9a-zA-Z]{6}|[a-z0-9](?:[a-z0-9-]{1,30}[a-z0-9]))$"
)
RESERVED_ALIASES: Final = frozenset({"analytics", "docs", "redoc", "shorten"})


class ShortenRequest(BaseModel):
    """Validate the JSON body used to request a shortened URL."""

    # Reject unknown fields so misspelled input does not fail silently.
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl = Field(
        description="An absolute HTTP or HTTPS URL to shorten",
        examples=["https://example.com/articles/fastapi"],
    )
    custom_alias: str | None = Field(
        default=None,
        min_length=3,
        max_length=32,
        pattern=CUSTOM_ALIAS_PATTERN,
        description=(
            "Optional lowercase alias containing letters, digits, or internal "
            "hyphens"
        ),
        examples=["portfolio-2026"],
    )

    @field_validator("custom_alias")
    @classmethod
    def reject_reserved_alias(cls, value: str | None) -> str | None:
        """Prevent aliases from occupying paths owned by the API."""
        if value in RESERVED_ALIASES:
            raise ValueError("custom alias is reserved by the API")
        return value


class ShortenResponse(BaseModel):
    """Describe the resource returned after a short URL is created."""

    original_url: HttpUrl
    short_code: str = Field(
        min_length=3,
        max_length=32,
        pattern=SHORT_CODE_PATTERN,
    )
    short_url: HttpUrl
    created_at: datetime


class AnalyticsResponse(BaseModel):
    """Describe stored metadata and usage for one short URL."""

    original_url: HttpUrl
    short_code: str = Field(
        min_length=3,
        max_length=32,
        pattern=SHORT_CODE_PATTERN,
    )
    clicks: int = Field(ge=0, description="Total successful redirects")
    created_at: datetime


class ErrorResponse(BaseModel):
    """Describe the standard error body returned by FastAPI."""

    detail: str
