"""Central logging configuration.

Keeps a single, predictable log format for the API server, the agent runtime
and the CLI so agent execution traces line up with API request logs.
"""

from __future__ import annotations

import logging
import sys

from app.core.config import settings

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

_configured = False


def configure_logging(level: int | None = None) -> None:
    """Install a stream handler on the root logger (idempotent)."""
    global _configured
    if _configured:
        return

    resolved_level = level or (logging.DEBUG if settings.debug else logging.INFO)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT))

    root = logging.getLogger()
    root.setLevel(resolved_level)
    root.handlers = [handler]

    # Third-party libraries are noisy at DEBUG.
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    configure_logging()
    return logging.getLogger(name)
