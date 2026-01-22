"""
Pipeline orchestrator for job processing.

Coordinates the three agents in sequence:
    1. CrawlerAgent.run(job) -> List[CrawledItem]
    2. SentimentAgent.run(job, items) -> List[ScoredItem]
    3. ActionAgent.run(job, scored_items) -> List[Alert]

Handles:
    - Database persistence with idempotency (ON CONFLICT DO NOTHING)
    - Job status updates
    - Error handling and conversion to PipelineError
    - Statistics calculation
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from ..agents import ActionAgent, CrawlerAgent, SentimentAgent, ScoredItem
from ..config import get_settings
from ..extensions import get_db_context
from ..logging_config import Timer, bind_context, clear_context, get_logger
from ..models import ActionRecommendation, Item, Job, JobStatus, SentimentLabel
from .errors import PipelineError, UnknownError

logger = get_logger(__name__)


class Pipeline:
    """
    Main processing pipeline that orchestrates all agents.
    
    Flow:
        1. Crawl content from source
        2. Analyze sentiment (TextBlob + LLM)
        3. Evaluate and trigger alerts
        4. Persist results with idempotency
        5. Update job statistics
    """
    
    def __init__(self, use_llm: bool = True):
        """
        Initialize pipeline with agents.
        
        Args:
            use_llm: Whether to use OpenAI for sentiment (can disable for testing)
        """
        self.crawler = CrawlerAgent()
        self.sentiment = SentimentAgent(use_llm=use_llm)
        self.action = ActionAgent()
        self.settings = get_settings()
    
    def _persist_items(self, job_id: int, scored_items: list[ScoredItem]) -> int:
        """
        Persist scored items to database with idempotency.
        
        Uses ON CONFLICT DO NOTHING to handle duplicates safely.
        
        Args:
            job_id: Job ID
            scored_items: List of items with sentiment data
            
        Returns:
            Number of items actually inserted
        """
        if not scored_items:
            return 0
        
        with get_db_context() as session:
            # Build insert statement with ON CONFLICT DO NOTHING
            values = []
            for item in scored_items:
                values.append({
                    "job_id": job_id,
                    "external_id": item.external_id,
                    "raw_text": item.raw_text,
                    "lang": item.lang,
                    "text_en": item.text_en,
                    "sentiment_textblob": item.sentiment_textblob,
                    "sentiment_llm": item.sentiment_llm,
                    "sentiment_final": item.sentiment_final,
                    "label": item.label.value if item.label else None,
                    "confidence": item.confidence,
                    "topics": item.topics,
                    "action_recommendation": (
                        item.action_recommendation.value if item.action_recommendation else None
                    ),
                    "rationale": item.rationale,
                    "metadata": item.metadata,
                })
            
            # Use PostgreSQL INSERT ... ON CONFLICT DO NOTHING
            stmt = insert(Item).values(values)
            stmt = stmt.on_conflict_do_nothing(
                index_elements=["job_id", "external_id"]
            )
            
            result = session.execute(stmt)
            inserted = result.rowcount
            
            logger.info(
                "items_persisted",
                total=len(scored_items),
                inserted=inserted,
                duplicates_skipped=len(scored_items) - inserted,
            )
            
            return inserted
    
    def _calculate_job_stats(self, scored_items: list[ScoredItem]) -> dict:
        """Calculate job statistics from scored items."""
        if not scored_items:
            return {}
        
        total = len(scored_items)
        scores = [i.sentiment_final for i in scored_items if i.sentiment_final is not None]
        
        label_counts = {
            "positive": sum(1 for i in scored_items if i.label == SentimentLabel.POSITIVE),
            "neutral": sum(1 for i in scored_items if i.label == SentimentLabel.NEUTRAL),
            "negative": sum(1 for i in scored_items if i.label == SentimentLabel.NEGATIVE),
        }
        
        # Collect all topics
        all_topics = []
        for item in scored_items:
            if item.topics:
                all_topics.extend(item.topics)
        
        # Count topics
        from collections import Counter
        topic_counts = Counter(all_topics)
        top_topics = [{"topic": t, "count": c} for t, c in topic_counts.most_common(10)]
        
        return {
            "total_items": total,
            "label_counts": label_counts,
            "negative_ratio": label_counts["negative"] / total if total > 0 else 0,
            "score_stats": {
                "mean": sum(scores) / len(scores) if scores else 0,
                "min": min(scores) if scores else 0,
                "max": max(scores) if scores else 0,
            },
            "top_topics": top_topics,
        }
    
    def process(self, job: Job) -> None:
        """
        Process a job through the full pipeline.
        
        This is the main entry point called by the worker.
        
        Args:
            job: Job model to process
            
        Raises:
            PipelineError: On any pipeline stage failure
        """
        bind_context(job_id=job.id)
        
        logger.info(
            "pipeline_started",
            source_type=job.source_type,
            source_url=job.source_url,
        )
        
        try:
            with Timer() as total_timer:
                # Stage 1: Crawl
                with Timer() as crawl_timer:
                    crawled_items = self.crawler.run(job)
                
                logger.info(
                    "stage_complete",
                    stage="crawl",
                    items=len(crawled_items),
                    latency_ms=crawl_timer.elapsed_ms,
                )
                
                # Stage 2: Sentiment Analysis
                with Timer() as sentiment_timer:
                    scored_items = self.sentiment.run(job, crawled_items)
                
                logger.info(
                    "stage_complete",
                    stage="sentiment",
                    items=len(scored_items),
                    latency_ms=sentiment_timer.elapsed_ms,
                )
                
                # Stage 3: Persist Items (idempotent)
                with Timer() as persist_timer:
                    inserted_count = self._persist_items(job.id, scored_items)
                
                logger.info(
                    "stage_complete",
                    stage="persist",
                    inserted=inserted_count,
                    latency_ms=persist_timer.elapsed_ms,
                )
                
                # Stage 4: Actions (alerts)
                with Timer() as action_timer:
                    alerts = self.action.run(job, scored_items)
                
                logger.info(
                    "stage_complete",
                    stage="action",
                    alerts=len(alerts),
                    latency_ms=action_timer.elapsed_ms,
                )
                
                # Calculate and store statistics
                stats = self._calculate_job_stats(scored_items)
                stats["alerts_triggered"] = len(alerts)
                
                # Update job as succeeded
                with get_db_context() as session:
                    db_job = session.query(Job).filter(Job.id == job.id).first()
                    if db_job:
                        db_job.status = JobStatus.SUCCEEDED
                        db_job.progress = len(scored_items)
                        db_job.stats = stats
                        db_job.completed_at = datetime.now(timezone.utc)
                        db_job.error_code = None
                        db_job.error_message = None
                
                logger.info(
                    "pipeline_complete",
                    total_items=len(scored_items),
                    alerts=len(alerts),
                    latency_ms=total_timer.elapsed_ms,
                )
                
        except PipelineError:
            # Re-raise pipeline errors as-is
            raise
        except Exception as e:
            # Wrap unknown errors
            logger.error("pipeline_unexpected_error", error=str(e), error_type=type(e).__name__)
            raise UnknownError(e)
        finally:
            clear_context()


def run_pipeline(job: Job, use_llm: bool = True) -> None:
    """
    Convenience function to run the pipeline.
    
    Args:
        job: Job to process
        use_llm: Whether to use OpenAI
    """
    pipeline = Pipeline(use_llm=use_llm)
    pipeline.process(job)


__all__ = ["Pipeline", "run_pipeline"]