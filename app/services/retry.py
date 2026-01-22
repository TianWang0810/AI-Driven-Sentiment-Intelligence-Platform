"""
Retry utilities with exponential backoff.

Provides decorators and helpers for retrying operations that may fail
transiently (network errors, rate limits, etc.).
"""

import functools
from typing import Callable, Tuple, Type, TypeVar

from tenacity import (
    RetryError,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ..logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


def with_retry(
    max_attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 30.0,
    retry_on: Tuple[Type[Exception], ...] = (Exception,),
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator for retry with exponential backoff.
    
    Args:
        max_attempts: Maximum number of attempts
        min_wait: Minimum wait time between retries (seconds)
        max_wait: Maximum wait time between retries (seconds)
        retry_on: Tuple of exception types to retry on
        
    Returns:
        Decorated function with retry logic
        
    Example:
        @with_retry(max_attempts=3, retry_on=(ConnectionError,))
        def fetch_data():
            ...
    """
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @retry(
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
            retry=retry_if_exception_type(retry_on),
            reraise=True,
        )
        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> T:
            return func(*args, **kwargs)
        return wrapper
    return decorator


def retry_call(
    func: Callable[..., T],
    args: tuple = (),
    kwargs: dict = None,
    max_attempts: int = 3,
    min_wait: float = 1.0,
    max_wait: float = 30.0,
    retry_on: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Callable[[Exception, int], None] = None,
) -> T:
    """
    Execute a function with retry logic.
    
    Args:
        func: Function to execute
        args: Positional arguments
        kwargs: Keyword arguments
        max_attempts: Maximum number of attempts
        min_wait: Minimum wait time (seconds)
        max_wait: Maximum wait time (seconds)
        retry_on: Exception types to retry on
        on_retry: Callback called on each retry (exception, attempt)
        
    Returns:
        Function result
        
    Raises:
        The last exception if all retries fail
    """
    kwargs = kwargs or {}
    last_exception = None
    
    for attempt in range(1, max_attempts + 1):
        try:
            return func(*args, **kwargs)
        except retry_on as e:
            last_exception = e
            
            if attempt < max_attempts:
                if on_retry:
                    on_retry(e, attempt)
                    
                logger.warning(
                    "retry_attempt",
                    attempt=attempt,
                    max_attempts=max_attempts,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                
                # Wait with exponential backoff
                import time
                wait_time = min(min_wait * (2 ** (attempt - 1)), max_wait)
                time.sleep(wait_time)
            else:
                logger.error(
                    "all_retries_exhausted",
                    attempts=max_attempts,
                    error=str(e),
                    error_type=type(e).__name__,
                )
    
    raise last_exception


def calculate_backoff(attempt: int, base: float = 1.0, max_wait: float = 300.0) -> float:
    """
    Calculate exponential backoff wait time.
    
    Args:
        attempt: Current attempt number (1-based)
        base: Base wait time in seconds
        max_wait: Maximum wait time in seconds
        
    Returns:
        Wait time in seconds
    """
    wait = base * (2 ** (attempt - 1))
    return min(wait, max_wait)


__all__ = ["with_retry", "retry_call", "calculate_backoff"]