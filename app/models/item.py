"""
Item model representing crawled content with sentiment analysis results.

Each item belongs to a job and contains:
    1. Raw content from crawler
    2. Language detection and English translation
    3. Sentiment scores (TextBlob + LLM + fused)
    4. Topics, action recommendation, and rationale

Idempotency:
    - UNIQUE(job_id, external_id) prevents duplicate inserts
    - Use ON CONFLICT DO NOTHING for safe re-processing
"""

import enum
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    DateTime, Enum, Float, ForeignKey,
    Index, Integer, String, Text, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..extensions import Base

if TYPE_CHECKING:
    from .job import Job


class SentimentLabel(str, enum.Enum):
    """Sentiment classification label."""
    
    POSITIVE = "positive"  # score > 0.1
    NEUTRAL = "neutral"    # -0.1 <= score <= 0.1
    NEGATIVE = "negative"  # score < -0.1


class ActionRecommendation(str, enum.Enum):
    """LLM-recommended action based on content."""
    
    IGNORE = "ignore"      # No action needed
    MONITOR = "monitor"    # Should be monitored
    ESCALATE = "escalate"  # Requires immediate attention


class Item(Base):
    """
    Crawled content item with sentiment analysis results.
    
    Attributes:
        id: Primary key
        job_id: Foreign key to jobs table
        external_id: Original ID from source (for deduplication)
        raw_text: Original text as crawled
        lang: Detected language code (en, zh, es, etc.)
        text_en: English translation (if translated)
        
        sentiment_textblob: TextBlob polarity score (-1 to 1)
        sentiment_llm: OpenAI sentiment score (-1 to 1)
        sentiment_final: Fused final score (-1 to 1)
        label: Final sentiment label
        confidence: LLM confidence (0 to 1)
        
        topics: Identified topics (max 5)
        action_recommendation: Suggested action
        rationale: LLM reasoning (1-2 sentences)
        
        metadata: Additional source data (likes, replies, etc.)
        created_at: Creation timestamp (UTC)
    """

    __tablename__ = "items"

    # Primary key and foreign key
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False, index=True
    )

    # External ID for deduplication (e.g., YouTube comment ID)
    external_id: Mapped[str] = mapped_column(
        String(255), nullable=False,
        comment="Original ID from source"
    )

    # Content
    raw_text: Mapped[str] = mapped_column(
        Text, nullable=False,
        comment="Original text as crawled"
    )
    lang: Mapped[Optional[str]] = mapped_column(
        String(10), nullable=True, default=None,
        comment="Detected language code"
    )
    text_en: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None,
        comment="English translation"
    )

    # Sentiment scores
    sentiment_textblob: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None,
        comment="TextBlob polarity (-1 to 1)"
    )
    sentiment_llm: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None,
        comment="OpenAI sentiment score (-1 to 1)"
    )
    sentiment_final: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None, index=True,
        comment="Fused final score (-1 to 1)"
    )
    label: Mapped[Optional[SentimentLabel]] = mapped_column(
        Enum(SentimentLabel, name="sentiment_label", native_enum=False),
        nullable=True, default=None, index=True
    )
    confidence: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True, default=None,
        comment="LLM confidence (0 to 1)"
    )

    # LLM analysis results
    topics: Mapped[Optional[list]] = mapped_column(
        JSONB, nullable=True, default=None,
        comment="Identified topics (max 5)"
    )
    action_recommendation: Mapped[Optional[ActionRecommendation]] = mapped_column(
        Enum(ActionRecommendation, name="action_recommendation", native_enum=False),
        nullable=True, default=None
    )
    rationale: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, default=None,
        comment="LLM reasoning (1-2 sentences)"
    )

    # Metadata from source
    item_metadata: Mapped[Optional[dict]] = mapped_column(
        "metadata",
        JSONB, nullable=True, default=None,
        comment="Source metadata (likes, author, etc.)"
    )

    # Timestamp (UTC)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc)
    )

    # Relationship
    job: Mapped["Job"] = relationship("Job", back_populates="items")

    # Constraints and indexes
    __table_args__ = (
        # Idempotency constraint: same external_id cannot be inserted twice per job
        UniqueConstraint("job_id", "external_id", name="uq_items_job_external"),
        Index("ix_items_job_label", "job_id", "label"),
    )

    def __repr__(self) -> str:
        label_str = self.label.value if self.label else "unanalyzed"
        preview = self.raw_text[:30] + "..." if len(self.raw_text) > 30 else self.raw_text
        return f"<Item {self.id} [{label_str}] '{preview}'>"

    def to_dict(self, include_raw_text: bool = True) -> dict:
        """Serialize item for API response."""
        result = {
            "id": self.id,
            "job_id": self.job_id,
            "external_id": self.external_id,
            "lang": self.lang,
            "sentiment_textblob": self.sentiment_textblob,
            "sentiment_llm": self.sentiment_llm,
            "sentiment_final": self.sentiment_final,
            "label": self.label.value if self.label else None,
            "confidence": self.confidence,
            "topics": self.topics,
            "action_recommendation": (
                self.action_recommendation.value if self.action_recommendation else None
            ),
            "rationale": self.rationale,
            "metadata": self.item_metadata,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
        if include_raw_text:
            result["raw_text"] = self.raw_text
            result["text_en"] = self.text_en
        return result


__all__ = ["Item", "SentimentLabel", "ActionRecommendation"]