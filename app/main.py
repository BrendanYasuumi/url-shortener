"""Create the FastAPI application and define its first endpoint."""

from fastapi import FastAPI


# ``app`` is the Python object that Uvicorn will run as a web application.
app = FastAPI(
    title="URL Shortener API",
    description="Create compact short links and track redirect analytics.",
    version="0.1.0",
)


@app.get("/")
def read_root() -> dict[str, str]:
    """Return a small response proving that the API is running."""
    return {"message": "URL Shortener API is running"}
