"""
Structured logging configuration using structlog.

Provides JSON-formatted logs for production (machine-readable) and
colorized console logs for development (human-readable).

All logs include: timestamp, level, event, and context fields
(job_id, worker_id, stage, attempt, latency_ms, error_code, request_id).
"""

import logging
import sys
import time
from contextvars import ContextVar
from typing import Any, Optional
from uuid import uuid4

import structlog
from structlog.typing import Processor

from .config import get_settings

# Context variable for request_id (used across async boundaries)
request_id_var: ContextVar[str] = ContextVar("request_id", default="")


def generate_request_id() -> str:
    """Generate a unique request ID."""
    return str(uuid4())[:8]


def add_request_id(
    logger: logging.Logger, method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Add request_id to log entries if available."""
    req_id = request_id_var.get()
    if req_id:
        event_dict["request_id"] = req_id
    return event_dict


def setup_logging(
    log_level: Optional[str] = None,
    log_format: Optional[str] = None,
) -> None:
    """
    Configure structured logging for the application.
    
    Should be called once at application startup.
    
    Args:
        log_level: Override log level from settings
        log_format: Override log format ('json' or 'console')
    """
    settings = get_settings()
    level = log_level or settings.log_level
    fmt = log_format or settings.log_format

    # Choose renderer based on format
    if fmt == "json":
        # JSON for production - machine readable, good for log aggregation
        renderer: Processor = structlog.processors.JSONRenderer()
    else:
        # Console for development - human readable with colors
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    # Shared processors that transform log entries
    shared_processors: list[Processor] = [
        # Merge context variables (job_id, worker_id, etc.)
        structlog.contextvars.merge_contextvars,
        # Add custom request_id
        add_request_id,
        # Add log level
        structlog.processors.add_log_level,
        # Add ISO format timestamp (UTC)
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        # Add logger name
        structlog.stdlib.add_logger_name,
        # Format positional arguments
        structlog.stdlib.PositionalArgumentsFormatter(),
        # Process exceptions
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        # Ensure JSON serializable
        structlog.processors.UnicodeDecoder(),
    ]

    # Configure structlog
    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # Configure stdlib logging formatter
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    # Setup handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # Quiet noisy third-party loggers
    for logger_name in ["urllib3", "httpx", "httpcore", "openai"]:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    Get a named logger instance.
    
    Args:
        name: Logger name, typically __name__
        
    Returns:
        A structlog logger instance
        
    Example:
        logger = get_logger(__name__)
        logger.info("job_started", job_id=123, stage="crawl")
    """
    return structlog.get_logger(name)


def bind_context(**kwargs: Any) -> None:
    """
    Bind context variables to all subsequent log entries.
    
    Useful for adding job_id, worker_id, etc. that should appear
    in all logs for a specific operation.
    
    Args:
        **kwargs: Key-value pairs to bind (e.g., job_id=123)
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """Clear all bound context variables."""
    structlog.contextvars.clear_contextvars()


class Timer:
    """
    Context manager for timing operations and logging latency.
    
    Example:
        with Timer() as t:
            do_something()
        logger.info("operation_complete", latency_ms=t.elapsed_ms)
    """

    def __init__(self):
        self.start_time: float = 0
        self.end_time: float = 0

    def __enter__(self) -> "Timer":
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, *args) -> None:
        self.end_time = time.perf_counter()

    @property
    def elapsed_ms(self) -> int:
        """Get elapsed time in milliseconds."""
        return int((self.end_time - self.start_time) * 1000)


__all__ = [
    "setup_logging",
    "get_logger",
    "bind_context",
    "clear_context",
    "request_id_var",
    "generate_request_id",
    "Timer",
]