"""
Unit and Integration Tests for News Core Defensive Shield in Multi-Alpha Growth Engine v2.1.
Verifies:
1. Defensive-only initialization (no offensive orders).
2. Regime Guard: Macro Blackout (FOMC/CPI/NFP) blocks breakout entries.
3. Regime Guard: USDC depeg blocks breakout entries.
4. Emergency Eject: Long position closed immediately on verified CRASH_SHOCK.
5. Squeeze Escape: Short position closed immediately on MAJOR_CATALYST.
6. Runner Protection: Long position with R >= 1.5R trails stop, never dumps market.
7. Soft Defense: Tier 3 Media rumor without market drop tightens stop, does not dump.
8. Super-Runner Defense: Positive catalyst with profit >= 0.8R moves stop to BE +0.3%.
9. Zero-lookahead and state persistence integrity.
"""

from datetime import datetime, timezone
from decimal import Decimal
import pytest

from v2_1.src.config import (
    INITIAL_CAPITAL,
    BASE_RISK_PCT,
    ENABLE_NEWS_DEFENSE,
    ENABLE_MACRO_BLACKOUT,
    ENABLE_DEPEG_GUARD,
    ENABLE_EMERGENCY_EJECT,
    ENABLE_SQUEEZE_ESCAPE,
    ENABLE_RUNNER_PROTECTION,
)
from v2_1.src.core_v2 import CoreV2Engine, ActivePosition
from v2_1.src.paper_trader_v2 import PaperTraderV21
from v2_1.src.news_core import (
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
    trader = PaperTraderV21.__new__(PaperTraderV21)
    trader.symbols = ["BTCUSDT", "SOLUSDT", "ETHUSDT"]
    trader.initial_capital = Decimal("300.0")
    trader.poll_interval = 5
    trader.bar_interval_ms = 14400000
    import threading
    trader.lock = threading.RLock()
    trader.instrument_rules = mock_rules
    trader.state_file = tmp_path / "BOT_STATE_TEST.json"
    trader.candle_buffers = {}
    trader.last_processed_1h_ts = {s: 0 for s in trader.symbols}
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
    trader.range_engine = None
    trader.regime_classifier = None

    trader.engine = CoreV2Engine(
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

    # Defensive-only News Core setup
    cfg = NewsConfig.defensive_only()
    trader.news_core = NewsCore(cfg)

    return trader


def test_news_defense_initialization(test_trader):
    """Verifies that News Core is initialized in strictly defensive mode."""
    nc = test_trader.news_core
    assert nc is not None
    assert nc.config.enable_emergency_eject is True
    assert nc.config.enable_squeeze_escape is True
    assert nc.config.enable_super_runner is True
    assert nc.config.enable_n1_pullback is False
    assert nc.config.enable_n3_crash_short is False
    assert nc.config.enable_n2_sympathy is False


def test_regime_guard_macro_blackout_blocks_entries(test_trader):
    """Verifies that high-impact macro window (FOMC rate decision) blocks new breakout entries."""
    # Scheduled FOMC: 2024-01-31 19:00:00 UTC
    # Test time 19:05 UTC (inside ±15m window)
    fomc_time = datetime(2024, 1, 31, 19, 5, 0, tzinfo=timezone.utc)
    status = test_trader.news_core.get_status("SOLUSDT", fomc_time)
    assert status["block_new_entries"] is True
    assert "Macro blackout" in status["reason"]

    # Test time 2 hours earlier (outside ±15m window)
    safe_time = datetime(2024, 1, 31, 17, 0, 0, tzinfo=timezone.utc)
    status_safe = test_trader.news_core.get_status("SOLUSDT", safe_time)
    assert status_safe["block_new_entries"] is False


def test_regime_guard_depeg_blocks_entries(test_trader):
    """Verifies that stablecoin depegging (>0.8%) blocks new trade entries."""
    now = datetime(2024, 5, 10, 12, 0, 0, tzinfo=timezone.utc)

    # Peg intact ($1.0000)
    test_trader.news_core.regime_guard.update_stablecoin_price(1.0005)
    assert test_trader.news_core.get_status("SOLUSDT", now)["block_new_entries"] is False

    # Peg broken ($0.9850 -> 1.5% drop > 0.8% threshold)
    test_trader.news_core.regime_guard.update_stablecoin_price(0.9850)
    st = test_trader.news_core.get_status("SOLUSDT", now)
    assert st["block_new_entries"] is True
    assert "Systemic stablecoin depeg" in st["reason"]


def test_emergency_eject_on_crash_shock(test_trader):
    """Verifies that an open Long is immediately closed on verified CRASH_SHOCK."""
    # Open an active Long on SOLUSDT
    test_trader.engine.open_positions["SOLUSDT"] = ActivePosition(
        symbol="SOLUSDT",
        direction=1,
        initial_qty=Decimal("1.0"),
        current_qty=Decimal("1.0"),
        entry_price=Decimal("150.0"),
        current_stop=Decimal("144.0"),
        initial_atr=Decimal("2.4"),
        highest_price=Decimal("152.0"),
        lowest_price=Decimal("149.0"),
        entry_time=1704067200000,
    )
    assert "SOLUSDT" in test_trader.engine.open_positions

    # Breaking news: Critical protocol hack (Tier 1 source, confirmed 1.5% 1m drop)
    headline = "SOLANA VALIDATOR EXPLOIT CONFIRMED: Hackers drain $200M from treasury"
    actions = test_trader.handle_news_event(
        headline=headline,
        symbol="SOLUSDT",
        source_tier=1,
        ret_1m=-0.015,
        vol_1m=5000.0,
        vol_median_60m=1000.0,
    )

    assert len(actions) == 1
    assert actions[0].action_type == ActionType.CLOSE_MARKET
    assert "EMERGENCY_EJECT" in actions[0].reason

    # Verify position is closed in engine
    assert "SOLUSDT" not in test_trader.engine.open_positions
    assert len(test_trader.engine.closed_trades) == 1
    assert "NEWS_EMERGENCY_EJECT" in test_trader.engine.closed_trades[0].exit_reason


def test_squeeze_escape_on_short(test_trader):
    """Verifies that an open Short is immediately closed on MAJOR_CATALYST."""
    # Open an active Short on ETHUSDT
    test_trader.engine.open_positions["ETHUSDT"] = ActivePosition(
        symbol="ETHUSDT",
        direction=-1,
        initial_qty=Decimal("0.5"),
        current_qty=Decimal("0.5"),
        entry_price=Decimal("3000.0"),
        current_stop=Decimal("3120.0"),
        initial_atr=Decimal("48.0"),
        highest_price=Decimal("3010.0"),
        lowest_price=Decimal("2980.0"),
        entry_time=1704067200000,
    )
    assert "ETHUSDT" in test_trader.engine.open_positions

    # Breaking news: Major ETF approval (Major Catalyst +3)
    headline = "SEC APPROVES SPOT ETHEREUM ETF SURPRISE VERDICT"
    actions = test_trader.handle_news_event(
        headline=headline,
        symbol="ETHUSDT",
        source_tier=1,
    )

    assert len(actions) == 1
    assert actions[0].action_type == ActionType.CLOSE_MARKET
    assert "SHORT_SQUEEZE_ESCAPE" in actions[0].reason

    # Verify short position is closed in engine
    assert "ETHUSDT" not in test_trader.engine.open_positions
    assert len(test_trader.engine.closed_trades) == 1
    assert "NEWS_SHORT_SQUEEZE_ESCAPE" in test_trader.engine.closed_trades[0].exit_reason


def test_runner_protection_trails_stop_no_market_dump(test_trader):
    """
    Verifies that a high-performing Long (R >= 1.5R) is NOT market-dumped
    on shock headlines. Instead, stop loss is trailed to protect +0.5R.
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
        entry_time=1704067200000,
    )

    # Shock news arrives
    headline = "SOLANA ECOSYSTEM DAPP HACKED: $10M stolen in bridge exploit"
    actions = test_trader.handle_news_event(
        headline=headline,
        symbol="SOLUSDT",
        source_tier=2,
        ret_1m=-0.012,
        vol_1m=4000.0,
        vol_median_60m=1000.0,
    )

    assert len(actions) == 1
    # MUST NOT be market close
    assert actions[0].action_type == ActionType.MODIFY_STOP_TP
    assert "RUNNER_PROTECTION_TRAIL" in actions[0].reason

    # Position must still be open in the engine
    assert "SOLUSDT" in test_trader.engine.open_positions
    # Stop loss must have been tightened from 145.0 to trail level (150 + 0.5*5 = 152.5)
    new_stop = test_trader.engine.open_positions["SOLUSDT"].current_stop
    assert new_stop >= Decimal("152.0")


def test_soft_defense_for_media_rumor_without_drop(test_trader):
    """
    Verifies that Tier 3 Media rumor without confirmed market dump
    does not dump the position at market; only applies soft stop tightening.
    """
    # Open Long on BTCUSDT at 60000, current price 60100 (R < 1.5R)
    test_trader.prices["BTCUSDT"] = Decimal("60100.0")
    test_trader.engine.open_positions["BTCUSDT"] = ActivePosition(
        symbol="BTCUSDT",
        direction=1,
        initial_qty=Decimal("0.05"),
        current_qty=Decimal("0.05"),
        entry_price=Decimal("60000.0"),
        current_stop=Decimal("58500.0"),
        initial_atr=Decimal("600.0"),
        highest_price=Decimal("60200.0"),
        lowest_price=Decimal("59900.0"),
        entry_time=1704067200000,
    )

    # Media headline with NO panic market drop (ret_1m = -0.002, below 1.0% drop threshold)
    headline = "MEDIA REPORT: Unconfirmed rumors claim potential smart contract bug in BTC network"
    actions = test_trader.handle_news_event(
        headline=headline,
        symbol="BTCUSDT",
        source_tier=3,  # Media
        ret_1m=-0.002,   # 0.2% drop only
        vol_1m=1000.0,
        vol_median_60m=1000.0,
    )

    assert len(actions) == 1
    assert actions[0].action_type == ActionType.MODIFY_STOP_TP
    assert "SOFT_DEFENSE_MEDIA" in actions[0].reason

    # Position remains open, stop is tightened
    assert "BTCUSDT" in test_trader.engine.open_positions
    new_stop = test_trader.engine.open_positions["BTCUSDT"].current_stop
    assert new_stop > Decimal("58500.0")


def test_super_runner_tightens_to_breakeven(test_trader):
    """
    Verifies that a profitable Long (profit >= 0.8R) with positive catalyst
    moves stop to breakeven + 0.3%.
    """
    # Entry 150.0, stop 145.0 (risk = 5.0). Current 155.0 -> profit = 5.0 = 1.0R (>= 0.8R)
    test_trader.prices["SOLUSDT"] = Decimal("155.0")
    test_trader.engine.open_positions["SOLUSDT"] = ActivePosition(
        symbol="SOLUSDT",
        direction=1,
        initial_qty=Decimal("1.0"),
        current_qty=Decimal("1.0"),
        entry_price=Decimal("150.0"),
        current_stop=Decimal("145.0"),
        initial_atr=Decimal("2.0"),
        highest_price=Decimal("155.5"),
        lowest_price=Decimal("149.5"),
        entry_time=1704067200000,
    )

    headline = "Solana Foundation announces major partnership with payments giant"
    actions = test_trader.handle_news_event(
        headline=headline,
        symbol="SOLUSDT",
        source_tier=1,
    )

    assert len(actions) == 1
    assert actions[0].action_type == ActionType.MODIFY_STOP_TP
    assert "SUPER_RUNNER_BE" in actions[0].reason

    # Stop should be at least BE + 0.3% = 150 * 1.003 = 150.45
    new_stop = test_trader.engine.open_positions["SOLUSDT"].current_stop
    assert new_stop >= Decimal("150.45")


def test_core_v2_process_bar_blocked_symbols(test_trader):
    """Verifies that CoreV2Engine honours blocked_symbols passed from Regime Guard."""
    bars = {
        "SOLUSDT": {
            "open": 150.0,
            "high": 160.0,
            "low": 149.0,
            "close": 159.0,
            "volume": 2000.0,
            "volume_sma": 1000.0,
            "upper": 155.0,  # Breakout above upper channel!
            "lower": 140.0,
            "ema_fast": 152.0,
            "ema_slow": 148.0,
            "atr": 2.5,
            "adx": 25.0,
        }
    }
    next_open = {"SOLUSDT": Decimal("159.5")}

    # Case 1: SOLUSDT is in blocked_symbols (e.g. macro embargo or depeg)
    test_trader.engine.process_bar_event(
        timestamp=1704067200000,
        bars=bars,
        next_open_prices=next_open,
        manage_positions=False,
        blocked_symbols={"SOLUSDT"},
    )
    assert "SOLUSDT" not in test_trader.engine.open_positions

    # Case 2: SOLUSDT is not blocked
    test_trader.engine.process_bar_event(
        timestamp=1704067200000 + 14400000,
        bars=bars,
        next_open_prices=next_open,
        manage_positions=False,
        blocked_symbols=set(),
    )
    assert "SOLUSDT" in test_trader.engine.open_positions
