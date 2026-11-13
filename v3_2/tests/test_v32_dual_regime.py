"""
Unit Tests for Bybit Multi-Alpha v3.2 Dual-Regime Hybrid Engine.
Verifies:
1. Indicator computation (Trend Donchian + Regime Bollinger/Keltner/ADX/RSI)
2. Regime Classifier behavior
3. Range Engine Maker limit order generation and fill execution
4. Regime Handoff from Range to Trend
5. State persistence and accounting invariants
"""

from decimal import Decimal
import numpy as np
import pandas as pd
import pytest

from v3_2.src.regime_classifier import MarketRegimeClassifier, MarketRegime, RegimeData
from v3_2.src.range_engine import RangeMeanReversionEngine, RangeActivePosition, RangeLimitOrder
from v3_2.src.paper_trader_v32 import prepare_dual_regime_indicators


def test_dual_regime_indicators_computation():
    n = 150
    df = pd.DataFrame({
        "timestamp": range(1000, 1000 + n),
        "open": np.random.uniform(100, 110, n),
        "high": np.random.uniform(110, 120, n),
        "low": np.random.uniform(90, 100, n),
        "close": np.random.uniform(95, 115, n),
        "volume": np.random.uniform(1000, 5000, n),
    })
    clf = MarketRegimeClassifier()
    res = prepare_dual_regime_indicators(df, clf, lookback=30)

    for col in ["upper", "lower", "ema_fast", "ema_slow", "atr", "rsi", "adx", "bb_mid", "bb_upper", "bb_lower", "kc_upper", "kc_lower"]:
        assert col in res.columns
        assert not res[col].iloc[-10:].isna().all()


def test_regime_classifier_chop_vs_trend():
    clf = MarketRegimeClassifier(adx_trend_threshold=20.0, bbw_compression_percentile=40.0)

    # 1. Chop / Range case (ADX 14 < 20, BB width percentile 0.30)
    chop_reg = clf.classify_bar(
        symbol="BTCUSDT",
        adx=14.0,
        bb_mid=100.0,
        bb_upper=104.0,
        bb_lower=96.0,
        bb_width_pctl=0.30,
        kc_upper=103.0,
        kc_lower=97.0,
        rsi=45.0,
        atr=2.0,
        volume=1000.0,
        volume_sma=1200.0,
    )
    assert chop_reg.regime == MarketRegime.RANGE
    assert chop_reg.allows_range_trading is True
    assert chop_reg.allows_trend_trading is False

    # 2. Trend case (ADX 28 > 20, Volume > Volume SMA)
    trend_reg = clf.classify_bar(
        symbol="BTCUSDT",
        adx=28.0,
        bb_mid=100.0,
        bb_upper=112.0,
        bb_lower=88.0,
        bb_width_pctl=0.85,
        kc_upper=108.0,
        kc_lower=92.0,
        rsi=68.0,
        atr=3.5,
        volume=2500.0,
        volume_sma=1200.0,
    )
    assert trend_reg.regime == MarketRegime.TREND
    assert trend_reg.allows_range_trading is False
    assert trend_reg.allows_trend_trading is True


def test_range_maker_order_generation():
    engine = RangeMeanReversionEngine(initial_capital=Decimal("300.0"))
    bars = {
        "ETHUSDT": {
            "open": 100.0,
            "high": 105.0,
            "low": 95.0,
            "close": 96.0,
            "volume": 1000.0,
            "volume_sma": 1200.0,
            "atr": 2.0,
            "bb_mid": 100.0,
            "bb_lower": 95.5,
            "bb_upper": 104.5,
        }
    }
    regimes = {
        "ETHUSDT": RegimeData(
            symbol="ETHUSDT",
            regime=MarketRegime.RANGE,
            adx=14.0,
            bb_mid=100.0,
            bb_upper=104.5,
            bb_lower=95.5,
            bb_width=0.09,
            bb_width_pctl=0.30,
            kc_upper=103.0,
            kc_lower=97.0,
            rsi=28.0,  # Oversold RSI (< 35) triggers LONG Maker Buy
            atr=2.0,
            volume=1000.0,
            volume_sma=1200.0,
            is_volatility_compressed=True,
            is_chop=True,
            is_trend=False,
        )
    }

    orders = engine.scan_and_generate_orders(
        timestamp=1700000000000,
        bars=bars,
        regimes=regimes,
        btc_bearish=False,
    )

    assert len(orders) == 1
    ord0 = orders[0]
    assert ord0.symbol == "ETHUSDT"
    assert ord0.direction == 1
    assert ord0.price == Decimal("95.5")
    assert ord0.stop_loss == Decimal("93.50")
    assert ord0.take_profit > ord0.price


def test_range_maker_order_fill_and_tp():
    engine = RangeMeanReversionEngine(initial_capital=Decimal("300.0"))
    engine.pending_orders["ETHUSDT"] = RangeLimitOrder(
        symbol="ETHUSDT",
        direction=1,
        price=Decimal("95.5"),
        qty=Decimal("1.0"),
        stop_loss=Decimal("93.5"),
        take_profit=Decimal("97.5"),
        created_time=1700000000000,
        created_bar_idx=0,
        atr=Decimal("2.0"),
    )

    # Next bar penetrates below limit price: open 96.0, low 94.0 -> Fills!
    next_bars = {
        "ETHUSDT": {
            "open": 96.0,
            "high": 98.0,
            "low": 94.0,
            "close": 97.0,
        }
    }
    next_open = {"ETHUSDT": Decimal("96.0")}

    filled = engine.evaluate_pending_fills(next_bars, next_open)
    assert "ETHUSDT" in filled
    assert "ETHUSDT" in engine.open_positions
    pos = engine.open_positions["ETHUSDT"]
    assert pos.entry_price == Decimal("95.5")

    # Increment bar counter so position is eligible for management
    engine.bar_counter = 1

    # Following bar reaches take profit (High >= 97.5)
    bars_tp = {
        "ETHUSDT": {
            "open": 97.0,
            "high": 98.5,
            "low": 96.5,
            "close": 98.0,
            "volume": 1000.0,
            "volume_sma": 1000.0,
        }
    }
    regimes = {
        "ETHUSDT": RegimeData(
            symbol="ETHUSDT",
            regime=MarketRegime.RANGE,
            adx=15.0,
            bb_mid=97.5,
            bb_upper=102.0,
            bb_lower=94.0,
            bb_width=0.08,
            bb_width_pctl=0.35,
            kc_upper=100.0,
            kc_lower=95.0,
            rsi=55.0,
            atr=2.0,
            volume=1000.0,
            volume_sma=1000.0,
            is_volatility_compressed=True,
            is_chop=True,
            is_trend=False,
        )
    }

    closed = engine.manage_positions_on_bar(1700003600000, bars_tp, regimes)
    assert len(closed) >= 1
    assert any(c.exit_reason in ("TP", "TP1") for c in closed)
    assert all(c.net_pnl > 0 for c in closed)
    assert "ETHUSDT" not in engine.open_positions


def test_range_regime_handoff():
    """Verifies that an open Range position closes immediately when ADX surges >= 20 on breakout volume against position."""
    engine = RangeMeanReversionEngine(initial_capital=Decimal("300.0"))
    engine.open_positions["SOLUSDT"] = RangeActivePosition(
        symbol="SOLUSDT",
        direction=1,
        entry_price=Decimal("150.0"),
        current_qty=Decimal("1.0"),
        stop_loss=Decimal("140.0"),  # Wide stop so handoff triggers before SL
        take_profit=Decimal("158.0"),
        initial_atr=Decimal("5.0"),
        entry_time=1700000000000,
        entry_bar_idx=0,
        entry_fees=Decimal("0.05"),
    )
    engine.bar_counter = 1

    # Bar with surging bear momentum breaking below bb_lower: ADX = 26.0, Volume ratio = 2.5x, close < bb_lower
    bars_breakout = {
        "SOLUSDT": {
            "open": 146.0,
            "high": 147.0,
            "low": 143.0,
            "close": 143.5,
            "volume": 2500.0,
            "volume_sma": 1000.0,
        }
    }
    regimes_breakout = {
        "SOLUSDT": RegimeData(
            symbol="SOLUSDT",
            regime=MarketRegime.TREND,
            adx=26.0,
            bb_mid=150.0,
            bb_upper=155.0,
            bb_lower=145.0,
            bb_width=0.06,
            bb_width_pctl=0.60,
            kc_upper=154.0,
            kc_lower=146.0,
            rsi=32.0,
            atr=5.0,
            volume=2500.0,
            volume_sma=1000.0,
            is_volatility_compressed=False,
            is_chop=False,
            is_trend=True,
        )
    }

    closed = engine.manage_positions_on_bar(1700003600000, bars_breakout, regimes_breakout)
    assert len(closed) == 1
    assert closed[0].exit_reason == "REGIME_HANDOFF"
    assert "SOLUSDT" not in engine.open_positions


def test_range_engine_persistence_roundtrip():
    engine = RangeMeanReversionEngine(initial_capital=Decimal("300.0"))
    engine.open_positions["BTCUSDT"] = RangeActivePosition(
        symbol="BTCUSDT",
        direction=1,
        entry_price=Decimal("65000.0"),
        current_qty=Decimal("0.01"),
        stop_loss=Decimal("64000.0"),
        take_profit=Decimal("66000.0"),
        initial_atr=Decimal("1000.0"),
        entry_time=1700000000000,
        entry_bar_idx=10,
        entry_fees=Decimal("0.21"),
    )
    engine.pending_orders["SOLUSDT"] = RangeLimitOrder(
        symbol="SOLUSDT",
        direction=-1,
        price=Decimal("160.0"),
        qty=Decimal("2.0"),
        stop_loss=Decimal("165.0"),
        take_profit=Decimal("155.0"),
        created_time=1700000000000,
        created_bar_idx=10,
        atr=Decimal("5.0"),
    )

    data = engine.to_dict()
    assert data["initial_capital"] == "300.0"
    assert "BTCUSDT" in data["open_positions"]
    assert "SOLUSDT" in data["pending_orders"]

    restored = RangeMeanReversionEngine(initial_capital=Decimal("300.0"))
    restored.load_dict(data)

    assert "BTCUSDT" in restored.open_positions
    assert restored.open_positions["BTCUSDT"].entry_price == Decimal("65000.0")
    assert "SOLUSDT" in restored.pending_orders
    assert restored.pending_orders["SOLUSDT"].price == Decimal("160.0")


def test_v32_news_defense_integration():
    from v3_2.src.paper_trader_v32 import PaperTraderV32
    from datetime import datetime, timezone

    pt = PaperTraderV32(
        symbols=["BTCUSDT", "ETHUSDT"],
        initial_capital=Decimal("300.0"),
        enable_news_defense=True,
    )
    assert pt.enable_news_defense is True
    assert pt.regime_guard is not None

    # Normal time: no macro blackout
    normal_dt = datetime(2024, 3, 15, 12, 0, tzinfo=timezone.utc)
    is_embargo, reason = pt._check_macro_embargo(normal_dt)
    assert not is_embargo

    # Macro FOMC time: 2024-03-20 18:00 UTC -> Embargo active!
    fomc_dt = datetime(2024, 3, 20, 18, 0, tzinfo=timezone.utc)
    is_embargo, reason = pt._check_macro_embargo(fomc_dt)
    assert is_embargo
    assert "FOMC" in reason or "Macro" in reason

    # Test status export
    status = pt.poll_cycle()
    assert status["news_defense_active"] is True
    assert status["news_shield"] is True
    assert "open_positions" in status


def test_range_funding_accounting_and_partial_tp_conservation():
    """
    FLEET-PAPER-GATE-006 Step 1.C:
    Verifies that funding is never double-counted:
    - Exactly one cashflow entry per settlement.
    - Positive and negative funding cashflows are tracked in funding_cashflow_ledger.
    - Partial TP1 allocates proportional entry fee and settled funding.
    - Subsequent full exit preserves penny-perfect conservation:
      Delta Realized Equity == Sum(closed_trades.net_pnl) == Sum(Gross) - Sum(Fees) + Sum(FundingCashflows).
    """
    init_cap = Decimal("300.0")
    engine = RangeMeanReversionEngine(initial_capital=init_cap)

    # 1. Open Long position
    pos_qty = Decimal("10.0")
    entry_p = Decimal("100.0")
    entry_notional = pos_qty * entry_p
    entry_fee = entry_notional * engine.maker_fee
    engine.realized_equity -= entry_fee

    tp_price = Decimal("105.0")
    sl_price = Decimal("95.0")
    tp_dist = tp_price - entry_p
    tp1_price = entry_p + (tp_dist * Decimal("0.60"))  # 103.0

    engine.open_positions["ETHUSDT"] = RangeActivePosition(
        symbol="ETHUSDT",
        direction=1,
        entry_price=entry_p,
        current_qty=pos_qty,
        stop_loss=sl_price,
        take_profit=tp_price,
        initial_atr=Decimal("2.0"),
        entry_time=1700000000000,
        entry_bar_idx=0,
        entry_fees=entry_fee,
        tp1_done=False,
        tp1_price=tp1_price,
        initial_qty=pos_qty,
        be_activated=False,
    )
    engine.bar_counter = 1

    equity_after_entry = engine.realized_equity
    assert equity_after_entry == init_cap - entry_fee

    # 2. Settlement 1: Positive funding rate (+0.05% -> Long pays)
    pos_funding_rate = Decimal("0.0005")
    mark_1 = Decimal("101.0")
    engine.process_funding(1700028800000, pos_funding_rate, {"ETHUSDT": mark_1})
    expected_cf1 = -1 * Decimal("1") * pos_qty * mark_1 * pos_funding_rate
    assert engine.open_positions["ETHUSDT"].funding_pnl == expected_cf1
    assert engine.realized_equity == equity_after_entry + expected_cf1
    assert len(engine.funding_cashflows) == 1

    equity_after_fund1 = engine.realized_equity

    # 3. Bar reaches TP1 (High >= 103.0, but < 105.0)
    bars_tp1 = {
        "ETHUSDT": {
            "open": 102.0,
            "high": 103.5,
            "low": 101.5,
            "close": 102.5,
            "volume": 1000.0,
            "volume_sma": 1000.0,
        }
    }
    regimes_tp1 = {
        "ETHUSDT": RegimeData(
            symbol="ETHUSDT", regime=MarketRegime.RANGE, adx=14.0, bb_mid=105.0,
            bb_upper=108.0, bb_lower=98.0, bb_width=0.08, bb_width_pctl=0.30,
            kc_upper=106.0, kc_lower=99.0, rsi=52.0, atr=2.0,
            volume=1000.0, volume_sma=1000.0, is_volatility_compressed=True,
            is_chop=True, is_trend=False,
        )
    }
    closed_tp1 = engine.manage_positions_on_bar(1700032400000, bars_tp1, regimes_tp1)
    assert len(closed_tp1) == 1
    tp1 = closed_tp1[0]
    assert tp1.exit_reason == "TP1"
    assert tp1.qty == Decimal("5.0")
    assert engine.open_positions["ETHUSDT"].current_qty == Decimal("5.0")
    assert engine.open_positions["ETHUSDT"].tp1_done is True

    # 4. Settlement 2: Negative funding rate (-0.04% -> Long receives) on remaining 5.0 qty
    neg_funding_rate = Decimal("-0.0004")
    mark_2 = Decimal("103.0")
    engine.process_funding(1700057600000, neg_funding_rate, {"ETHUSDT": mark_2})
    expected_cf2 = -1 * Decimal("1") * Decimal("5.0") * mark_2 * neg_funding_rate
    assert len(engine.funding_cashflows) == 2

    # 5. Bar reaches Full Take Profit (High >= 105.0)
    bars_tp2 = {
        "ETHUSDT": {
            "open": 104.0,
            "high": 105.5,
            "low": 103.5,
            "close": 105.0,
            "volume": 1000.0,
            "volume_sma": 1000.0,
        }
    }
    closed_tp2 = engine.manage_positions_on_bar(1700061200000, bars_tp2, regimes_tp1)
    assert len(closed_tp2) == 1
    tp2 = closed_tp2[0]
    assert tp2.exit_reason == "TP"
    assert "ETHUSDT" not in engine.open_positions

    # 6. PENNY-PERFECT INVARIANT VALIDATION
    total_closed = engine.closed_trades
    assert len(total_closed) == 2

    sum_net_pnl = sum(t.net_pnl for t in total_closed)
    sum_gross_pnl = sum(t.qty * (t.exit_price - t.entry_price) for t in total_closed)
    sum_fees = entry_fee + sum(t.qty * t.exit_price * engine.maker_fee for t in total_closed)
    sum_funding_cf = sum(Decimal(cf["cashflow_amount"]) for cf in engine.funding_cashflows)

    delta_realized_equity = engine.realized_equity - init_cap

    # Invariant 1: Delta Equity == Sum Net PnL
    assert abs(delta_realized_equity - sum_net_pnl) < Decimal("0.0000001"), (
        f"Mismatch: delta_eq={delta_realized_equity} vs sum_net={sum_net_pnl}"
    )

    # Invariant 2: Sum Net PnL == Sum Gross - Sum Fees + Sum Funding
    expected_net = sum_gross_pnl - sum_fees + sum_funding_cf
    assert abs(sum_net_pnl - expected_net) < Decimal("0.0000001"), (
        f"Mismatch: sum_net={sum_net_pnl} vs expected_net={expected_net}"
    )

    # Invariant 3: Ending Equity == Initial Capital + Sum Net PnL
    assert abs(engine.realized_equity - (init_cap + sum_net_pnl)) < Decimal("0.0000001")

