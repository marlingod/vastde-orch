"""structlog setup: pretty in TTY, JSON in CI/non-TTY."""

from __future__ import annotations

import logging
import sys

import structlog


def configure(level: str = "INFO") -> None:
    logging.basicConfig(format="%(message)s", level=level.upper(), stream=sys.stderr)

    processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if sys.stderr.isatty():
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    else:
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper())),
        cache_logger_on_first_use=True,
    )
