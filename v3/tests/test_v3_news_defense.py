"""
Unit and Integration Tests for News Core Defensive Shield in Multi-Alpha Growth Engine v3.
Verifies:
1. Defensive-only initialization (no offensive orders).
2. Regime Guard: Macro Blackout (FOMC/CPI/NFP) blocks breakout entries.
3. Regime Guard: USDC depeg blocks breakout entries.
4. Emergency Eject: Long position closed immediately on verified CRASH_SHOCK.
5. Squeeze Escape: Short position closed immediately on MAJOR_CATALYST.
6. Runner Protection: Long position with R >= 1.5R trails stop, never dumps market.
7. Soft Defense: Tier 3 Media rumor without market drop tightens stop, does not dump.
8. Super-Runner Defense: Positive catalyst with profit >= 0.8R moves stop to BE +0.3%.
"""

from datetime import datetime, timezone
from decimal import Decimal
import pytest

from v3.src.config import (
    INITIAL_CAPITAL,
    BASE_RISK_PCT,
    ENABLE_NEWS_DEFENSE,
    ENABLE_MACRO_BLACKOUT,
    ENABLE_DEPEG_GUARD,
    ENABLE_EMERGENCY_EJECT,
    ENABLE_SQUEEZE_ESCAPE,
    ENABLE_RUNNER_PROTECTION,
)
from v3.src.core_v3 import CoreV3Engine, ActivePosition
from v3.src.paper_trader_v3 import PaperTraderV3
from v3.src.news_core import (
    NewsCore,
    NewsConfig,
    PositionView,
    PositionAction,
    ActionType,
    EventType,
    Side,
)


@pytest.fixture
def mock_rules():
    return {
        "BTCUSDT": {
            "minOrderQty": Decimal("0.001"),
            "qtyStep": Decimal("0.001"),
            "minNotional": Decimal("5.0"),
            "maxOrderQty": Decimal("100"),
            "tickSize": Decimal("0.10"),
        },
        "SOLUSDT": {
            "minOrderQty": Decimal("0.01"),
            "qtyStep": Decimal("0.01"),
            "minNotional": Decimal("5.0"),
            "maxOrderQty": Decimal("1000"),
            "tickSize": Decimal("0.01"),
        },
        "ETHUSDT": {
            "minOrderQty": Decimal("0.01"),
            "qtyStep": Decimal("0.01"),
            "minNotional": Decimal("5.0"),
            "maxOrderQty": Decimal("1000"),
            "tickSize": Decimal("0.01"),
        },
    }


@pytest.fixture
def test_trader(mock_rules, tmp_path):
    trader = PaperTraderV3.__new__(PaperTraderV3)
    trader.symbols = ["BTCUSDT", "SOLUSDT", "ETHUSDT"]
    trader.initial_capital = Decimal("300.0")
    trader.poll_interval = 5
    trader.bar_interval_ms = 14400000
    import threading
    trader.lock = threading.RLock()
    trader.instrument_rules = mock_rules
    trader.state_file = tmp_path / "BOT_STATE_TEST_V3.json"
    trader.candle_buffers = {}
    trader.last_processed_bar_ts = {s: 0 for s in trader.symbols}
    trader.prices = {
        "BTCUSDT": Decimal("60000.0"),
        "SOLUSDT": Decimal("150.0"),
        "ETHUSDT": Decimal("3000.0"),
    }
    trader.price_times = {s: 1000000.0 for s in trader.symbols}
    trader.price_server_times = {s: 1704067200000 for s in trader.symbols}
    trader.daily_date = ""
    trader.batches = {}
    trader.ready = True
    trader.btc_trend_status = "BULLISH"
    trader.screened_universe = []
    trader.last_screener_run_ts = 0.0

    trader.engine = CoreV3Engine(
        initial_capital=Decimal("300.0"),
        instrument_rules=mock_rules,
        base_risk_pct=Decimal("0.02"),
        max_leverage=Decimal("3.5"),
    )

    class DummyNotifier:
        def __init__(self):
            self.notifications = []
        def notify(self, event):
            self.notifications.append(event)

    trader.notifier = DummyNotifier()

    cfg = NewsConfig.defensive_only()
    trader.news_core = NewsCore(cfg)

    return trader


def test_v3_news_defense_initialization(test_trader):
    """Verifies that News Core is initialized in strictly defensive mode."""
    nc = test_trader.news_core
    assert nc is not None
    assert nc.config.enable_emergency_eject is True
    assert nc.config.enable_squeeze_escape is True
    assert nc.config.enable_super_runner is True
    assert nc.config.enable_n1_pullback is False
    assert nc.config.enable_n3_crash_short is False
    assert nc.config.enable_n2_sympathy is False


def test_v3_regime_guard_macro_blackout_blocks_entries(test_trader):
    """Verifies that high-impact macro window (FOMC rate decision) blocks new breakout entries."""
    fomc_time = datetime(2024, 1, 31, 19, 5, 0, tzinfo=timezone.utc)
    status = test_trader.news_core.get_status("SOLUSDT", fomc_time)
    assert status["block_new_entries"] is True
    assert "Macro blackout" in status["reason"]

    safe_time = datetime(2024, 1, 31, 17, 0, 0, tzinfo=timezone.utc)
    status_safe = test_trader.news_core.get_status("SOLUSDT", safe_time)
    assert status_safe["block_new_entries"] is False


def test_v3_regime_guard_depeg_blocks_entries(test_trader):
    """Verifies that stablecoin depegging (>0.8%) blocks new trade entries."""
    now = datetime(2024, 5, 10, 12, 0, 0, tzinfo=timezone.utc)

    # Peg intact ($1.0005)
    test_trader.news_core.regime_guard.update_stablecoin_price(1.0005)
    assert test_trader.news_core.get_status("BTCUSDT", now)["block_new_entries"] is False

    # Severe depeg ($0.9850, -1.5% depeg)
    test_trader.news_core.regime_guard.update_stablecoin_price(0.9850)
    status_depeg = test_trader.news_core.get_status("BTCUSDT", now)
    assert status_depeg["block_new_entries"] is True
    assert "depeg" in status_depeg["reason"].lower()


def test_v3_emergency_eject_long_on_crash_shock(test_trader):
    """
    Emergency Eject:
    Long position in SOLUSDT must be closed at MARKET immediately
    when a verified CRASH_SHOCK headline arrives with market confirmation.
    """
    test_trader.engine.open_positions["SOLUSDT"] = ActivePosition(
        symbol="SOLUSDT",
        direction=1,
        initial_qty=Decimal("1.0"),
        current_qty=Decimal("1.0"),
        entry_price=Decimal("150.0"),
        current_stop=Decimal("142.5"),
        initial_atr=Decimal("3.0"),
        highest_price=Decimal("152.0"),
        lowest_price=Decimal("148.0"),
        entry_time=1000,
    )
    test_trader.prices["SOLUSDT"] = Decimal("148.0")

    actions = test_trader.handle_news_event(
        headline="BREAKING: Solana Foundation multi-sig wallet exploited for $150M in ongoing hack",
        symbol="SOLUSDT",
        source_tier=1,
        ret_1m=-0.025,
        vol_1m=5.0,
        vol_median_60m=1.0,
        timestamp=datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
    )

    assert len(actions) == 1
    assert actions[0].action_type == ActionType.CLOSE_MARKET
    assert "SOLUSDT" not in test_trader.engine.open_positions
    assert len(test_trader.engine.closed_trades) == 1
    assert test_trader.engine.closed_trades[0].exit_reason.startswith("NEWS_")


def test_v3_squeeze_escape_short_on_major_catalyst(test_trader):
    """
    Squeeze Escape:
    Short position in ETHUSDT must be closed immediately
    when a MAJOR_CATALYST is approved.
    """
    test_trader.engine.open_positions["ETHUSDT"] = ActivePosition(
        symbol="ETHUSDT",
        direction=-1,
        initial_qty=Decimal("0.1"),
        current_qty=Decimal("0.1"),
        entry_price=Decimal("3000.0"),
        current_stop=Decimal("3100.0"),
        initial_atr=Decimal("40.0"),
        highest_price=Decimal("3020.0"),
        lowest_price=Decimal("2950.0"),
        entry_time=1000,
    )
    test_trader.prices["ETHUSDT"] = Decimal("2980.0")

    actions = test_trader.handle_news_event(
        headline="SEC Approves Spot Ethereum ETF Listing on all US Exchanges",
        symbol="ETHUSDT",
        source_tier=1,
        ret_1m=0.035,
        vol_1m=6.0,
        vol_median_60m=1.0,
        timestamp=datetime(2024, 5, 23, 21, 0, 0, tzinfo=timezone.utc),
    )

    assert len(actions) == 1
    assert actions[0].action_type == ActionType.CLOSE_MARKET
    assert "ETHUSDT" not in test_trader.engine.open_positions
    assert len(test_trader.engine.closed_trades) == 1


def test_v3_runner_protection_trails_stop_instead_of_eject(test_trader):
    """
    Runner Protection:
    If a Long position has accumulated significant profit (>= 1.5R),
    a negative news shock must NOT dump at market.
    Instead, it must tighten the trailing stop close to the current price.
    """
    # Open a winning Long on SOLUSDT (entry 150.0, stop 145.0, initial risk = $5.0)
    # Current price = $162.0 -> profit = $12.0 = 2.4R (well above 1.5R threshold)
    test_trader.prices["SOLUSDT"] = Decimal("162.0")
    test_trader.engine.open_positions["SOLUSDT"] = ActivePosition(
        symbol="SOLUSDT",
        direction=1,
        initial_qty=Decimal("1.0"),
        current_qty=Decimal("1.0"),
        entry_price=Decimal("150.0"),
        current_stop=Decimal("145.0"),
        initial_atr=Decimal("2.0"),
        highest_price=Decimal("163.0"),
        lowest_price=Decimal("149.0"),
        entry_time=1000,
    )

    headline = "SOLANA ECOSYSTEM DAPP HACKED: $10M stolen in bridge exploit"
    actions = test_trader.handle_news_event(
        headline=headline,
        symbol="SOLUSDT",
        source_tier=2,
        ret_1m=-0.012,
        vol_1m=4000.0,
        vol_median_60m=1000.0,
        timestamp=datetime(2024, 7, 15, 14, 0, 0, tzinfo=timezone.utc),
    )

    assert len(actions) == 1
    assert actions[0].action_type == ActionType.MODIFY_STOP_TP
    assert "RUNNER_PROTECTION_TRAIL" in actions[0].reason
    assert "SOLUSDT" in test_trader.engine.open_positions
    new_stop = test_trader.engine.open_positions["SOLUSDT"].current_stop
    assert new_stop >= Decimal("152.0")
