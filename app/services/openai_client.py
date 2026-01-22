"""
OpenAI client for sentiment analysis with structured output.

Uses OpenAI's structured output (response_format) to ensure consistent
JSON responses that match our expected schema.

Expected output schema:
{
    "sentiment_score": float (-1 to 1),
    "confidence": float (0 to 1),
    "topics": list[str] (max 5),
    "action_recommendation": "ignore" | "monitor" | "escalate",
    "rationale": str (1-2 sentences)
}
"""

import json
from typing import Optional

from openai import OpenAI
from pydantic import BaseModel, Field, field_validator

from ..config import get_settings
from ..logging_config import Timer, get_logger
from .errors import OpenAIBadResponseError, OpenAIError
from .retry import with_retry

logger = get_logger(__name__)


class SentimentResponse(BaseModel):
    """
    Pydantic model for OpenAI sentiment analysis response.
    
    Used for validation of the structured output from OpenAI.
    """
    
    sentiment_score: float = Field(
        ..., ge=-1, le=1,
        description="Sentiment score from -1 (negative) to 1 (positive)"
    )
    confidence: float = Field(
        ..., ge=0, le=1,
        description="Confidence in the analysis"
    )
    topics: list[str] = Field(
        default_factory=list, max_length=5,
        description="Key topics identified (max 5)"
    )
    action_recommendation: str = Field(
        ..., pattern="^(ignore|monitor|escalate)$",
        description="Recommended action"
    )
    rationale: str = Field(
        ..., max_length=500,
        description="Brief reasoning (1-2 sentences)"
    )

    @field_validator("topics")
    @classmethod
    def limit_topics(cls, v: list[str]) -> list[str]:
        """Ensure max 5 topics."""
        return v[:5] if len(v) > 5 else v


# System prompt for sentiment analysis
SENTIMENT_SYSTEM_PROMPT = """You are a sentiment analysis expert. Analyze the given text and provide:
1. sentiment_score: A score from -1 (very negative) to 1 (very positive)
2. confidence: Your confidence in this analysis from 0 to 1
3. topics: Key topics mentioned (max 5)
4. action_recommendation: One of "ignore", "monitor", or "escalate"
   - ignore: Content is neutral or positive, no action needed
   - monitor: Content has concerns that should be tracked
   - escalate: Content requires immediate attention
5. rationale: Brief explanation (1-2 sentences)

Be objective and consider the overall emotional tone, specific praise/criticism, and intent."""

# User prompt template
SENTIMENT_USER_PROMPT = """Analyze the sentiment of this text:

{text}

Respond with valid JSON only."""


class OpenAIClient:
    """
    Client for OpenAI API with structured output support.
    
    Uses gpt-4o-mini by default for cost efficiency.
    Implements retry logic for transient failures.
    """
    
    def __init__(self):
        """Initialize OpenAI client with settings."""
        settings = get_settings()
        self.client = OpenAI(api_key=settings.openai_api_key)
        self.model = settings.openai_model
        
    @with_retry(max_attempts=3, retry_on=(OpenAIError,))
    def analyze_sentiment(self, text: str) -> SentimentResponse:
        """
        Analyze sentiment of text using OpenAI.
        
        Args:
            text: Text to analyze (will be truncated to 2000 chars)
            
        Returns:
            SentimentResponse with score, confidence, topics, etc.
            
        Raises:
            OpenAIError: On API errors (retryable)
            OpenAIBadResponseError: On invalid response format (retryable)
        """
        # Truncate long text to avoid token limits
        truncated_text = text[:2000] if len(text) > 2000 else text
        
        with Timer() as timer:
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SENTIMENT_SYSTEM_PROMPT},
                        {"role": "user", "content": SENTIMENT_USER_PROMPT.format(text=truncated_text)},
                    ],
                    response_format={"type": "json_object"},
                    temperature=0.1,  # Low temperature for consistency
                    max_tokens=500,
                )
                
            except Exception as e:
                logger.error(
                    "openai_api_error",
                    error=str(e),
                    error_type=type(e).__name__,
                )
                raise OpenAIError(str(e), retryable=True)
        
        # Parse response
        try:
            content = response.choices[0].message.content
            data = json.loads(content)
            result = SentimentResponse(**data)
            
            logger.debug(
                "openai_analysis_complete",
                sentiment_score=result.sentiment_score,
                confidence=result.confidence,
                topics=result.topics,
                latency_ms=timer.elapsed_ms,
            )
            
            return result
            
        except json.JSONDecodeError as e:
            logger.error("openai_json_parse_error", error=str(e), content=content[:200])
            raise OpenAIBadResponseError(f"Invalid JSON: {e}")
            
        except Exception as e:
            logger.error("openai_response_validation_error", error=str(e))
            raise OpenAIBadResponseError(f"Response validation failed: {e}")


# Singleton instance
_client: Optional[OpenAIClient] = None


def get_openai_client() -> OpenAIClient:
    """Get or create OpenAI client singleton."""
    global _client
    if _client is None:
        _client = OpenAIClient()
    return _client


__all__ = ["OpenAIClient", "SentimentResponse", "get_openai_client"]