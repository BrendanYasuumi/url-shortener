"""Structured application logging configuration.

The Uvicorn server owns its startup and access logs. This module configures a
separate application logger for machine-readable request and error events.
"""

from datetime import UTC, datetime
import json
import logging
import os
import sys
from typing import Final


LOGGER_NAME: Final = "url_shortener"
DEFAULT_LOG_LEVEL: Final = "INFO"

# Only known fields are copied from LogRecord objects. A fixed allowlist keeps
# framework internals out of the public log format and makes downstream parsing
# predictable.
CONTEXT_FIELDS: Final = (
    "event",
    "request_id",
    "method",
    "path",
    "status_code",
    "duration_ms",
    "error_type",
)


class JsonFormatter(logging.Formatter):
    """Serialize one logging record as one compact JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        """Return a stable JSON representation of a log record."""
        timestamp = datetime.fromtimestamp(record.created, tz=UTC).isoformat(
            timespec="milliseconds"
        )
        payload: dict[str, object] = {
            "timestamp": timestamp.replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for field_name in CONTEXT_FIELDS:
            if hasattr(record, field_name):
                payload[field_name] = getattr(record, field_name)

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _resolve_log_level(configured_level: str | None) -> int:
    """Convert a configured level name to logging's numeric representation."""
    level_name = (configured_level or DEFAULT_LOG_LEVEL).upper()
    numeric_level = getattr(logging, level_name, None)
    if not isinstance(numeric_level, int):
        return logging.INFO
    return numeric_level


def configure_application_logger() -> logging.Logger:
    """Create the process-wide JSON application logger exactly once."""
    application_logger = logging.getLogger(LOGGER_NAME)
    application_logger.setLevel(
        _resolve_log_level(os.getenv("URL_SHORTENER_LOG_LEVEL"))
    )
    application_logger.propagate = False

    # Application modules import this function from multiple places. Naming
    # the handler prevents duplicate lines when imports or tests create apps.
    if not any(
        handler.get_name() == "url_shortener_json"
        for handler in application_logger.handlers
    ):
        handler = logging.StreamHandler(sys.stdout)
        handler.set_name("url_shortener_json")
        handler.setFormatter(JsonFormatter())
        application_logger.addHandler(handler)

    return application_logger


logger = configure_application_logger()
