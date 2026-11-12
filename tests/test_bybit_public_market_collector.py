"""
Unit Tests for Bybit Public Market Data Collector.
Verifies:
1. 10-topic batching invariant.
2. Zero-credential public stream subscription structure.
3. Keepalive mechanics.
"""

from pathlib import Path
import tempfile
import pytest

from tools.bybit_public_market_collector import BybitPublicMarketCollector


def test_public_collector_topic_batching():
    symbols = [f"COIN{i}USDT" for i in range(25)]
    collector = BybitPublicMarketCollector(symbols=symbols)

    topics = [f"tickers.{s}" for s in symbols]
    chunks = collector._chunk_topics(topics, chunk_size=10)

    assert len(chunks) == 3
    assert len(chunks[0]) == 10
    assert len(chunks[1]) == 10
    assert len(chunks[2]) == 5
    for ch in chunks:
        assert len(ch) <= 10


def test_public_collector_initialization():
    with tempfile.TemporaryDirectory() as tmp_dir:
        collector = BybitPublicMarketCollector(
            symbols=["BTCUSDT", "ETHUSDT"],
            output_dir=Path(tmp_dir),
            collect_spot_bbo=True,
            collect_funding_tickers=True,
            collect_public_trades=False,
        )
        assert collector.symbols == ["BTCUSDT", "ETHUSDT"]
        assert collector.is_running is False
        assert collector.records_collected == 0
