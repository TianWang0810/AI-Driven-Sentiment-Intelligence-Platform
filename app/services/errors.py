"""
Custom exceptions for the pipeline.

All pipeline errors inherit from PipelineError and include:
    - code: Machine-readable error code
    - message: Human-readable error message
    - retryable: Whether the operation should be retried

Error codes:
    - CRAWL_FAILED: Failed to crawl source
    - CRAWL_EMPTY: No content found
    - OPENAI_ERROR: OpenAI API error
    - OPENAI_BAD_RESPONSE: Invalid response from OpenAI
    - LOCK_TIMEOUT: Job was locked too long
    - VALIDATION_ERROR: Input validation failed
    - UNKNOWN_ERROR: Unexpected error
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class PipelineError(Exception):
    """
    Base exception for all pipeline errors.
    
    Attributes:
        code: Machine-readable error code
        message: Human-readable error message
        retryable: Whether the operation should be retried
    """
    
    code: str
    message: str
    retryable: bool = True

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"

    def to_dict(self) -> dict:
        """Serialize error for API response or logging."""
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
        }


# -----------------------------------------------------------------------------
# Crawl Errors
# -----------------------------------------------------------------------------
class CrawlError(PipelineError):
    """Error during crawling stage."""
    
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(
            code="CRAWL_FAILED",
            message=message,
            retryable=retryable,
        )


class CrawlEmptyError(PipelineError):
    """No content found during crawling."""
    
    def __init__(self, source_url: str):
        super().__init__(
            code="CRAWL_EMPTY",
            message=f"No content found at {source_url}",
            retryable=False,  # Don't retry if source is empty
        )


# -----------------------------------------------------------------------------
# OpenAI Errors
# -----------------------------------------------------------------------------
class OpenAIError(PipelineError):
    """Error from OpenAI API."""
    
    def __init__(self, message: str, retryable: bool = True):
        super().__init__(
            code="OPENAI_ERROR",
            message=message,
            retryable=retryable,
        )


class OpenAIBadResponseError(PipelineError):
    """Invalid or malformed response from OpenAI."""
    
    def __init__(self, message: str):
        super().__init__(
            code="OPENAI_BAD_RESPONSE",
            message=message,
            retryable=True,  # Retry may get valid response
        )


# -----------------------------------------------------------------------------
# Worker Errors
# -----------------------------------------------------------------------------
class LockTimeoutError(PipelineError):
    """Job was locked too long (stuck worker)."""
    
    def __init__(self, job_id: int, locked_at: str):
        super().__init__(
            code="LOCK_TIMEOUT",
            message=f"Job {job_id} locked since {locked_at} exceeded timeout",
            retryable=True,
        )


# -----------------------------------------------------------------------------
# Validation Errors
# -----------------------------------------------------------------------------
class ValidationError(PipelineError):
    """Input validation failed."""
    
    def __init__(self, message: str):
        super().__init__(
            code="VALIDATION_ERROR",
            message=message,
            retryable=False,  # Don't retry validation errors
        )


# -----------------------------------------------------------------------------
# Generic Errors
# -----------------------------------------------------------------------------
class UnknownError(PipelineError):
    """Unexpected error wrapper."""
    
    def __init__(self, original_error: Exception):
        super().__init__(
            code="UNKNOWN_ERROR",
            message=str(original_error),
            retryable=True,
        )


__all__ = [
    "PipelineError",
    "CrawlError",
    "CrawlEmptyError",
    "OpenAIError",
    "OpenAIBadResponseError",
    "LockTimeoutError",
    "ValidationError",
    "UnknownError",
]