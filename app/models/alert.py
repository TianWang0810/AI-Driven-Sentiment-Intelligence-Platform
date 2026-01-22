"""
Alert model for triggered alerts from ActionAgent.

Alerts are created when sentiment analysis results meet certain conditions:
    - negative_spike: Negative ratio exceeds threshold
    - risk_keyword: Risk keywords detected in content

The payload field contains detailed trigger information.
"""

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Enum, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import Base

if TYPE_CHECKING:
    from .job import Job


class AlertType(str, enum.Enum):
    """Alert type enumeration."""
    
    NEGATIVE_SPIKE = "negative_spike"    # Negative ratio > threshold
    RISK_KEYWORD = "risk_keyword"        # Risk keywords detected
    SENTIMENT_SPIKE = "sentiment_spike"  # Sudden sentiment change (reserved)
    VOLUME_SPIKE = "volume_spike"        # Unusual volume (reserved)
    CUSTOM = "custom"                    # Custom alerts


class Alert(Base):
    """
    Alert model for storing triggered alerts.
    
    Attributes:
        id: Primary key
        job_id: Foreign key to jobs table
        type: Alert type (negative_spike, risk_keyword, etc.)
        payload: Detailed alert information as JSON
        created_at: Creation timestamp (UTC)
    
    Payload structure for NEGATIVE_SPIKE:
        {
            "ratio": 0.45,
            "threshold": 0.30,
            "total_items": 100,
            "negative_items": 45,
            "top_negative_topics": ["price", "quality"],
            "recommendation": "escalate"
        }
    """

    __tablename__ = "alerts"

    # Primary key and foreign key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    # Alert information
    type: Mapped[AlertType] = mapped_column(
        Enum(AlertType, name="alert_type", native_enum=False),
        nullable=False, index=True
    )
    payload: Mapped[dict] = mapped_column(
        JSONB, nullable=False,
        comment="Alert details as JSON"
    )

    # Timestamp (UTC)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    # Relationship
    job: Mapped["Job"] = relationship("Job", back_populates="alerts")

    def __repr__(self) -> str:
        return f"<Alert {self.id} [{self.type.value}] job={self.job_id}>"

    def to_dict(self) -> dict:
        """Serialize alert for API response."""
        return {
            "id": self.id,
            "job_id": self.job_id,
            "type": self.type.value,
            "payload": self.payload,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


__all__ = ["Alert", "AlertType"]