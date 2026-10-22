"""
Unit and Integration Tests for News Core.
Tests:
- Layer 1: Classification & 45-minute cooldown deduplication.
- Layer 2: Market confirmation volume & price return thresholds.
- Layer 3: Position management matrix (Emergency Eject, Squeeze Escape, Super-Runner).
- Layer 4: Offensive order generation (N1 Pullback limit 45%, N3 Crash Short).
- Regime Guard: Macro blackout window, stablecoin depeg, gold spike.
- Replay engine: Zero-lookahead execution simulation & cost models.
- Main bot integration facade compatibility.
"""

import sys
from pathlib import Path

# Ensure News Core directory is on path
_this_dir = Path(__file__).parent.resolve()
if str(_this_dir) not in sys.path:
    sys.path.insert(0, str(_this_dir))

import pytest
from datetime import datetime, timezone, timedelta

from news_core_final import (
    NewsCore,
    NewsConfig,
    PositionView,
    PositionAction,
    OrderIntent,
    Side,
    EventType,
    ActionType,
    MarketCandle,
    RegimeStatus,
)
from sources import NewsSourceParser, StandardNewsItem
from replay_engine import ReplaySimulator


# ============================================================================
# Layer 1: Classification & Deduplication Tests
# ============================================================================

def test_classifier_crash_shock():
    core = NewsCore(NewsConfig())
    headlines = [
        "Radiant Capital hacked for $4.5M via smart contract vulnerability",
        "Binance announces delisting of WAVES and OMG trading pairs",
        "Major lending protocol insolvent and filing Chapter 11 bankruptcy",
        "SEC sues leading crypto exchange for alleged securities violations",
        "Stablecoin depeg crisis: token drops 10% below peg",
    ]
    for h in headlines:
        event, score = core.classify(
            h,
            timestamp=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
            check_cooldown=False,
        )
        assert event == EventType.CRASH_SHOCK, f"Failed on: {h}"
        assert score == -3, f"Score should be -3 on: {h}"


def test_classifier_major_catalyst():
    core = NewsCore(NewsConfig())
    headlines = [
        "Binance Will List Dogwifhat (WIF) with Seed Tag Applied",
        "Upbit will list AXIS on KRW and BTC markets",
        "SEC approves 11 spot Bitcoin ETFs in landmark decision",
        "Foundation announces massive token burn and buyback program",
        "Layer 1 protocol completes successful mainnet launch",
    ]
    for h in headlines:
        event, score = core.classify(
            h,
            timestamp=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc),
            check_cooldown=False,
        )
        assert event == EventType.MAJOR_CATALYST, f"Failed on: {h}"
        assert score == 3, f"Score should be +3 on: {h}"


def test_classifier_geopolitical_shock():
    core = NewsCore(NewsConfig())
    h = "Missile strike and war declared in Middle East escalation"
    event, score = core.classify(h, timestamp=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc))
    assert event == EventType.GEOPOLITICAL_SHOCK
    assert score == -2


def test_classifier_neutral():
    core = NewsCore(NewsConfig())
    headlines = [
        "Weekly technical analysis: Bitcoin consolidates inside 4-hour range",
        "Crypto community discusses governance voting proposal 12",
        "Developer tutorial on writing Vyper smart contracts",
    ]
    for h in headlines:
        event, score = core.classify(h, timestamp=datetime(2024, 1, 1, 10, 0, tzinfo=timezone.utc))
        assert event == EventType.NEUTRAL
        assert score == 0


def test_cooldown_deduplication():
    core = NewsCore(NewsConfig(cooldown_minutes=45))
    t0 = datetime(2024, 3, 5, 10, 0, 0, tzinfo=timezone.utc)
    headline = "Binance Will List Dogwifhat (WIF)"

    # First arrival -> Processed
    event1, score1 = core.classify(headline, timestamp=t0)
    assert event1 == EventType.MAJOR_CATALYST
    assert score1 == 3

    # Duplicate arrival after 5 minutes from another channel -> Suppressed by 45m cooldown
    t1 = t0 + timedelta(minutes=5)
    event2, score2 = core.classify(headline, timestamp=t1)
    assert event2 == EventType.NEUTRAL
    assert score2 == 0

    # Arrival after 46 minutes (cooldown expired) -> Processed
    t2 = t0 + timedelta(minutes=46)
    event3, score3 = core.classify(headline, timestamp=t2)
    assert event3 == EventType.MAJOR_CATALYST
    assert score3 == 3


def test_symbol_extraction():
    core = NewsCore(NewsConfig())
    assert core.extract_symbol("Binance Will List Dogwifhat (WIF)") == "WIFUSDT"
    assert core.extract_symbol("Notice of delisting SOLUSDT perpetual") == "SOLUSDT"
    assert core.extract_symbol("Exploit on $PEPE liquidity pool") == "PEPEUSDT"
    assert core.extract_symbol("Generic market commentary", default="BTCUSDT") == "BTCUSDT"


# ============================================================================
# Layer 2: Market Confirmation Tests
# ============================================================================

def test_market_confirmation_rules():
    core = NewsCore(NewsConfig(vol_multiplier=3.0, min_abs_return_1m=0.006))
    median_vol_60m = 100.0

    # Case 1: Low volume (200 < 300) -> Fails confirmation
    candle_low_vol = MarketCandle(
        timestamp=1700000000000,
        open=100.0,
        high=102.0,
        low=100.0,
        close=101.5,  # +1.5% return
        volume=200.0,  # Only 2x median!
    )
    assert not core.check_market_confirmation(
        score=3,
        candle_1m=candle_low_vol,
        median_vol_60m=median_vol_60m,
    )

    # Case 2: High volume (400 >= 300) but return too weak (+0.3% < 0.6%) -> Fails
    candle_weak_return = MarketCandle(
        timestamp=1700000000000,
        open=100.0,
        high=100.5,
        low=99.8,
        close=100.3,  # +0.3% return
        volume=400.0,
    )
    assert not core.check_market_confirmation(
        score=3,
        candle_1m=candle_weak_return,
        median_vol_60m=median_vol_60m,
    )

    # Case 3: High volume (400 >= 300) AND price moved opposite (down on positive news) -> Fails
    candle_wrong_direction = MarketCandle(
        timestamp=1700000000000,
        open=100.0,
        high=100.1,
        low=98.5,
        close=99.0,  # -1.0% return
        volume=400.0,
    )
    assert not core.check_market_confirmation(
        score=3,
        candle_1m=candle_wrong_direction,
        median_vol_60m=median_vol_60m,
    )

    # Case 4: High volume (450 >= 300) AND price surged +1.2% in direction -> Confirmed!
    candle_confirmed_catalyst = MarketCandle(
        timestamp=1700000000000,
        open=100.0,
        high=101.8,
        low=99.9,
        close=101.2,  # +1.2% return
        volume=450.0,
    )
    assert core.check_market_confirmation(
        score=3,
        candle_1m=candle_confirmed_catalyst,
        median_vol_60m=median_vol_60m,
    )

    # Case 5: Negative shock confirmed: High volume (500) AND return -1.5% <= -0.6% -> Confirmed!
    candle_confirmed_crash = MarketCandle(
        timestamp=1700000000000,
        open=100.0,
        high=100.2,
        low=98.0,
        close=98.5,  # -1.5% return
        volume=500.0,
    )
    assert core.check_market_confirmation(
        score=-3,
        candle_1m=candle_confirmed_crash,
        median_vol_60m=median_vol_60m,
    )


# ============================================================================
# Layer 3: Position Management Matrix Tests
# ============================================================================

def test_position_matrix_emergency_eject():
    core = NewsCore(NewsConfig(enable_emergency_eject=True))
    pos = PositionView(
        symbol="SOLUSDT",
        side=Side.LONG,
        size=10.0,
        entry_price=150.0,
        current_price=147.0,
        stop_loss=145.0,
    )
    # Trigger emergency eject via confirmed drop or tier 1
    actions = core.manage_positions(
        symbol="SOLUSDT",
        score=-3,
        event=EventType.CRASH_SHOCK,
        open_positions=[pos],
        current_price=147.0,
        ret_1m=-0.02, # -2% confirmed drop
        source_tier=1, # Official tier
    )
    assert len(actions) == 1
    act = actions[0]
    assert act.action_type == ActionType.CLOSE_MARKET
    assert act.symbol == "SOLUSDT"
    assert act.size == 10.0
    assert "EMERGENCY_EJECT" in act.reason


def test_position_matrix_squeeze_escape():
    core = NewsCore(NewsConfig(enable_squeeze_escape=True))
    pos = PositionView(
        symbol="DOGEUSDT",
        side=Side.SHORT,
        size=5000.0,
        entry_price=0.15,
        current_price=0.152,
        stop_loss=0.16,
    )
    actions = core.manage_positions(
        symbol="DOGEUSDT",
        score=3,
        event=EventType.MAJOR_CATALYST,
        open_positions=[pos],
        current_price=0.152,
    )
    assert len(actions) == 1
    act = actions[0]
    assert act.action_type == ActionType.CLOSE_MARKET
    assert act.symbol == "DOGEUSDT"
    assert "squeeze" in act.reason.lower()


def test_position_matrix_super_runner():
    core = NewsCore(NewsConfig(
        enable_super_runner=True,
        super_runner_r_threshold=0.8,
        super_runner_breakeven_offset_pct=0.003
    ))
    # Entry: 100, Stop: 95 (Risk = 5). Current price: 105 -> PnL = +5 / 5 = 1.0R (>= 0.8R)
    pos = PositionView(
        symbol="SOLUSDT",
        side=Side.LONG,
        size=20.0,
        entry_price=100.0,
        current_price=105.0,
        stop_loss=95.0,
        unrealized_pnl_r=1.0,
    )
    actions = core.manage_positions(
        symbol="SOLUSDT",
        score=3,
        event=EventType.MAJOR_CATALYST,
        open_positions=[pos],
        current_price=105.0,
    )
    assert len(actions) == 1
    act = actions[0]
    assert act.action_type == ActionType.MODIFY_STOP_TP
    # New stop should be entry * 1.003 = 100.3
    assert act.stop_price == 100.3
    assert "runner" in act.reason.lower()


# ============================================================================
# Layer 4: Offensive Order Generation Tests
# ============================================================================

def test_offensive_n1_pullback():
    core = NewsCore(NewsConfig(
        enable_n1_pullback=True,
        pullback_ratio=0.45,
        n1_stop_loss_pct=0.015,
        max_news_risk_pct=0.10,
    ))
    high = 110.0
    low = 100.0  # Range = 10.0
    # Long pullback limit: high - (10 * 0.45) = 110 - 4.5 = 105.5
    order = core.generate_n1_pullback("SOLUSDT", high, low, Side.LONG, deposit=1000.0)
    assert order is not None
    assert order.symbol == "SOLUSDT"
    assert order.side == Side.LONG
    assert order.order_type == "LIMIT"
    assert order.limit_price == 105.5
    assert order.post_only is True
    assert order.strategy_tag == "N1_PULLBACK"
    assert order.stop_loss == round(100.0 * (1 - 0.015), 4)  # 98.5
    assert order.size > 0


def test_offensive_n3_crash_short():
    core = NewsCore(NewsConfig(enable_n3_crash_short=True, n3_stop_loss_pct=0.02))
    current_price = 50.0
    high_price = 55.0
    order = core.generate_n3_crash_short("RDNTUSDT", current_price, high_price, deposit=1000.0)
    assert order is not None
    assert order.symbol == "RDNTUSDT"
    assert order.side == Side.SHORT
    assert order.order_type == "MARKET"
    assert order.strategy_tag == "N3_CRASH_SHORT"
    assert order.stop_loss > current_price


# ============================================================================
# Regime Guard Tests
# ============================================================================

def test_regime_guard_macro_blackout():
    core = NewsCore(NewsConfig(macro_blackout_minutes=15))
    # FOMC is at 2024-01-31 19:00:00 UTC
    fomc_time = datetime(2024, 1, 31, 19, 0, 0, tzinfo=timezone.utc)

    # 10 minutes before FOMC -> Inside ±15m window -> BLOCKED
    t_inside = fomc_time - timedelta(minutes=10)
    status_inside = core.get_status("BTCUSDT", t_inside)
    assert status_inside["block_new_entries"] is True
    assert "Macro blackout" in status_inside["reason"]

    # 30 minutes before FOMC -> Outside window -> CLEAR
    t_outside = fomc_time - timedelta(minutes=30)
    status_outside = core.get_status("BTCUSDT", t_outside)
    assert status_outside["block_new_entries"] is False
    assert status_outside["reason"] is None


def test_regime_guard_stablecoin_depeg():
    core = NewsCore(NewsConfig(enable_stablecoin_guard=True, stablecoin_depeg_threshold=0.008))
    now = datetime(2024, 1, 15, 12, 0, 0, tzinfo=timezone.utc)

    # Normal price -> Clear
    core.regime_guard.update_stablecoin_price(1.0002)
    assert core.get_status("SOLUSDT", now)["block_new_entries"] is False

    # Depeg: USDC drops to 0.985 (1.5% drop > 0.8% threshold) -> BLOCKED
    core.regime_guard.update_stablecoin_price(0.9850)
    status = core.get_status("SOLUSDT", now)
    assert status["block_new_entries"] is True
    assert "depeg" in status["reason"].lower()


def test_regime_guard_gold_spike():
    core = NewsCore(NewsConfig(enable_paxg_guard=True, paxg_spike_threshold_1m=0.007))
    t0 = datetime(2024, 4, 13, 20, 0, 0, tzinfo=timezone.utc)

    # Extreme safe-haven spike +0.85%
    core.regime_guard.record_paxg_spike(t0, 0.0085)

    # 5 minutes later -> BLOCKED
    t1 = t0 + timedelta(minutes=5)
    status = core.get_status("BTCUSDT", t1)
    assert status["block_new_entries"] is True
    assert "PAXG" in status["reason"]


# ============================================================================
# Replay Simulator Tests
# ============================================================================

def test_replay_limit_order_strictly_through():
    """Verify limit order executes ONLY if price trades strictly through."""
    sim = ReplaySimulator(NewsConfig(enable_n1_pullback=True, pullback_ratio=0.45))
    t0 = datetime(2024, 3, 5, 10, 0, 0, tzinfo=timezone.utc)

    # Impulse bar: Open 100, High 110, Low 100, Close 108, Volume 500 (confirmed)
    c0 = MarketCandle(timestamp=int(t0.timestamp() * 1000), open=100.0, high=110.0, low=100.0, close=108.0, volume=500.0)
    # Range = 10, Pullback limit = 110 - 4.5 = 105.5

    # Bar 1: Low 106.0 -> Did NOT trade through 105.5
    c1 = MarketCandle(timestamp=int((t0 + timedelta(minutes=1)).timestamp() * 1000), open=108.0, high=109.0, low=106.0, close=107.0, volume=200.0)
    # Bar 2: Low 104.5 -> Strictly trades THROUGH 105.5!
    c2 = MarketCandle(timestamp=int((t0 + timedelta(minutes=2)).timestamp() * 1000), open=107.0, high=107.0, low=104.5, close=105.0, volume=350.0)
    # Bar 3: Rebound to 112.0 (Take profit)
    c3 = MarketCandle(timestamp=int((t0 + timedelta(minutes=3)).timestamp() * 1000), open=105.0, high=116.0, low=104.8, close=115.0, volume=400.0)

    res = sim.simulate_event(
        headline="Binance Will List Dogwifhat (WIF)",
        published_at_utc=t0,
        candles_1m=[c0, c1, c2, c3],
        median_vol_60m=100.0,
        existing_position=None,
    )
    assert res["status"] == "SUCCESS"
    assert res["trade"] is not None
    trade = res["trade"]
    assert trade.entry_price == 105.5
    assert trade.exit_reason in ("TAKE_PROFIT", "TIME_HORIZON_CLOSE")
    assert trade.net_return_r > 0


def test_main_bot_code_snippet_compatibility():
    """Verify the exact integration code snippet specified by the user."""
    core = NewsCore(NewsConfig())
    now_utc = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

    # 1. Before opening trade
    status = core.get_status("SOLUSDT", now_utc)
    if status["block_new_entries"]:
        return  # Would exit

    # 2. Headline arrives
    headline = "Radiant Capital exploit drains lending protocol"
    event, score = core.classify(headline)
    assert abs(score) >= 2

    # Open positions
    open_positions = [
        PositionView(
            symbol="SOLUSDT",
            side=Side.LONG,
            size=5.0,
            entry_price=120.0,
            current_price=118.0,
            stop_loss=115.0,
        )
    ]
    current_price = 118.0

    actions = core.manage_positions(
        "SOLUSDT",
        score,
        event,
        open_positions,
        current_price,
        ret_1m=-0.015,
        source_tier=1
    )
    assert len(actions) > 0
    assert actions[0].action_type == ActionType.CLOSE_MARKET

    # 3. N1 Generation
    high, low = 125.0, 115.0
    n1 = core.generate_n1_pullback("SOLUSDT", high, low, Side.LONG)
    assert n1 is not None
    assert n1.post_only is True
    assert n1.limit_price < high


# ============================================================================
# False-Positive Defense Tests (v1.1)
# ============================================================================

def test_v11_price_drop_gate_blocks_eject_when_flat():
    """Verify emergency eject is BLOCKED if price has not confirmed panic drop."""
    core = NewsCore(NewsConfig(require_price_drop_for_eject=True, eject_min_drop_1m=0.010, soft_defense_for_media=False))
    pos = PositionView(
        symbol="SOLUSDT",
        side=Side.LONG,
        size=10.0,
        entry_price=100.0,
        current_price=100.0,
        stop_loss=95.0,
        unrealized_pnl_r=0.5,
    )
    # Media headline arrives, but ret_1m is 0.0 (no price drop!)
    actions = core.manage_positions(
        symbol="SOLUSDT",
        score=-3,
        event="CRASH_SHOCK",
        open_positions=[pos],
        current_price=100.0,
        ret_1m=0.0,
        vol_1m=50.0,
        vol_median_60m=100.0,
        source_tier=3,
    )
    # Price did NOT drop 1% -> Emergency eject must NOT trigger!
    close_actions = [a for a in actions if a.action_type == ActionType.CLOSE_MARKET]
    assert len(close_actions) == 0


def test_v11_price_drop_gate_triggers_eject_when_dumping():
    """Verify emergency eject TRIGGERS when price actually dumps >= 1% on volume."""
    core = NewsCore(NewsConfig(require_price_drop_for_eject=True, eject_min_drop_1m=0.010))
    pos = PositionView(
        symbol="SOLUSDT",
        side=Side.LONG,
        size=10.0,
        entry_price=100.0,
        current_price=98.5,
        stop_loss=95.0,
        unrealized_pnl_r=-0.3,
    )
    # Panic confirmed: ret_1m = -1.5% and volume is 4x median
    actions = core.manage_positions(
        symbol="SOLUSDT",
        score=-3,
        event="CRASH_SHOCK",
        open_positions=[pos],
        current_price=98.5,
        ret_1m=-0.015,
        vol_1m=400.0,
        vol_median_60m=100.0,
        source_tier=3,
    )
    assert len(actions) == 1
    assert actions[0].action_type == ActionType.CLOSE_MARKET
    assert "EMERGENCY_EJECT" in actions[0].reason


def test_v11_tier1_official_ejects_immediately():
    """Verify Tier 1 official exchange source ejects immediately without waiting for drop."""
    core = NewsCore(NewsConfig(require_price_drop_for_eject=True))
    pos = PositionView(
        symbol="SOLUSDT",
        side=Side.LONG,
        size=10.0,
        entry_price=100.0,
        current_price=100.0,
        stop_loss=95.0,
    )
    # Official Binance CMS notice (source_tier=1)
    actions = core.manage_positions(
        symbol="SOLUSDT",
        score=-3,
        event="CRASH_SHOCK",
        open_positions=[pos],
        current_price=100.0,
        ret_1m=0.0,
        source_tier=1,
    )
    assert len(actions) == 1
    assert actions[0].action_type == ActionType.CLOSE_MARKET
    assert "tier=1" in actions[0].reason


def test_v11_media_tier3_uses_soft_defense():
    """Verify Tier 3 media uses soft defense (tightens stop) when price is flat."""
    core = NewsCore(NewsConfig(soft_defense_for_media=True, soft_stop_offset_pct=0.005))
    pos = PositionView(
        symbol="SOLUSDT",
        side=Side.LONG,
        size=10.0,
        entry_price=100.0,
        current_price=102.0,
        stop_loss=95.0,
    )
    # Media headline, ret_1m is flat (-0.1%)
    actions = core.manage_positions(
        symbol="SOLUSDT",
        score=-3,
        event="CRASH_SHOCK",
        open_positions=[pos],
        current_price=102.0,
        ret_1m=-0.001,
        source_tier=3,
    )
    assert len(actions) == 1
    assert actions[0].action_type == ActionType.MODIFY_STOP_TP
    # Soft stop tightened to 102.0 * (1 - 0.005) = 101.49
    assert actions[0].stop_price > pos.stop_loss
    assert "SOFT_DEFENSE_MEDIA" in actions[0].reason


def test_v11_runner_protection_trails_stop():
    """Verify that a strong runner (R >= 1.5R) is never market-closed, but trails stop."""
    core = NewsCore(NewsConfig(protect_runners_min_r=1.5, runner_trail_r=0.5))
    # Entry: 100, Stop: 90 (Risk = 10). Current price: 120 -> R = +2.0R (>= 1.5R)
    pos = PositionView(
        symbol="SOLUSDT",
        side=Side.LONG,
        size=5.0,
        entry_price=100.0,
        current_price=120.0,
        stop_loss=90.0,
        unrealized_pnl_r=2.0,
    )
    actions = core.manage_positions(
        symbol="SOLUSDT",
        score=-3,
        event="CRASH_SHOCK",
        open_positions=[pos],
        current_price=120.0,
        ret_1m=-0.02, # Even if price dumps 2%!
        source_tier=1,
    )
    assert len(actions) == 1
    # Runner protection overrides market eject -> trails stop!
    assert actions[0].action_type == ActionType.MODIFY_STOP_TP
    assert actions[0].stop_price >= 105.0 # Entry (100) + 0.5 * Risk (10) = 105.0
    assert "RUNNER_PROTECTION_TRAIL" in actions[0].reason
