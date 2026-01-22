"""
Database extensions and connection management.

Manages SQLAlchemy engine, session factory, and database initialization.
Uses connection pooling and scoped sessions for thread safety.
"""

from contextlib import contextmanager
from typing import Generator, Optional

from sqlalchemy import create_engine, text
from sqlalchemy.orm import (
    DeclarativeBase,
    Session,
    scoped_session,
    sessionmaker,
)

from .config import get_settings
from .logging_config import get_logger

logger = get_logger(__name__)


class Base(DeclarativeBase):
    """
    SQLAlchemy declarative base class.
    
    All model classes inherit from this base.
    """
    pass


# Global state (initialized lazily)
_engine: Optional[object] = None
_session_factory: Optional[scoped_session] = None


def get_engine():
    """
    Get or create the SQLAlchemy engine.
    
    The engine manages the connection pool to PostgreSQL.
    It is a singleton - created once and reused.
    
    Configuration:
        - pool_pre_ping: Test connections before use (handles reconnection)
        - pool_size: Number of persistent connections
        - max_overflow: Extra connections when pool is exhausted
    """
    global _engine

    if _engine is None:
        settings = get_settings()

        # Log without exposing password
        db_host = settings.database_url.split("@")[-1] if "@" in settings.database_url else "localhost"
        logger.info("creating_database_engine", database_host=db_host)

        _engine = create_engine(
            settings.database_url,
            pool_pre_ping=True,  # Verify connections before use
            pool_size=10,
            max_overflow=20,
            echo=False,  # Set to True to log SQL queries
        )

    return _engine


def get_session_factory() -> scoped_session:
    """
    Get or create the session factory.
    
    Uses scoped_session for thread safety in web applications.
    Each thread gets its own session instance.
    """
    global _session_factory

    if _session_factory is None:
        _session_factory = scoped_session(
            sessionmaker(
                bind=get_engine(),
                expire_on_commit=False,  # Don't expire objects after commit
                autoflush=True,
            )
        )

    return _session_factory


def get_db_session() -> Session:
    """
    Get a database session.
    
    IMPORTANT: Caller is responsible for closing the session!
    For most cases, use get_db_context() instead.
    """
    return get_session_factory()()


@contextmanager
def get_db_context() -> Generator[Session, None, None]:
    """
    Context manager for database sessions.
    
    Automatically handles commit, rollback, and cleanup.
    This is the RECOMMENDED way to use sessions.
    
    Example:
        with get_db_context() as session:
            job = Job(source_type="youtube", source_url="...")
            session.add(job)
            # Auto-commits on exit, rollbacks on exception
    """
    session = get_db_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def close_db_session(exception: Optional[Exception] = None) -> None:
    """
    Close and remove the current session.
    
    Called at the end of each Flask request via teardown.
    """
    session_factory = get_session_factory()
    session_factory.remove()


def init_db() -> None:
    """
    Initialize database by creating all tables.
    
    For development only. Use Alembic migrations in production.
    """
    # Import models to register them with Base
    from . import models  # noqa: F401

    logger.info("initializing_database")
    Base.metadata.create_all(get_engine())
    logger.info("database_initialized")


def check_db_connection() -> bool:
    """
    Check if database connection is working.
    
    Used for health checks.
    """
    try:
        with get_db_context() as session:
            session.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error("database_connection_failed", error=str(e))
        return False


__all__ = [
    "Base",
    "get_engine",
    "get_session_factory",
    "get_db_session",
    "get_db_context",
    "close_db_session",
    "init_db",
    "check_db_connection",
]