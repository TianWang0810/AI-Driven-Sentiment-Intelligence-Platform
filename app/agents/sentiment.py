"""
Sentiment Agent for analyzing content sentiment.

Processing flow:
    1. Language detection (using langdetect)
    2. Translation to English if needed (reserved, currently skipped)
    3. TextBlob polarity analysis
    4. OpenAI sentiment analysis with structured output
    5. Score fusion: final = TB_WEIGHT * textblob + LLM_WEIGHT * llm
    6. Label assignment: >0.1=positive, <-0.1=negative, else neutral

If OpenAI fails, graceful degradation uses TextBlob only.
"""

from dataclasses import dataclass
from typing import Optional

from langdetect import detect, LangDetectException
from textblob import TextBlob

from ..config import get_settings
from ..logging_config import Timer, bind_context, get_logger
from ..models import ActionRecommendation, Item, Job, SentimentLabel
from ..services.errors import OpenAIBadResponseError, OpenAIError
from ..services.openai_client import SentimentResponse, get_openai_client
from .crawler import CrawledItem

logger = get_logger(__name__)


@dataclass
class ScoredItem:
    """
    Item with sentiment analysis results.
    
    Contains all data needed to create/update an Item model.
    """
    
    # From CrawledItem
    external_id: str
    raw_text: str
    source_url: Optional[str]
    metadata: dict
    
    # Language
    lang: Optional[str]
    text_en: Optional[str]
    
    # Sentiment scores
    sentiment_textblob: float
    sentiment_llm: Optional[float]
    sentiment_final: float
    label: SentimentLabel
    confidence: Optional[float]
    
    # LLM analysis
    topics: Optional[list[str]]
    action_recommendation: Optional[ActionRecommendation]
    rationale: Optional[str]
    
    # Flags
    llm_failed: bool = False


class SentimentAgent:
    """
    Agent for sentiment analysis using TextBlob + OpenAI.
    
    Fusion formula:
        sentiment_final = TB_WEIGHT * textblob + LLM_WEIGHT * llm
    
    Label thresholds (configurable):
        - score > 0.1 -> positive
        - score < -0.1 -> negative
        - else -> neutral
    
    Graceful degradation:
        If OpenAI fails, uses TextBlob only with warning logged.
    """
    
    def __init__(self, use_llm: bool = True):
        """
        Initialize sentiment agent.
        
        Args:
            use_llm: Whether to use OpenAI (can disable for testing/cost)
        """
        self.use_llm = use_llm
        self.settings = get_settings()
        self._openai_client = None
    
    @property
    def openai_client(self):
        """Lazy load OpenAI client."""
        if self._openai_client is None and self.use_llm:
            self._openai_client = get_openai_client()
        return self._openai_client
    
    def _detect_language(self, text: str) -> Optional[str]:
        """
        Detect language of text.
        
        Returns ISO 639-1 language code or None.
        """
        try:
            return detect(text)
        except LangDetectException:
            return None
    
    def _analyze_textblob(self, text: str) -> float:
        """
        Analyze sentiment using TextBlob.
        
        Returns polarity score from -1 to 1.
        """
        blob = TextBlob(text)
        return blob.sentiment.polarity
    
    def _analyze_llm(self, text: str) -> Optional[SentimentResponse]:
        """
        Analyze sentiment using OpenAI.
        
        Returns SentimentResponse or None if disabled/failed.
        """
        if not self.use_llm or not self.settings.has_openai_key:
            return None
        
        try:
            return self.openai_client.analyze_sentiment(text)
        except (OpenAIError, OpenAIBadResponseError) as e:
            logger.warning(
                "llm_analysis_failed_graceful_degradation",
                error=str(e),
                error_code=e.code if hasattr(e, "code") else "UNKNOWN",
            )
            return None
    
    def _calculate_label(self, score: float) -> SentimentLabel:
        """Determine sentiment label from score."""
        if score > self.settings.sentiment_positive_threshold:
            return SentimentLabel.POSITIVE
        elif score < self.settings.sentiment_negative_threshold:
            return SentimentLabel.NEGATIVE
        return SentimentLabel.NEUTRAL
    
    def _map_action_recommendation(self, action: str) -> Optional[ActionRecommendation]:
        """Map string action to enum."""
        mapping = {
            "ignore": ActionRecommendation.IGNORE,
            "monitor": ActionRecommendation.MONITOR,
            "escalate": ActionRecommendation.ESCALATE,
        }
        return mapping.get(action)
    
    def analyze_item(self, crawled_item: CrawledItem) -> ScoredItem:
        """
        Analyze sentiment of a single item.
        
        Args:
            crawled_item: Item from crawler
            
        Returns:
            ScoredItem with all sentiment data
        """
        text = crawled_item.raw_text
        
        # 1. Language detection
        lang = self._detect_language(text)
        
        # 2. Use English text for analysis (translation reserved for future)
        text_en = text if lang == "en" else None
        analysis_text = text  # Use original for now
        
        # 3. TextBlob analysis (always runs, fast and free)
        sentiment_textblob = self._analyze_textblob(analysis_text)
        
        # 4. LLM analysis (may fail gracefully)
        llm_result = self._analyze_llm(analysis_text)
        llm_failed = llm_result is None and self.use_llm
        
        # 5. Calculate final score
        if llm_result:
            # Weighted fusion
            sentiment_final = (
                self.settings.sentiment_tb_weight * sentiment_textblob +
                self.settings.sentiment_llm_weight * llm_result.sentiment_score
            )
            sentiment_llm = llm_result.sentiment_score
            confidence = llm_result.confidence
            topics = llm_result.topics
            action_recommendation = self._map_action_recommendation(llm_result.action_recommendation)
            rationale = llm_result.rationale
        else:
            # TextBlob only (degraded mode)
            sentiment_final = sentiment_textblob
            sentiment_llm = None
            confidence = None
            topics = None
            action_recommendation = None
            rationale = None
        
        # Clamp to [-1, 1]
        sentiment_final = max(-1.0, min(1.0, sentiment_final))
        
        # 6. Determine label
        label = self._calculate_label(sentiment_final)
        
        return ScoredItem(
            external_id=crawled_item.external_id,
            raw_text=crawled_item.raw_text,
            source_url=crawled_item.source_url,
            metadata=crawled_item.metadata,
            lang=lang,
            text_en=text_en,
            sentiment_textblob=sentiment_textblob,
            sentiment_llm=sentiment_llm,
            sentiment_final=sentiment_final,
            label=label,
            confidence=confidence,
            topics=topics,
            action_recommendation=action_recommendation,
            rationale=rationale,
            llm_failed=llm_failed,
        )
    
    def run(self, job: Job, crawled_items: list[CrawledItem]) -> list[ScoredItem]:
        """
        Run sentiment analysis on all items.
        
        Args:
            job: Job model (for logging context)
            crawled_items: List of items from crawler
            
        Returns:
            List of ScoredItem with sentiment data
        """
        bind_context(job_id=job.id, stage="sentiment")
        
        logger.info(
            "sentiment_agent_started",
            items_count=len(crawled_items),
            use_llm=self.use_llm,
        )
        
        scored_items = []
        llm_failures = 0
        
        with Timer() as timer:
            for i, crawled_item in enumerate(crawled_items):
                try:
                    scored = self.analyze_item(crawled_item)
                    scored_items.append(scored)
                    
                    if scored.llm_failed:
                        llm_failures += 1
                    
                except Exception as e:
                    # Log but continue with other items
                    logger.error(
                        "item_analysis_failed",
                        item_index=i,
                        external_id=crawled_item.external_id,
                        error=str(e),
                    )
        
        logger.info(
            "sentiment_agent_complete",
            items_analyzed=len(scored_items),
            llm_failures=llm_failures,
            latency_ms=timer.elapsed_ms,
        )
        
        return scored_items


__all__ = ["SentimentAgent", "ScoredItem"]