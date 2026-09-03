"""Pydantic models that define the API's input and output contracts."""

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class ShortenRequest(BaseModel):
    """Validate the JSON body used to request a shortened URL."""

    # Reject unknown fields so misspelled input does not fail silently.
    model_config = ConfigDict(extra="forbid")

    url: HttpUrl = Field(
        description="An absolute HTTP or HTTPS URL to shorten",
        examples=["https://example.com/articles/fastapi"],
    )
