"""
Action Agent for triggering alerts based on sentiment analysis results.

Evaluates scored items and creates alerts when:
    - Negative ratio exceeds threshold (negative_spike)
    - Risk keywords detected (risk_keyword, reserved)

Alert payload for negative_spike:
{
    "ratio": 0.45,
    "threshold": 0.30,
    "total_items": 100,
    "negative_items": 45,
    "top_negative_topics": ["price", "quality"],
    "recommendation": "escalate"
}
"""

from collections import Counter
from typing import Optional

import requests

from ..config import get_settings
from ..extensions import get_db_context
from ..logging_config import Timer, bind_context, get_logger
from ..models import Alert, AlertType, Job, SentimentLabel
from .sentiment import ScoredItem

logger = get_logger(__name__)


class ActionAgent:
    """
    Agent for evaluating results and triggering alerts.
    
    Checks:
        1. Negative ratio threshold
        2. (Reserved) Risk keywords
        3. (Reserved) Sentiment spikes
    """
    
    def __init__(self):
        """Initialize action agent with settings."""
        self.settings = get_settings()
    
    def _calculate_stats(self, scored_items: list[ScoredItem]) -> dict:
        """
        Calculate aggregate statistics from scored items.
        
        Returns dict with:
            - total_items
            - label_counts
            - negative_ratio
            - positive_ratio
            - neutral_ratio
            - avg_score
            - top_topics
        """
        if not scored_items:
            return {}
        
        # Count labels
        label_counts = Counter(item.label for item in scored_items)
        total = len(scored_items)
        
        # Calculate ratios
        negative_count = label_counts.get(SentimentLabel.NEGATIVE, 0)
        positive_count = label_counts.get(SentimentLabel.POSITIVE, 0)
        neutral_count = label_counts.get(SentimentLabel.NEUTRAL, 0)
        
        # Aggregate topics
        all_topics = []
        for item in scored_items:
            if item.topics:
                all_topics.extend(item.topics)
        top_topics = [topic for topic, _ in Counter(all_topics).most_common(10)]
        
        # Calculate average score
        scores = [item.sentiment_final for item in scored_items if item.sentiment_final is not None]
        avg_score = sum(scores) / len(scores) if scores else 0
        
        return {
            "total_items": total,
            "label_counts": {
                "positive": positive_count,
                "neutral": neutral_count,
                "negative": negative_count,
            },
            "negative_ratio": negative_count / total if total > 0 else 0,
            "positive_ratio": positive_count / total if total > 0 else 0,
            "neutral_ratio": neutral_count / total if total > 0 else 0,
            "avg_score": avg_score,
            "top_topics": top_topics,
        }
    
    def _get_top_negative_topics(self, scored_items: list[ScoredItem], limit: int = 5) -> list[str]:
        """Get most common topics from negative items."""
        negative_topics = []
        for item in scored_items:
            if item.label == SentimentLabel.NEGATIVE and item.topics:
                negative_topics.extend(item.topics)
        return [topic for topic, _ in Counter(negative_topics).most_common(limit)]
    
    def _create_alert(
        self,
        job_id: int,
        alert_type: AlertType,
        payload: dict,
    ) -> Alert:
        """Create and persist an alert."""
        with get_db_context() as session:
            alert = Alert(
                job_id=job_id,
                type=alert_type,
                payload=payload,
            )
            session.add(alert)
            session.flush()  # Get the ID
            
            logger.info(
                "alert_created",
                alert_id=alert.id,
                alert_type=alert_type.value,
                job_id=job_id,
            )
            
            return alert
    
    def _send_slack_webhook(self, alert: Alert, job: Job) -> bool:
        """
        Send alert to Slack webhook.
        
        Returns True if successful, False otherwise.
        """
        webhook_url = self.settings.slack_webhook_url
        if not webhook_url:
            return False
        
        try:
            payload = {
                "text": f"🚨 Sentiment Alert: {alert.type.value}",
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Alert Type:* {alert.type.value}\n*Job ID:* {job.id}\n*Source:* {job.source_type}"
                        }
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*Details:*\n```{alert.payload}```"
                        }
                    }
                ]
            }
            
            response = requests.post(webhook_url, json=payload, timeout=10)
            response.raise_for_status()
            
            logger.info("slack_webhook_sent", alert_id=alert.id)
            return True
            
        except Exception as e:
            logger.error("slack_webhook_failed", alert_id=alert.id, error=str(e))
            return False
    
    def run(self, job: Job, scored_items: list[ScoredItem]) -> list[Alert]:
        """
        Evaluate results and create alerts if thresholds exceeded.
        
        Args:
            job: Job model
            scored_items: List of items with sentiment scores
            
        Returns:
            List of created Alert objects
        """
        bind_context(job_id=job.id, stage="action")
        
        logger.info(
            "action_agent_started",
            items_count=len(scored_items),
        )
        
        alerts = []
        
        with Timer() as timer:
            # Calculate statistics
            stats = self._calculate_stats(scored_items)
            
            if not stats:
                logger.info("action_agent_no_items")
                return alerts
            
            negative_ratio = stats["negative_ratio"]
            threshold = self.settings.alert_negative_ratio_threshold
            
            # Check negative ratio threshold
            if negative_ratio > threshold:
                top_negative_topics = self._get_top_negative_topics(scored_items)
                
                # Determine recommendation based on ratio
                if negative_ratio > 0.5:
                    recommendation = "escalate"
                elif negative_ratio > 0.4:
                    recommendation = "monitor"
                else:
                    recommendation = "monitor"
                
                payload = {
                    "ratio": round(negative_ratio, 4),
                    "threshold": threshold,
                    "total_items": stats["total_items"],
                    "negative_items": stats["label_counts"]["negative"],
                    "top_negative_topics": top_negative_topics,
                    "recommendation": recommendation,
                }
                
                alert = self._create_alert(
                    job_id=job.id,
                    alert_type=AlertType.NEGATIVE_SPIKE,
                    payload=payload,
                )
                alerts.append(alert)
                
                # Send webhook notification
                self._send_slack_webhook(alert, job)
        
        logger.info(
            "action_agent_complete",
            alerts_created=len(alerts),
            negative_ratio=round(stats.get("negative_ratio", 0), 4),
            latency_ms=timer.elapsed_ms,
        )
        
        return alerts


__all__ = ["ActionAgent"]