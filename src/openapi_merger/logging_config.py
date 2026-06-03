from __future__ import annotations

import logging
import os
import sys
from typing import IO

import structlog


def configure_logging(stream: IO[str] | None = None) -> None:
    """Configure structlog + stdlib logging.

    Env vars:
        LOG_LEVEL: DEBUG | INFO | WARNING | ERROR (default INFO)
        LOG_FORMAT: logfmt | json (default logfmt)
    """
    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    fmt = os.getenv("LOG_FORMAT", "logfmt").lower()
    if fmt == "json":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.processors.LogfmtRenderer(
            key_order=["timestamp", "level", "event", "request_id"],
            drop_missing=True,
        )

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=shared_processors + [renderer],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.PrintLoggerFactory(file=stream or sys.stdout),
        cache_logger_on_first_use=False,
    )

    # Route uvicorn / fastapi stdlib loggers through the same handler so they
    # also show up in structured form. Uvicorn's own access log stays on its
    # default formatter — we add our own request middleware in Task 4.
    logging.basicConfig(
        level=level,
        format="%(message)s",
        stream=stream or sys.stdout,
        force=True,
    )
