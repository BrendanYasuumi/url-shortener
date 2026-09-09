"""Create and configure the URL shortener's FastAPI application."""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
import os
from pathlib import Path
import sqlite3

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.database import (
    create_url_record,
    database_connection,
    find_url_by_short_code,
    initialize_database,
    record_click_and_get_url,
)
from app.schemas import (
    AnalyticsResponse,
    ErrorResponse,
    ShortenRequest,
    ShortenResponse,
)


def create_app(database_path: str | Path | None = None) -> FastAPI:
    """Build an application using the configured SQLite database.

    Accepting a database path makes the application's external dependency
    explicit. Production can use a durable file while every test injects its
    own temporary database.
    """
    configured_path = str(
        database_path or os.getenv("URL_SHORTENER_DB_PATH", "urls.db")
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Initialize the database before the application accepts requests."""
        initialize_database(configured_path)
        yield

    application = FastAPI(
        title="URL Shortener API",
        description="Create compact short links and track redirect analytics.",
        version="0.2.0",
        lifespan=lifespan,
    )

    def get_connection() -> Iterator[sqlite3.Connection]:
        """Provide one transactional database connection per request."""
        with database_connection(configured_path) as connection:
            yield connection

    @application.get("/")
    def read_root() -> dict[str, str]:
        """Return a small response proving that the API is running."""
        return {"message": "URL Shortener API is running"}

    @application.post(
        "/shorten",
        response_model=ShortenResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["URLs"],
        summary="Create a short URL",
    )
    def shorten_url(
        payload: ShortenRequest,
        request: Request,
        connection: sqlite3.Connection = Depends(get_connection),
    ) -> ShortenResponse:
        """Persist a validated destination and return its new short link."""
        row = create_url_record(connection, str(payload.url))
        return ShortenResponse(
            original_url=row["original_url"],
            short_code=row["short_code"],
            short_url=str(
                request.url_for(
                    "redirect_to_original_url",
                    short_code=row["short_code"],
                )
            ),
            created_at=row["created_at"],
        )

    @application.get(
        "/analytics/{short_code}",
        response_model=AnalyticsResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["Analytics"],
        summary="Read short URL analytics",
    )
    def get_analytics(
        short_code: str,
        connection: sqlite3.Connection = Depends(get_connection),
    ) -> AnalyticsResponse:
        """Return stored metadata and total clicks without changing them."""
        row = find_url_by_short_code(connection, short_code)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Short URL not found.",
            )

        return AnalyticsResponse(
            original_url=row["original_url"],
            short_code=row["short_code"],
            clicks=row["clicks"],
            created_at=row["created_at"],
        )

    @application.get(
        "/{short_code}",
        status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        response_class=RedirectResponse,
        responses={404: {"model": ErrorResponse}},
        tags=["URLs"],
        summary="Follow a short URL",
        name="redirect_to_original_url",
    )
    def redirect_to_original_url(
        short_code: str,
        connection: sqlite3.Connection = Depends(get_connection),
    ) -> RedirectResponse:
        """Count a visit and temporarily redirect to the original URL."""
        original_url = record_click_and_get_url(connection, short_code)
        if original_url is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Short URL not found.",
            )

        return RedirectResponse(
            url=original_url,
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
        )

    return application


# Uvicorn imports this module-level object when started with ``app.main:app``.
app = create_app()
