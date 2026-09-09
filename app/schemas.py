"""Pydantic models that define the API's input and output contracts."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ShortenRequest(BaseModel):
    """Validate the JSON body used to request a shortened URL."""

    # Reject unknown fields so misspelled input does not fail silently.
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl = Field(
        description="An absolute HTTP or HTTPS URL to shorten",
        examples=["https://example.com/articles/fastapi"],
    )


class ShortenResponse(BaseModel):
    """Describe the resource returned after a short URL is created."""

    original_url: HttpUrl
    short_code: str = Field(
        min_length=6,
        max_length=6,
        pattern=r"^[0-9a-zA-Z]{6}$",
    )
    short_url: HttpUrl
    created_at: datetime


class ErrorResponse(BaseModel):
    """Describe the standard error body returned by FastAPI."""

    detail: str
