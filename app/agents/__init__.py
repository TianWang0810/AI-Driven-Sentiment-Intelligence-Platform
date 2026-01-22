"""
Agents package.

Contains modular agents for the pipeline:
    - CrawlerAgent: Fetches content from sources
    - SentimentAgent: Analyzes sentiment using TextBlob + OpenAI
    - ActionAgent: Triggers alerts based on results
"""

from .actions import ActionAgent
from .crawler import CrawledItem, CrawlerAgent
from .sentiment import ScoredItem, SentimentAgent

__all__ = [
    "CrawlerAgent",
    "CrawledItem",
    "SentimentAgent",
    "ScoredItem",
    "ActionAgent",
]