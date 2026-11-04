"""
Unit and integration tests for Multi-Alpha Growth Engine v3.1.4 (4-Hour Breakout Champion).
Verifies:
1. 4-hour configuration invariants (interval=240, 14.4M ms bar, 30-bar Donchian window, port 8084).
2. Indicator calculations with zero-lookahead PIT.
3. State persistence and accounting integrity.
4. News Core v1.2 defensive parameters compatibility.
"""

from decimal import Decimal
import json
from pathlib import Path
import tempfile
import pandas as pd
import pytest

from v3_1_4.src.config import (
    PORT,
    VERSION_NAME,
    CANDLE_INTERVAL,
    BAR_INTERVAL_MS,
    DONCHIAN_WINDOW,
    INITIAL_CAPITAL,
    BASE_RISK_PCT,
    MAX_CONCURRENT_POSITIONS,
    ENABLE_NEWS_DEFENSE,
    ENABLE_MACRO_PROFIT_TRIMMING,
    MACRO_LOCKOUT_BEFORE_MINUTES,
    MACRO_LOCKOUT_AFTER_MINUTES,
    MACRO_TRIM_RATIO,
    MACRO_TRIM_MIN_R,
)
from v3_1_4.src.paper_trader_v314 import PaperTraderV314, prepare_symbol_indicators
from v3.src.core_v3 import CoreV3Engine


def test_v314_config_invariants():
    """Validates that v3.1.4 is mathematically aligned for 4h trading."""
    assert PORT == 8084
    assert VERSION_NAME == "v3.1.4 (4h)"
    assert CANDLE_INTERVAL == "240"
    assert BAR_INTERVAL_MS == 4 * 3600 * 1000  # 14,400,000 ms
    assert DONCHIAN_WINDOW == 30               # 30 * 4h = 120h = 5 days lookback
    assert INITIAL_CAPITAL == Decimal("300.0")
    assert BASE_RISK_PCT == Decimal("0.010")
    assert MAX_CONCURRENT_POSITIONS == 4

    # News Core v1.2 parameters
    assert ENABLE_NEWS_DEFENSE is True
    assert ENABLE_MACRO_PROFIT_TRIMMING is True
    assert MACRO_LOCKOUT_BEFORE_MINUTES == 45
    assert MACRO_LOCKOUT_AFTER_MINUTES == 30
    assert MACRO_TRIM_RATIO == Decimal("0.30")
    assert MACRO_TRIM_MIN_R == Decimal("1.0")


def test_v314_indicator_pit_zero_lookahead():
    """Ensures Donchian channels use shift(1) zero-lookahead PIT on 4h bars."""
    bars = []
    base_ts = 1700000000000
    for i in range(50):
        bars.append({
            "timestamp": base_ts + i * BAR_INTERVAL_MS,
            "open": 100.0 + i,
            "high": 105.0 + i,
            "low": 95.0 + i,
            "close": 102.0 + i,
            "volume": 1000.0 * (i + 1),
        })
    df = pd.DataFrame(bars)
    res = prepare_symbol_indicators(df, lookback=DONCHIAN_WINDOW)

    assert "upper" in res.columns
    assert "lower" in res.columns
    assert "atr" in res.columns
    assert "adx" in res.columns

    # Check shift(1) property: bar 31's upper is max(high) of bars 1..30, NOT including bar 31
    expected_upper = df["high"].iloc[1:31].max()
    actual_upper = res["upper"].iloc[31]
    assert actual_upper == expected_upper


def test_v314_state_persistence_and_daemon_init():
    """Tests initializing PaperTraderV314 and saving/restoring persistent state."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)
        state_file = tmp_path / "BOT_STATE_PERSISTENT_V314.json"
        status_file = tmp_path / "PAPER_TRADING_LIVE_V314.json"
        md_file = tmp_path / "PAPER_TRADING_LIVE_V314.md"

        trader = PaperTraderV314(
            symbols=["BTCUSDT", "ETHUSDT"],
            client=None,
            enable_news_defense=False,
            state_file=state_file,
            live_status_file=status_file,
            live_md_file=md_file,
        )

        assert trader.bar_interval_ms == BAR_INTERVAL_MS
        assert trader.engine.realized_equity == Decimal("300.0")

        # Save persistent state
        trader.save_persistent_state()
        assert state_file.exists()

        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data["initial_capital"] == "300.0"
        assert data["realized_equity"] == "300.0"
        assert "monitored_symbols" in data["runtime"]
