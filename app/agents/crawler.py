"""
Crawler Agent for fetching content from various sources.

Supports:
    - YouTube: Comments from videos
    - Webpage: Paragraphs from HTML pages
    - RSS: Entries from RSS/Atom feeds

Output format (CrawledItem):
    {
        "external_id": str,      # Unique ID from source
        "raw_text": str,         # Content text
        "source_url": str,       # Optional: specific URL
        "metadata": dict,        # Source-specific data
        "lang": str,             # Optional: detected language
    }
"""

import hashlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Generator, Optional

import feedparser
import requests
from bs4 import BeautifulSoup

from ..logging_config import Timer, get_logger
from ..models import Job
from ..services.errors import CrawlEmptyError, CrawlError

logger = get_logger(__name__)


@dataclass
class CrawledItem:
    """
    Data class representing a single crawled item.
    
    Attributes:
        external_id: Unique identifier from the source
        raw_text: The actual text content
        source_url: Optional specific URL for this item
        metadata: Source-specific metadata (likes, author, etc.)
        lang: Optional detected language code
    """
    
    external_id: str
    raw_text: str
    source_url: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    lang: Optional[str] = None


class BaseCrawler(ABC):
    """Abstract base class for all crawlers."""
    
    @abstractmethod
    def crawl(self, url: str, max_items: int = 100) -> Generator[CrawledItem, None, None]:
        """
        Crawl content from the source.
        
        Args:
            url: URL to crawl
            max_items: Maximum number of items to return
            
        Yields:
            CrawledItem objects
        """
        pass


class YouTubeCrawler(BaseCrawler):
    """
    Crawler for YouTube video comments.
    
    Uses youtube-comment-downloader library which doesn't require API key.
    """
    
    def crawl(self, url: str, max_items: int = 100) -> Generator[CrawledItem, None, None]:
        """Crawl YouTube comments from video URL."""
        try:
            from youtube_comment_downloader import YoutubeCommentDownloader
        except ImportError:
            raise CrawlError("youtube-comment-downloader not installed", retryable=False)
        
        video_id = self._extract_video_id(url)
        if not video_id:
            raise CrawlError(f"Invalid YouTube URL: {url}", retryable=False)
        
        logger.info("youtube_crawl_started", video_id=video_id, max_items=max_items)
        
        try:
            downloader = YoutubeCommentDownloader()
            count = 0
            
            for comment in downloader.get_comments_from_url(url, sort_by=0):
                if count >= max_items:
                    break
                
                text = comment.get("text", "").strip()
                if not text:
                    continue
                
                yield CrawledItem(
                    external_id=comment.get("cid", f"yt_{count}"),
                    raw_text=text,
                    source_url=url,
                    metadata={
                        "author": comment.get("author", ""),
                        "likes": comment.get("votes", 0),
                        "reply_count": comment.get("reply_count", 0),
                        "time": comment.get("time", ""),
                        "video_id": video_id,
                    },
                )
                count += 1
            
            logger.info("youtube_crawl_complete", video_id=video_id, items=count)
            
        except Exception as e:
            logger.error("youtube_crawl_error", video_id=video_id, error=str(e))
            raise CrawlError(f"YouTube crawl failed: {e}")
    
    def _extract_video_id(self, url: str) -> Optional[str]:
        """Extract video ID from various YouTube URL formats."""
        patterns = [
            r'(?:v=|/v/|youtu\.be/)([^&\n?#]+)',
            r'(?:embed/)([^&\n?#]+)',
            r'(?:shorts/)([^&\n?#]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None


class WebpageCrawler(BaseCrawler):
    """
    Crawler for generic webpages.

    - Primary: extract paragraphs (<p>)
    - Fallback for list-style pages (e.g., Hacker News): extract titles/links
    - Final fallback: extract visible page text snippet
    """

    def crawl(self, url: str, max_items: int = 100) -> Generator[CrawledItem, None, None]:
        logger.info("webpage_crawl_started", url=url, max_items=max_items)

        try:
            # 更“像浏览器”的 UA，避免一些站点返回异常内容
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                )
            }
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()

        except requests.RequestException as e:
            logger.error("webpage_fetch_error", url=url, error=str(e))
            raise CrawlError(f"Failed to fetch webpage: {e}")

        html = response.text or ""
        soup = BeautifulSoup(html, "html.parser")

        # Remove non-content elements
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
            tag.decompose()

        # 关键调试信息：以后你一眼就知道为什么会 empty
        p_tags = soup.find_all("p")
        hn_titles = soup.select("span.titleline a")  # HN 首页标题
        logger.info(
            "webpage_parse_stats",
            url=url,
            status_code=response.status_code,
            html_len=len(html),
            p_count=len(p_tags),
            hn_title_count=len(hn_titles),
        )

        count = 0

        # -------------------------
        # 1) Primary: <p> paragraphs
        # -------------------------
        for i, p in enumerate(p_tags):
            if count >= max_items:
                break

            text = p.get_text(" ", strip=True)
            if len(text) < 20:
                continue

            external_id = hashlib.md5(text.encode()).hexdigest()[:16]
            yield CrawledItem(
                external_id=f"web_{external_id}",
                raw_text=text,
                source_url=url,
                metadata={"type": "paragraph", "paragraph_index": i},
            )
            count += 1

        # ---------------------------------------
        # 2) Fallback: list pages (HN-like titles)
        # ---------------------------------------
        if count == 0:
            for i, a in enumerate(hn_titles):
                if count >= max_items:
                    break

                title = a.get_text(" ", strip=True)
                href = a.get("href")
                if not title or len(title) < 5:
                    continue

                # 用 title + href 做 hash，更稳定
                key = f"{title}||{href or ''}"
                external_id = hashlib.md5(key.encode()).hexdigest()[:16]

                yield CrawledItem(
                    external_id=f"web_{external_id}",
                    raw_text=title,
                    source_url=url,
                    metadata={"type": "hn_title", "title_index": i, "href": href},
                )
                count += 1

        # ---------------------------------------
        # 3) Final fallback: visible text snippet
        # ---------------------------------------
        if count == 0:
            text = soup.get_text(" ", strip=True)
            text = " ".join(text.split())  # 压缩空白
            if text:
                snippet = text[:2000]  # 防止太长
                external_id = hashlib.md5(snippet.encode()).hexdigest()[:16]
                yield CrawledItem(
                    external_id=f"web_{external_id}",
                    raw_text=snippet,
                    source_url=url,
                    metadata={"type": "page_text_snippet", "chars": len(snippet)},
                )
                count = 1

        logger.info("webpage_crawl_complete", url=url, items=count)



class RSSCrawler(BaseCrawler):
    """
    Crawler for RSS/Atom feeds.
    
    Extracts entries from feed.
    """
    
    def crawl(self, url: str, max_items: int = 100) -> Generator[CrawledItem, None, None]:
        """Crawl entries from RSS feed."""
        logger.info("rss_crawl_started", url=url, max_items=max_items)
        
        try:
            feed = feedparser.parse(url)
            
            if feed.bozo and feed.bozo_exception:
                logger.warning("rss_parse_warning", url=url, error=str(feed.bozo_exception))
            
        except Exception as e:
            logger.error("rss_fetch_error", url=url, error=str(e))
            raise CrawlError(f"Failed to parse RSS: {e}")
        
        count = 0
        
        for entry in feed.entries[:max_items]:
            # Get content from various fields
            content = ""
            if hasattr(entry, "summary"):
                content = entry.summary
            elif hasattr(entry, "description"):
                content = entry.description
            elif hasattr(entry, "content") and entry.content:
                content = entry.content[0].value
            
            # Strip HTML tags
            if content:
                soup = BeautifulSoup(content, "html.parser")
                content = soup.get_text(strip=True)
            
            if not content or len(content) < 10:
                continue
            
            # Generate external ID
            external_id = getattr(entry, "id", "") or getattr(entry, "link", "") or f"rss_{count}"
            external_id = hashlib.md5(external_id.encode()).hexdigest()[:16]
            
            # Parse published date
            published = None
            if hasattr(entry, "published_parsed") and entry.published_parsed:
                try:
                    published = datetime(*entry.published_parsed[:6]).isoformat()
                except Exception:
                    pass
            
            yield CrawledItem(
                external_id=f"rss_{external_id}",
                raw_text=content,
                source_url=getattr(entry, "link", url),
                metadata={
                    "title": getattr(entry, "title", ""),
                    "author": getattr(entry, "author", ""),
                    "published": published,
                    "link": getattr(entry, "link", ""),
                },
            )
            count += 1
        
        logger.info("rss_crawl_complete", url=url, items=count)


class CrawlerAgent:
    """
    Main crawler agent that dispatches to appropriate crawler.
    
    Usage:
        agent = CrawlerAgent()
        items = agent.run(job)
    """
    
    CRAWLERS = {
        "youtube": YouTubeCrawler,
        "webpage": WebpageCrawler,
        "rss": RSSCrawler,
    }
    
    def __init__(self):
        """Initialize crawler instances."""
        self._crawlers: dict[str, BaseCrawler] = {}
    
    def _get_crawler(self, source_type: str) -> BaseCrawler:
        """Get or create crawler for source type."""
        if source_type not in self._crawlers:
            crawler_class = self.CRAWLERS.get(source_type)
            if not crawler_class:
                raise CrawlError(f"Unknown source type: {source_type}", retryable=False)
            self._crawlers[source_type] = crawler_class()
        return self._crawlers[source_type]
    
    def run(self, job: Job) -> list[CrawledItem]:
        """
        Run crawler for a job.
        
        Args:
            job: Job model with source_type, source_url, and options
            
        Returns:
            List of CrawledItem objects
            
        Raises:
            CrawlError: On crawl failure
            CrawlEmptyError: If no content found
        """
        source_type = job.source_type
        source_url = job.source_url
        options = job.options or {}
        
        max_items = options.get("max_items", 100)
        
        logger.info(
            "crawler_agent_started",
            job_id=job.id,
            source_type=source_type,
            max_items=max_items,
            stage="crawl",
        )
        
        with Timer() as timer:
            crawler = self._get_crawler(source_type)
            items = list(crawler.crawl(source_url, max_items))
        
        if not items:
            raise CrawlEmptyError(source_url)
        
        logger.info(
            "crawler_agent_complete",
            job_id=job.id,
            items_count=len(items),
            latency_ms=timer.elapsed_ms,
            stage="crawl",
        )
        
        return items


__all__ = ["CrawlerAgent", "CrawledItem"]