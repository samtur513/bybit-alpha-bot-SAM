"""
Live News Streamer Module for Bybit Multi-Alpha Bot (v3.1).
Periodically pulls breaking cryptocurrency news from Tier-1 crypto feeds
(CoinTelegraph, CoinDesk, Decrypt RSS), parses headlines, deduplicates items,
and dispatches real-time events to News Core Defensive Shield.
"""

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
import email.utils
import hashlib
import logging
import re
import threading
import time
from typing import Callable, Dict, List, Optional, Set, Tuple
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger("LiveNewsStreamer")

DEFAULT_FEEDS = [
    ("CoinTelegraph", "https://cointelegraph.com/rss"),
    ("CoinDesk", "https://www.coindesk.com/arc/outboundfeeds/rss/"),
    ("Decrypt", "https://decrypt.co/feed"),
]


@dataclass
class NewsItem:
    guid: str
    headline: str
    source: str
    url: str
    published_at: datetime
    symbol: Optional[str] = None


class LiveNewsStreamer:
    """
    Autonomous multi-source crypto RSS/API streamer.
    Runs in a resilient background thread, polling feeds every `poll_interval_sec`.
    """

    def __init__(
        self,
        feeds: Optional[List[Tuple[str, str]]] = None,
        poll_interval_sec: float = 30.0,
        on_news_callback: Optional[Callable[[NewsItem], None]] = None,
        max_seen_history: int = 5000,
        user_agent: str = "BybitAlphaBot/3.1 (NewsDefensiveShield)",
        timeout_sec: float = 6.0,
    ):
        self.feeds = list(feeds or DEFAULT_FEEDS)
        self.poll_interval = poll_interval_sec
        self.on_news = on_news_callback
        self.max_seen = max_seen_history
        self.user_agent = user_agent
        self.timeout = timeout_sec

        self._seen_guids: Set[str] = set()
        self._seen_queue: deque = deque(maxlen=max_seen_history)
        self._lock = threading.Lock()
        self._running = False
        self._thread: Optional[threading.Thread] = None

        self.last_poll_ts: float = 0.0
        self.total_received: int = 0
        self.total_emitted: int = 0
        self.feed_errors: Dict[str, int] = {name: 0 for name, _ in self.feeds}

    def _make_guid(self, headline: str, url: str) -> str:
        raw = f"{headline.strip().lower()}|{url.strip().lower()}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _is_seen(self, guid: str) -> bool:
        with self._lock:
            return guid in self._seen_guids

    def _mark_seen(self, guid: str) -> None:
        with self._lock:
            if guid not in self._seen_guids:
                if len(self._seen_queue) >= self.max_seen:
                    oldest = self._seen_queue.popleft()
                    self._seen_guids.discard(oldest)
                self._seen_guids.add(guid)
                self._seen_queue.append(guid)

    def parse_feed_xml(self, source_name: str, xml_content: bytes) -> List[NewsItem]:
        """Parses RSS 2.0 or Atom XML content into a list of NewsItem objects."""
        items: List[NewsItem] = []
        try:
            root = ET.fromstring(xml_content)
        except Exception as e:
            logger.warning(f"LiveNewsStreamer: XML parse error for {source_name}: {e}")
            return []

        # RSS 2.0 <item> elements
        rss_items = root.findall(".//item")
        if rss_items:
            for it in rss_items:
                title_elem = it.find("title")
                link_elem = it.find("link")
                pub_elem = it.find("pubDate")
                guid_elem = it.find("guid")

                headline = title_elem.text.strip() if (title_elem is not None and title_elem.text) else ""
                url = link_elem.text.strip() if (link_elem is not None and link_elem.text) else ""
                if not headline:
                    continue

                # Published date
                pub_dt = datetime.now(timezone.utc)
                if pub_elem is not None and pub_elem.text:
                    try:
                        pub_dt = email.utils.parsedate_to_datetime(pub_elem.text.strip())
                    except Exception:
                        pub_dt = datetime.now(timezone.utc)

                guid = guid_elem.text.strip() if (guid_elem is not None and guid_elem.text) else self._make_guid(headline, url)
                items.append(NewsItem(
                    guid=guid,
                    headline=headline,
                    source=source_name,
                    url=url,
                    published_at=pub_dt,
                ))
            return items

        # Atom <entry> elements
        atom_entries = root.findall(".//{http://www.w3.org/2005/Atom}entry")
        for ent in atom_entries:
            title_elem = ent.find("{http://www.w3.org/2005/Atom}title")
            link_elem = ent.find("{http://www.w3.org/2005/Atom}link")
            updated_elem = ent.find("{http://www.w3.org/2005/Atom}updated")
            if updated_elem is None:
                updated_elem = ent.find("{http://www.w3.org/2005/Atom}published")
            id_elem = ent.find("{http://www.w3.org/2005/Atom}id")

            headline = title_elem.text.strip() if (title_elem is not None and title_elem.text) else ""
            url = link_elem.get("href", "").strip() if link_elem is not None else ""
            if not headline:
                continue

            pub_dt = datetime.now(timezone.utc)
            if updated_elem is not None and updated_elem.text:
                try:
                    # ISO 8601
                    ts_str = updated_elem.text.strip().replace("Z", "+00:00")
                    pub_dt = datetime.fromisoformat(ts_str)
                except Exception:
                    pub_dt = datetime.now(timezone.utc)

            guid = id_elem.text.strip() if (id_elem is not None and id_elem.text) else self._make_guid(headline, url)
            items.append(NewsItem(
                guid=guid,
                headline=headline,
                source=source_name,
                url=url,
                published_at=pub_dt,
            ))

        return items

    def fetch_feed(self, source_name: str, url: str) -> List[NewsItem]:
        """Fetches and parses a single RSS feed."""
        headers = {"User-Agent": self.user_agent}
        try:
            resp = requests.get(url, headers=headers, timeout=self.timeout)
            if resp.status_code == 200:
                self.feed_errors[source_name] = 0
                return self.parse_feed_xml(source_name, resp.content)
            else:
                logger.warning(f"LiveNewsStreamer: Feed {source_name} returned HTTP {resp.status_code}")
                self.feed_errors[source_name] = self.feed_errors.get(source_name, 0) + 1
        except Exception as e:
            logger.debug(f"LiveNewsStreamer: Failed to fetch {source_name} ({url}): {e}")
            self.feed_errors[source_name] = self.feed_errors.get(source_name, 0) + 1
        return []

    def poll_once(self, emit_events: bool = True) -> List[NewsItem]:
        """
        Polls all configured feeds once.
        If `emit_events` is False (used in warmup), marks all existing items as seen
        without triggering the callback.
        """
        new_items: List[NewsItem] = []
        for source_name, url in self.feeds:
            items = self.fetch_feed(source_name, url)
            for it in items:
                self.total_received += 1
                if not self._is_seen(it.guid):
                    self._mark_seen(it.guid)
                    new_items.append(it)
                    if emit_events and self.on_news:
                        self.total_emitted += 1
                        try:
                            self.on_news(it)
                        except Exception as e:
                            logger.error(f"LiveNewsStreamer: Callback error on '{it.headline[:40]}': {e}")

        self.last_poll_ts = time.time()
        return new_items

    def warmup(self) -> int:
        """
        Pre-populates seen cache with currently existing news to avoid a burst of old alerts.
        Returns count of pre-seeded news items.
        """
        logger.info("LiveNewsStreamer: Warming up seen cache from feeds...")
        seeded = self.poll_once(emit_events=False)
        logger.info(f"LiveNewsStreamer: Warmup complete. {len(seeded)} existing headlines cached.")
        return len(seeded)

    def _worker(self) -> None:
        """Background streaming worker loop."""
        logger.info(f"LiveNewsStreamer: Background daemon started (interval: {self.poll_interval}s).")
        try:
            self.warmup()
        except Exception as e:
            logger.warning(f"LiveNewsStreamer: Warmup failed (will proceed live): {e}")

        while self._running:
            try:
                self.poll_once(emit_events=True)
            except Exception as e:
                logger.error(f"LiveNewsStreamer: Unexpected error in poll loop: {e}")

            # Sleep in small increments for responsive stop()
            end_time = time.time() + self.poll_interval
            while self._running and time.time() < end_time:
                time.sleep(0.5)

        logger.info("LiveNewsStreamer: Background worker exited cleanly.")

    def start(self) -> None:
        """Starts background news streaming thread."""
        with self._lock:
            if self._running:
                return
            self._running = True
            self._thread = threading.Thread(target=self._worker, name="LiveNewsStreamerThread", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Stops background news streaming thread."""
        with self._lock:
            self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3.0)
            self._thread = None
