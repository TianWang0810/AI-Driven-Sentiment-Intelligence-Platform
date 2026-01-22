"""
Job model representing a sentiment analysis task.

Job Lifecycle:
    1. Created via POST /v1/jobs -> status='queued', attempts=0
    2. Worker claims job -> status='running', locked_by=worker_id, attempts+=1
    3. Processing succeeds -> status='succeeded', progress=items_count
    4. Processing fails -> retry (status='queued') or status='failed'

Concurrency:
    - Uses FOR UPDATE SKIP LOCKED for safe job claiming
    - locked_at/locked_by track ownership
    - Watchdog releases stuck jobs (locked_at < now - LOCK_TIMEOUT)
"""

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, String, Index, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import Base

if TYPE_CHECKING:
    from .alert import Alert
    from .item import Item


class JobStatus(str, enum.Enum):
    """Job status enumeration."""
    
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Job(Base):
    """
    Sentiment analysis job model.
    
    Attributes:
        id: Primary key
        source_type: Source type (youtube, webpage, rss)
        source_url: URL to crawl
        options: JSON options (max_items, sort, etc.)
        status: Current job status (stored as lowercase string)
        attempts: Number of processing attempts
        max_attempts: Maximum retry attempts (default 3)
        progress: Number of items processed
        locked_at: When job was locked by worker
        locked_by: Worker ID that owns this job
        error_code: Error code if failed
        error_message: Detailed error message
        stats: Aggregated statistics after completion
        created_at: Creation timestamp (UTC)
        updated_at: Last update timestamp (UTC)
        started_at: Processing start timestamp
        completed_at: Processing completion timestamp
    """

    __tablename__ = "jobs"

    # Primary key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Source configuration
    source_type: Mapped[str] = mapped_column(
        String(50), nullable=False, index=True,
        comment="Source type: youtube, webpage, rss"
    )
    source_url: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="URL to crawl"
    )
    options: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=None,
        comment="Options: {max_items, sort, ...}"
    )

    # Status stored as String (lowercase) instead of Enum to avoid case issues
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=JobStatus.QUEUED.value, index=True
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Number of processing attempts"
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=3,
        comment="Maximum retry attempts"
    )
    progress: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0,
        comment="Number of items processed"
    )

    # Worker locking (for concurrent workers)
    locked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
        comment="When job was locked"
    )
    locked_by: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, default=None,
        comment="Worker ID that owns this job"
    )

    # Error tracking
    error_code: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, default=None,
        comment="Error code (CRAWL_FAILED, OPENAI_ERROR, etc.)"
    )
    error_message: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None,
        comment="Detailed error message"
    )

    # Results
    stats: Mapped[Optional[dict]] = mapped_column(
        JSONB, nullable=True, default=None,
        comment="Aggregated statistics after completion"
    )

    # Timestamps (all UTC)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc)
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    # Relationships
    items: Mapped[list["Item"]] = relationship(
        "Item", back_populates="job",
        cascade="all, delete-orphan", lazy="selectin"
    )
    alerts: Mapped[list["Alert"]] = relationship(
        "Alert", back_populates="job",
        cascade="all, delete-orphan", lazy="selectin"
    )

    # Indexes for worker queries
    __table_args__ = (
        Index("ix_jobs_queue", "status", "created_at"),
        Index("ix_jobs_watchdog", "status", "locked_at"),
    )

    def __repr__(self) -> str:
        return f"<Job {self.id} [{self.status}] {self.source_type}>"

    def to_dict(self, include_stats: bool = True) -> dict:
        """Serialize job for API response."""
        result = {
            "id": self.id,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "options": self.options,
            "status": self.status,
            "attempts": self.attempts,
            "max_attempts": self.max_attempts,
            "progress": self.progress,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
        if include_stats:
            result["stats"] = self.stats
        return result

    @property
    def is_terminal(self) -> bool:
        """Check if job is in terminal state."""
        return self.status in (JobStatus.SUCCEEDED.value, JobStatus.FAILED.value)

    @property
    def can_retry(self) -> bool:
        """Check if job can be retried."""
        return self.attempts < self.max_attempts


__all__ = ["Job", "JobStatus"]