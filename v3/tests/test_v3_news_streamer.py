"""
Comprehensive Unit Tests for LiveNewsStreamer (v3.1).
Verifies:
1. RSS 2.0 and Atom XML parsing
2. Deduplication and LRU cache eviction
3. Warmup behavior (no false alerts on startup)
4. Network error resilience & malformed XML handling
5. End-to-end integration with NewsCore defensive shield
"""

from datetime import datetime, timezone
from decimal import Decimal
import pytest
from unittest.mock import MagicMock, patch

from v3.src.news_streamer import LiveNewsStreamer, NewsItem
from v3.src.news_core import (
    NewsCore,
    NewsConfig,
    PositionView,
    Side,
    ActionType,
    EventType,
)

SAMPLE_RSS_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Crypto News Sample</title>
    <item>
      <title>Binance halts Solana withdrawals amid $50M exploit</title>
      <link>https://example.com/news/1</link>
      <pubDate>Sun, 01 Nov 2026 12:00:00 +0000</pubDate>
      <guid>guid-news-1</guid>
    </item>
    <item>
      <title>SEC officially approves historic Ethereum staking ETF</title>
      <link>https://example.com/news/2</link>
      <pubDate>Sun, 01 Nov 2026 12:05:00 +0000</pubDate>
      <guid>guid-news-2</guid>
    </item>
    <item>
      <title>   </title>
      <link>https://example.com/news/3</link>
      <guid>guid-news-3</guid>
    </item>
  </channel>
</rss>
"""

SAMPLE_ATOM_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Crypto Atom Feed</title>
  <entry>
    <title>Major North Korea cyber attack targets Bitcoin infrastructure</title>
    <link href="https://example.com/atom/1"/>
    <updated>2026-11-01T12:10:00Z</updated>
    <id>urn:uuid:atom-1</id>
  </entry>
</feed>
"""


def test_parse_rss_20_xml():
    streamer = LiveNewsStreamer()
    items = streamer.parse_feed_xml("TestRSS", SAMPLE_RSS_XML)
    # Third item has whitespace title and should be skipped
    assert len(items) == 2
    assert items[0].headline == "Binance halts Solana withdrawals amid $50M exploit"
    assert items[0].source == "TestRSS"
    assert items[0].guid == "guid-news-1"
    assert items[0].published_at.year == 2026
    assert items[1].headline == "SEC officially approves historic Ethereum staking ETF"


def test_parse_atom_xml():
    streamer = LiveNewsStreamer()
    items = streamer.parse_feed_xml("TestAtom", SAMPLE_ATOM_XML)
    assert len(items) == 1
    assert "North Korea cyber attack" in items[0].headline
    assert items[0].guid == "urn:uuid:atom-1"
    assert items[0].source == "TestAtom"


def test_deduplication():
    streamer = LiveNewsStreamer(max_seen_history=3)
    emitted = []
    streamer.on_news = lambda item: emitted.append(item)

    with patch.object(streamer, "fetch_feed") as mock_fetch:
        mock_fetch.return_value = [
            NewsItem("1", "News 1", "Src", "url1", datetime.now(timezone.utc)),
            NewsItem("2", "News 2", "Src", "url2", datetime.now(timezone.utc)),
        ]
        # Poll 1
        new_items = streamer.poll_once(emit_events=True)
        assert len(new_items) == 2
        assert len(emitted) == 2

        # Poll 2 with duplicate items
        new_items_2 = streamer.poll_once(emit_events=True)
        assert len(new_items_2) == 0
        assert len(emitted) == 2  # No new callback invocations

        # Poll 3 with one new item and one old item
        mock_fetch.return_value = [
            NewsItem("2", "News 2", "Src", "url2", datetime.now(timezone.utc)),
            NewsItem("3", "News 3", "Src", "url3", datetime.now(timezone.utc)),
        ]
        new_items_3 = streamer.poll_once(emit_events=True)
        assert len(new_items_3) == 1
        assert new_items_3[0].guid == "3"
        assert len(emitted) == 3


def test_warmup_suppresses_callbacks():
    """Verify that during warmup, items are cached without triggering alerts."""
    streamer = LiveNewsStreamer()
    emitted = []
    streamer.on_news = lambda item: emitted.append(item)

    with patch.object(streamer, "fetch_feed") as mock_fetch:
        mock_fetch.return_value = [
            NewsItem("10", "Old breaking news", "Src", "url10", datetime.now(timezone.utc)),
        ]
        # Warmup
        count = streamer.warmup()
        assert count == 1
        assert len(emitted) == 0  # Crucial: callback NOT fired

        # Live poll with same news
        live_items = streamer.poll_once(emit_events=True)
        assert len(live_items) == 0
        assert len(emitted) == 0

        # Live poll with brand new news
        mock_fetch.return_value = [
            NewsItem("11", "Brand new headline", "Src", "url11", datetime.now(timezone.utc)),
        ]
        live_items_2 = streamer.poll_once(emit_events=True)
        assert len(live_items_2) == 1
        assert len(emitted) == 1
        assert emitted[0].guid == "11"


def test_resilience_to_network_and_malformed_xml():
    streamer = LiveNewsStreamer(feeds=[("FailFeed", "http://bad.url")])
    
    # 1. Malformed XML
    items = streamer.parse_feed_xml("FailFeed", b"<!INVALID XML GARBAGE>>")
    assert items == []

    # 2. HTTP error response
    with patch("requests.get") as mock_get:
        mock_get.return_value.status_code = 500
        items_err = streamer.fetch_feed("FailFeed", "http://bad.url")
        assert items_err == []
        assert streamer.feed_errors["FailFeed"] == 1

    # 3. Connection timeout
    with patch("requests.get", side_effect=Exception("Connection timed out")):
        items_timeout = streamer.fetch_feed("FailFeed", "http://bad.url")
        assert items_timeout == []
        assert streamer.feed_errors["FailFeed"] == 2


def test_integration_with_news_core_defense():
    """
    End-to-end integration test: LiveNewsStreamer emits headlines,
    NewsCore classifies them and triggers appropriate defensive actions.
    - Tier 3 Media: Tightens stop loss (SOFT_DEFENSE_MEDIA)
    - Tier 1 Official: Immediate emergency eject (CLOSE_MARKET)
    """
    cfg = NewsConfig.defensive_only()
    cfg.enable_emergency_eject = True
    news_core = NewsCore(cfg)

    # Mock open position in SOLUSDT (Long)
    open_pos = {
        "SOLUSDT": PositionView(
            symbol="SOLUSDT",
            side=Side.LONG,
            size=10.0,
            entry_price=150.0,
            current_price=148.0,
            stop_loss=140.0,
            unrealized_pnl_r=-0.2,
        )
    }

    dispatched_actions = []

    def on_live_headline(item: NewsItem):
        sym = news_core.extract_symbol(item.headline, default="BTCUSDT")
        event_type, score = news_core.classify(item.headline, symbol=sym)
        if score != 0:
            actions = news_core.manage_positions(
                symbol=sym,
                score=score,
                event=event_type,
                open_positions=open_pos,
                current_price=148.0,
                source_tier=3,  # Media tier from RSS
            )
            dispatched_actions.extend(actions)

    streamer = LiveNewsStreamer(on_news_callback=on_live_headline)

    # 1. Simulate arrival of breaking media news
    breaking = NewsItem(
        guid="exploit-1",
        headline="BREAKING: Solana bridge hit by critical smart contract exploit, $100M drained",
        source="CoinTelegraph",
        url="https://example.com/exploit",
        published_at=datetime.now(timezone.utc),
    )

    with patch.object(streamer, "fetch_feed", return_value=[breaking]):
        streamer.poll_once(emit_events=True)

    # Verification: Media source tightens stop to protect position
    assert len(dispatched_actions) == 1
    action = dispatched_actions[0]
    assert action.symbol == "SOLUSDT"
    assert action.action_type == ActionType.MODIFY_STOP_TP
    assert "SOFT_DEFENSE" in action.reason.upper()
    assert action.stop_price > 140.0  # Stop was tightened upward from 140.0

    # 2. Simulate Tier 1 official announcement -> Emergency Eject
    tier1_actions = news_core.manage_positions(
        symbol="SOLUSDT",
        score=-3,
        event=EventType.CRASH_SHOCK,
        open_positions=open_pos,
        current_price=148.0,
        source_tier=1,
    )
    assert len(tier1_actions) == 1
    assert tier1_actions[0].action_type == ActionType.CLOSE_MARKET
    assert "EMERGENCY_EJECT" in tier1_actions[0].reason
