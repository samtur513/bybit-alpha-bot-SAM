"""
Connectors and Parsers for Verified News Sources (2024 - 2026).
Based on audits in NEWS_SOURCES_VERIFIED.md:
1. Binance CMS API (cat 48 listings, 161 delistings, 49 news) - uses releaseDate (ms)
2. Upbit API (trade category) - uses first_listed_at (sec, KST -> UTC)
3. KuCoin Announcements API - uses cTime (ms)
4. ForexFactory Calendar - uses dateline (sec) for macro consensus vs actual
5. Telegram Channel preview - uses post datetime (UTC)
"""

from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional
import urllib.request
import urllib.error

logger = logging.getLogger("NewsSources")


@dataclass
class StandardNewsItem:
    """Standardized news event object across all sources."""
    source: str
    headline: str
    published_at: datetime  # Exact first publication time in UTC
    symbol: Optional[str] = None
    url: Optional[str] = None
    raw_data: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "headline": self.headline,
            "published_at": self.published_at.isoformat(),
            "symbol": self.symbol,
            "url": self.url,
        }


class NewsSourceParser:
    """Parses raw API responses from verified sources into StandardNewsItem objects."""

    @staticmethod
    def parse_binance_cms(data: Dict[str, Any]) -> List[StandardNewsItem]:
        """
        Parse Binance CMS article list query.
        releaseDate is Unix ms representing true first publication time.
        """
        items: List[StandardNewsItem] = []
        articles = data.get("data", {}).get("catalogs", [])
        # Sometimes articles are in data.articles or data.catalogs[0].articles
        article_list = []
        if isinstance(articles, list) and articles:
            for cat in articles:
                article_list.extend(cat.get("articles", []))
        elif "articles" in data.get("data", {}):
            article_list = data["data"]["articles"]

        for art in article_list:
            title = art.get("title", "")
            release_ms = art.get("releaseDate", 0)
            if not release_ms:
                continue

            dt_utc = datetime.fromtimestamp(release_ms / 1000.0, tz=timezone.utc)
            code = art.get("code")
            url = f"https://www.binance.com/en/support/announcement/{code}" if code else None

            items.append(
                StandardNewsItem(
                    source="Binance_CMS",
                    headline=title,
                    published_at=dt_utc,
                    url=url,
                    raw_data=art,
                )
            )
        return items

    @staticmethod
    def parse_upbit_announcements(data: Dict[str, Any]) -> List[StandardNewsItem]:
        """
        Parse Upbit API announcements.
        CRITICAL: Uses 'first_listed_at' (first publication), NOT 'listed_at' (last edit).
        Upbit timestamps are in KST (UTC+9).
        """
        items: List[StandardNewsItem] = []
        notices = data.get("data", {}).get("list", [])
        if not notices and isinstance(data, list):
            notices = data

        for notif in notices:
            title = notif.get("title", "")
            first_listed = notif.get("first_listed_at") or notif.get("created_at")
            if not first_listed:
                continue

            # Parse ISO string or unix
            if isinstance(first_listed, (int, float)):
                dt_utc = datetime.fromtimestamp(first_listed, tz=timezone.utc)
            else:
                # Upbit returns ISO like "2024-03-05T20:00:00+09:00"
                try:
                    dt = datetime.fromisoformat(str(first_listed))
                    dt_utc = dt.astimezone(timezone.utc)
                except Exception:
                    continue

            items.append(
                StandardNewsItem(
                    source="Upbit_API",
                    headline=title,
                    published_at=dt_utc,
                    url=f"https://upbit.com/service_center/notice?id={notif.get('id')}",
                    raw_data=notif,
                )
            )
        return items

    @staticmethod
    def parse_kucoin_announcements(data: Dict[str, Any]) -> List[StandardNewsItem]:
        """
        Parse KuCoin announcements API.
        Uses cTime (Unix ms) for publication.
        """
        items: List[StandardNewsItem] = []
        ann_list = data.get("data", {}).get("items", [])
        for ann in ann_list:
            title = ann.get("annTitle", "")
            ctime = ann.get("cTime", 0)
            if not ctime:
                continue

            dt_utc = datetime.fromtimestamp(ctime / 1000.0, tz=timezone.utc)
            items.append(
                StandardNewsItem(
                    source="KuCoin_API",
                    headline=title,
                    published_at=dt_utc,
                    url=ann.get("annUrl"),
                    raw_data=ann,
                )
            )
        return items

    @staticmethod
    def parse_telegram_preview_html(html_text: str) -> List[StandardNewsItem]:
        """
        Parse public web preview of Telegram channels (e.g. t.me/s/Bybit_Announcements).
        Extracts <time datetime="2024-01-05T03:59:08+00:00"> and post text.
        """
        import re
        items: List[StandardNewsItem] = []
        # Pattern for message post and time
        time_matches = re.finditer(
            r'<time\s+datetime="([^"]+)"[^>]*>.*?</time>.*?<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
            html_text,
            re.DOTALL
        )
        for m in time_matches:
            time_str = m.group(1)
            raw_text = re.sub(r'<[^<]+?>', '', m.group(2)).strip()
            first_line = raw_text.split('\n')[0] if raw_text else ""
            if not first_line:
                continue

            try:
                dt = datetime.fromisoformat(time_str)
                dt_utc = dt.astimezone(timezone.utc)
            except Exception:
                continue

            items.append(
                StandardNewsItem(
                    source="Telegram_Channel",
                    headline=first_line,
                    published_at=dt_utc,
                    raw_data={"full_text": raw_text}
                )
            )
        return items


class VerifiedSourcesFetcher:
    """Safe, rate-limited fetcher for verified free endpoints."""

    DEFAULT_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) NewsCoreResearch/2.1"

    @classmethod
    def fetch_binance_listings(cls, page_no: int = 1, page_size: int = 20) -> List[StandardNewsItem]:
        """Fetch Binance announcements without API key (Catalog 48 = New Listing)."""
        url = (
            f"https://www.binance.com/bapi/composite/v1/public/cms/article/list/query"
            f"?type=1&pageNo={page_no}&pageSize={page_size}&catalogId=48"
        )
        req = urllib.request.Request(url, headers={"User-Agent": cls.DEFAULT_USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return NewsSourceParser.parse_binance_cms(data)
        except Exception as e:
            logger.warning(f"Failed to fetch Binance listings: {e}")
            return []

    @classmethod
    def fetch_upbit_trade_notices(cls, page_no: int = 1, page_size: int = 20) -> List[StandardNewsItem]:
        """Fetch Upbit trade announcements without API key."""
        url = (
            f"https://api-manager.upbit.com/api/v1/announcements"
            f"?os=web&page={page_no}&per_page={page_size}&category=trade"
        )
        req = urllib.request.Request(url, headers={"User-Agent": cls.DEFAULT_USER_AGENT})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return NewsSourceParser.parse_upbit_announcements(data)
        except Exception as e:
            logger.warning(f"Failed to fetch Upbit notices: {e}")
            return []
