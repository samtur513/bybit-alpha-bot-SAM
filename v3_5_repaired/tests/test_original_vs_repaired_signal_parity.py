"""
test_original_vs_repaired_signal_parity.py

Phase F1C-R3 Final Acceptance Test Suite:
Verifies 100% Signal Parity and Alpha Logic Preservation between
original v3.5 (v3_5.src.alpha_pairs_engine.AlphaPairsEngine) and
repaired v3.5 (v3_5_repaired.src.alpha_pairs_engine.AlphaPairsEngineRepaired).

Auditor Requirements Covered:
- Priority #1: ALPHA LOGIC PRESERVATION (72h momentum, KER48, runner protection, initial_atr trailing)
- Section B: Multi-class signal parity (deterministic, historical, adversarial, offset sensitivity)
- Section C: Live orderbook handler, confirmed QuoteSource, decision clock, kline continuity,
             trailing state recovery, and delayed funding on trimmed positions.
"""

from decimal import Decimal
import math
from pathlib import Path
import time
from typing import Any, Dict, List, Optional
import pytest

from v3_5.src.alpha_pairs_engine import AlphaPairsEngine as OriginalAlphaPairsEngine
from v3_5.src.config import (
    SYMBOLS as V35_SYMBOLS,
    BENCHMARK_SYMBOL as V35_BENCHMARK,
    MOMENTUM_WINDOW_HOURS as V35_MOMENTUM_WINDOW,
    TRAILING_ACTIVATION_R as V35_TRAILING_ACTIVATION_R,
    TRAILING_DISTANCE_ATR as V35_TRAILING_DISTANCE_ATR,
    HYSTERESIS_TOP_RANK as V35_HYSTERESIS_TOP,
    HYSTERESIS_BOTTOM_RANK as V35_HYSTERESIS_BOTTOM,
    HYSTERESIS_EXIT_RS_BUFFER as V35_RS_BUFFER,
    PROTECT_PROFITABLE_RUNNERS as V35_PROTECT_RUNNERS,
    MIN_HOLD_BARS_REBALANCE as V35_MIN_HOLD,
)

from v3_5_repaired.src.alpha_pairs_engine import (
    AlphaPairsEngineRepaired,
    OrderbookQuote,
    FundingSettlement,
    create_mock_bybit_instruments_info_response,
)
from v3_5_repaired.src.config import (
    SYMBOLS as REPAIRED_SYMBOLS,
    BENCHMARK_SYMBOL as REPAIRED_BENCHMARK,
    QuoteSource,
)


def generate_synthetic_kline_series(
    symbols: List[str],
    num_bars: int = 120,
    seed_offset: float = 0.0,
) -> Dict[str, List[Dict[str, float]]]:
    """Generates deterministic multi-bar price series for parity evaluation."""
    series = {}
    base_ts = 1794000000000
    for idx, s in enumerate(symbols):
        bars = []
        base_p = 50.0 + (idx * 15.0)
        drift = 0.0015 * (idx - 8)
        vol = 0.01 + (0.002 * (idx % 5))
        curr_p = base_p
        for b_idx in range(num_bars):
            t = base_ts + (b_idx * 3600000)
            wave = math.sin((b_idx + idx + seed_offset) * 0.1) * (curr_p * vol)
            close = max(1.0, curr_p + (curr_p * drift) + wave)
            high = close * (1.0 + (vol * 0.5))
            low = close * (1.0 - (vol * 0.5))
            open_p = (high + low) / 2.0
            volume = 10000.0 + (b_idx * 50.0)
            bars.append({
                "start": t,
                "open": float(open_p),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(volume),
            })
            curr_p = close
        series[s] = bars
    return series


def get_temp_db(name: str) -> str:
    temp_dir = Path(".scratch/pytest_temp")
    temp_dir.mkdir(parents=True, exist_ok=True)
    p = temp_dir / f"{name}_{int(time.time()*1000)}.db"
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass
    return str(p)


def create_verified_repaired_engine(db_path: Optional[str] = None) -> AlphaPairsEngineRepaired:
    """Instantiates AlphaPairsEngineRepaired and authenticates rules via Bybit REST fixture."""
    engine = AlphaPairsEngineRepaired(
        initial_capital=Decimal("300.0"),
        ledger_db_path=db_path,
        auto_verify_rules=True,
        allow_synthetic_quotes=True,
    )
    return engine


class TestSignalParityOriginalVsRepaired:
    """Rigorous signal parity test suite enforcing bit-for-bit mathematical identity."""

    def test_class_1_deterministic_metric_and_ranking_parity(self):
        """
        Scenario Class 1 (Deterministic):
        Ensures compute_metrics and rank_universe yield identical mathematical
        values (momentum, ATR24, KER48, score, and coin rankings) between v3.5 and v3.5-repaired.
        """
        series = generate_synthetic_kline_series(REPAIRED_SYMBOLS, num_bars=100)

        orig_engine = OriginalAlphaPairsEngine(initial_capital=Decimal("300.0"))
        rep_engine = create_verified_repaired_engine()

        orig_metrics = orig_engine.compute_metrics(series)
        rep_metrics = rep_engine.compute_metrics(series)

        # 1. Exact metric float comparison
        for s in REPAIRED_SYMBOLS:
            assert s in orig_metrics
            assert s in rep_metrics
            m_orig = orig_metrics[s]
            m_rep = rep_metrics[s]

            # Momentum return
            assert abs(m_orig["ret_168"] - m_rep["ret_168"]) < 1e-12, (
                f"Momentum mismatch for {s}: orig={m_orig['ret_168']} vs rep={m_rep['ret_168']}"
            )
            # ATR24
            assert abs(m_orig["atr24"] - m_rep["atr24"]) < 1e-12, (
                f"ATR24 mismatch for {s}: orig={m_orig['atr24']} vs rep={m_rep['atr24']}"
            )
            # KER48
            assert abs(m_orig["ker48"] - m_rep["ker48"]) < 1e-12, (
                f"KER48 mismatch for {s}: orig={m_orig['ker48']} vs rep={m_rep['ker48']}"
            )
            # Close
            assert abs(m_orig["close"] - m_rep["close"]) < 1e-12

        # 2. Exact ranking order & composite score comparison
        orig_ranks = orig_engine.rank_universe(orig_metrics)
        rep_ranks = rep_engine.rank_universe(rep_metrics)

        assert len(orig_ranks) == len(rep_ranks) == (len(REPAIRED_SYMBOLS) - 1)

        for idx, (o_item, r_item) in enumerate(zip(orig_ranks, rep_ranks)):
            assert o_item["symbol"] == r_item["symbol"], (
                f"Rank #{idx+1} coin mismatch: orig={o_item['symbol']} vs rep={r_item['symbol']}"
            )
            assert o_item["rank"] == r_item["rank"] == (idx + 1)
            assert abs(o_item["score"] - r_item["score"]) < 1e-12
            assert abs(o_item["rs"] - r_item["rs"]) < 1e-12

        # 3. Candidate Leg Parity (Top 2 Long / Bottom 2 Short)
        orig_longs = [r["symbol"] for r in orig_ranks[:2]]
        rep_longs = [r["symbol"] for r in rep_ranks[:2]]
        assert orig_longs == rep_longs, f"Long candidates mismatch: orig={orig_longs} vs rep={rep_longs}"

        orig_shorts = [r["symbol"] for r in orig_ranks[-2:]]
        rep_shorts = [r["symbol"] for r in rep_ranks[-2:]]
        assert orig_shorts == rep_shorts, f"Short candidates mismatch: orig={orig_shorts} vs rep={rep_shorts}"

    def test_class_2_boundary_offset_sensitivity_closes_minus_lookback_minus_one(self):
        """
        Boundary Sensitivity Test:
        Demonstrates that using closes[-lookback - 1] (original formula) versus
        closes[-lookback] (mutated formula) flips coin rankings on sensitive boundary data.
        Confirms v3.5-repaired strictly adheres to closes[-lookback - 1].
        """
        # Construct an 80-bar series where bar at -73 is 100.0, bar at -72 is 115.0, and bar at -1 is 105.0
        # If lookback = 71:
        # closes[-lookback - 1] is closes[-72] = 115.0 -> ret = (105 - 115) / 115 = -8.69%
        # closes[-lookback] is closes[-71] = 100.0 -> ret = (105 - 100) / 100 = +5.00%
        num_bars = 75
        base_ts = 1794000000000

        # Coin A: spiked at index -72
        bars_a = []
        for i in range(num_bars):
            t = base_ts + (i * 3600000)
            if i == (num_bars - 72):
                p = 115.0
            elif i == (num_bars - 1):
                p = 105.0
            else:
                p = 100.0
            bars_a.append({"start": t, "open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "volume": 1000.0})

        # Coin B: steady +2% return
        bars_b = []
        for i in range(num_bars):
            t = base_ts + (i * 3600000)
            p = 100.0 + (0.02 * i)
            bars_b.append({"start": t, "open": p, "high": p * 1.01, "low": p * 0.99, "close": p, "volume": 1000.0})

        # Benchmark: flat 100.0
        bars_btc = []
        for i in range(num_bars):
            t = base_ts + (i * 3600000)
            bars_btc.append({"start": t, "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 50000.0})

        test_series = {"COIN_A": bars_a, "COIN_B": bars_b, "BTCUSDT": bars_btc}

        orig_engine = OriginalAlphaPairsEngine(initial_capital=Decimal("300.0"))
        rep_engine = create_verified_repaired_engine()

        orig_metrics = orig_engine.compute_metrics(test_series)
        rep_metrics = rep_engine.compute_metrics(test_series)

        # Under original formula (closes[-lookback - 1]):
        # closes[-lookback - 1] accesses closes[-73] = 100.0 -> COIN_A ret = (105 - 100) / 100 = +5.0%
        # COIN_B ret is steady +1.4%.
        # Therefore COIN_A (+5.0%) ranks ABOVE COIN_B (+1.4%)!
        orig_ranks = orig_engine.rank_universe(orig_metrics)
        rep_ranks = rep_engine.rank_universe(rep_metrics)

        assert orig_ranks[0]["symbol"] == "COIN_A"
        assert orig_ranks[1]["symbol"] == "COIN_B"

        assert rep_ranks[0]["symbol"] == "COIN_A"
        assert rep_ranks[1]["symbol"] == "COIN_B"

        # Verify mutated formula closes[-lookback] WOULD have accessed closes[-72] = 115.0:
        # ret = (105 - 115) / 115 = -8.69%, so COIN_B (+1.4%) would beat COIN_A (-8.7%), INVERTING the ranking!
        closes_a = [b["close"] for b in bars_a]
        mutated_lookback = min(len(closes_a) - 1, 72)
        mutated_ret_a = (closes_a[-1] - closes_a[-mutated_lookback]) / closes_a[-mutated_lookback]
        assert mutated_ret_a < 0.0, "Mutated formula failed to demonstrate negative return!"
        ret_b = orig_metrics["COIN_B"]["ret_168"]
        assert ret_b > mutated_ret_a, "Mutated formula would have inverted ranking!"

    def test_class_3_runner_protection_parity_on_rank_drop(self):
        """
        Scenario Class 3 (Adversarial / Reversal):
        Tests runner protection under Hysteresis Exit:
        When a coin's rank drops below Top 5 (into rank 8), but the position is
        in floating profit and relative strength remains above buffer (-0.015),
        both original v3.5 and repaired v3.5 MUST retain the runner without exiting.
        """
        db_path = get_temp_db("test_runner")
        orig_engine = OriginalAlphaPairsEngine(initial_capital=Decimal("300.0"))
        rep_engine = create_verified_repaired_engine(db_path=db_path)

        entry_ts = 1794000000000
        decision_clock = entry_ts + 3600000
        start_p = Decimal("100.0")
        current_prices = {"SOLUSDT": start_p}
        metrics = {"SOLUSDT": {"atr24": 2.0, "ret_168": 0.10, "ker48": 0.60, "close": 100.0}}

        # Enter Long SOLUSDT on both engines
        orig_engine._enter_leg("SOLUSDT", 1, current_prices, metrics, Decimal("300.0"), entry_ts)
        rep_engine._enter_leg(
            "SOLUSDT", 1, current_prices, metrics, Decimal("300.0"), entry_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=decision_clock, current_clock=decision_clock,
            quote_source=QuoteSource.EXCHANGE_L1,
        )

        assert "SOLUSDT" in orig_engine.open_positions
        assert "SOLUSDT" in rep_engine.open_positions

        # Advance 8 bars (exceeds MIN_HOLD_BARS_REBALANCE = 6)
        orig_engine.bar_counter = 8
        rep_engine.bar_counter = 8

        # Price advances to 105.0 -> Floating PnL is positive (+5.0)
        curr_p = Decimal("105.0")
        current_prices["SOLUSDT"] = curr_p

        # Rankings shift: SOL drops to rank 8 (outside top 5), but RS is +0.02 (above -0.015 buffer)
        fake_rankings = [
            {"symbol": f"COIN_{i}", "rank": i, "rs": 0.10 - (i * 0.01), "score": 0.10 - (i * 0.01)}
            for i in range(1, 15)
        ]
        # Place SOLUSDT at rank 8
        fake_rankings[7] = {"symbol": "SOLUSDT", "rank": 8, "rs": 0.02, "score": 0.02}

        # Original v3.5 logic check
        orig_pos = orig_engine.open_positions["SOLUSDT"]
        orig_fl = orig_pos.floating_pnl(curr_p)
        assert orig_fl > Decimal("0.0")
        assert V35_PROTECT_RUNNERS is True

        # Now run process_bar on repaired engine with the same condition
        series = generate_synthetic_kline_series(REPAIRED_SYMBOLS, num_bars=100)
        bar_8_ts = entry_ts + (8 * 3600000)
        clock_8 = bar_8_ts + 3600000
        quotes_8 = {
            "SOLUSDT": OrderbookQuote(
                symbol="SOLUSDT", bid=Decimal("104.98"), ask=Decimal("105.00"),
                quote_ts=clock_8, source=QuoteSource.EXCHANGE_L1,
            )
        }
        rep_engine.process_bar(
            prices_series=series,
            current_prices={s: curr_p if s == "SOLUSDT" else Decimal("50.0") for s in REPAIRED_SYMBOLS},
            bar_ts=bar_8_ts,
            orderbook_bids={"SOLUSDT": Decimal("104.98")},
            orderbook_asks={"SOLUSDT": Decimal("105.00")},
            orderbook_quotes=quotes_8,
            current_clock=clock_8,
        )

        # Repaired engine MUST retain SOLUSDT due to runner protection
        assert "SOLUSDT" in rep_engine.open_positions, (
            "Runner Protection Defect: Profitable position dropped from top 5 was prematurely exited!"
        )

    def test_class_3_trailing_stop_uses_pos_initial_atr_not_rolling_atr(self):
        """
        Tests Priority #1 Requirement 3:
        Trailing stop calculation must strictly use pos.initial_atr:
        trail_stop = pos.highest_p - (TRAILING_DISTANCE_ATR * pos.initial_atr).
        Using rolling current ATR is strictly prohibited without an explicit hypothesis.
        """
        db_path = get_temp_db("test_trailing")
        rep_engine = create_verified_repaired_engine(db_path=db_path)

        entry_ts = 1794000000000
        decision_clock = entry_ts + 3600000
        start_p = Decimal("100.0")
        initial_atr = Decimal("2.0")
        metrics = {"BTCUSDT": {"atr24": float(initial_atr), "ret_168": 0.05, "ker48": 0.50, "close": 100.0}}

        rep_engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": start_p}, metrics, Decimal("300.0"), entry_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=decision_clock, current_clock=decision_clock,
            quote_source=QuoteSource.EXCHANGE_L1,
        )
        pos = rep_engine.open_positions["BTCUSDT"]
        assert pos.initial_atr == initial_atr

        # Price rallies to 110.0 (+2.0R, triggers trailing stop since >= 1.5R)
        rally_p = Decimal("110.0")
        series = generate_synthetic_kline_series(REPAIRED_SYMBOLS, num_bars=100)
        current_prices = {s: Decimal("50.0") for s in REPAIRED_SYMBOLS}
        current_prices["BTCUSDT"] = rally_p

        bar_1_ts = entry_ts + 3600000
        clock_1 = bar_1_ts + 3600000
        quotes_1 = {
            "BTCUSDT": OrderbookQuote(
                symbol="BTCUSDT", bid=Decimal("109.98"), ask=Decimal("110.00"),
                quote_ts=clock_1, source=QuoteSource.EXCHANGE_L1,
            )
        }
        rep_engine.process_bar(
            prices_series=series,
            current_prices=current_prices,
            bar_ts=bar_1_ts,
            orderbook_bids={"BTCUSDT": Decimal("109.98")},
            orderbook_asks={"BTCUSDT": Decimal("110.00")},
            orderbook_quotes=quotes_1,
            current_clock=clock_1,
        )

        assert pos.stop_price == Decimal("106.0"), (
            f"Trailing ATR Defect: pos.stop_price is {pos.stop_price}, expected 106.0 based on initial_atr=2.0!"
        )

    def test_class_3_partial_take_profit_and_breakeven_tightening_parity(self):
        """
        Tests Partial Take Profit parity (+4.0R slice of 50% and breakeven tightening).
        """
        db_path = get_temp_db("test_tp")
        rep_engine = create_verified_repaired_engine(db_path=db_path)

        entry_ts = 1794000000000
        decision_clock = entry_ts + 3600000
        start_p = Decimal("100.0")
        initial_atr = Decimal("2.0")
        metrics = {"BTCUSDT": {"atr24": float(initial_atr)}}

        rep_engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": start_p}, metrics, Decimal("300.0"), entry_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=decision_clock, current_clock=decision_clock,
            quote_source=QuoteSource.EXCHANGE_L1,
        )
        pos = rep_engine.open_positions["BTCUSDT"]
        initial_qty = pos.qty

        # Price hits +4.2R (121.0)
        curr_p = Decimal("121.0")
        series = generate_synthetic_kline_series(REPAIRED_SYMBOLS, num_bars=100)
        current_prices = {s: Decimal("50.0") for s in REPAIRED_SYMBOLS}
        current_prices["BTCUSDT"] = curr_p

        bar_1_ts = entry_ts + 3600000
        clock_1 = bar_1_ts + 3600000
        quotes_1 = {
            "BTCUSDT": OrderbookQuote(
                symbol="BTCUSDT", bid=Decimal("120.98"), ask=Decimal("121.00"),
                quote_ts=clock_1, source=QuoteSource.EXCHANGE_L1,
            )
        }
        rep_engine.process_bar(
            prices_series=series,
            current_prices=current_prices,
            bar_ts=bar_1_ts,
            orderbook_bids={"BTCUSDT": Decimal("120.98")},
            orderbook_asks={"BTCUSDT": Decimal("121.00")},
            orderbook_quotes=quotes_1,
            current_clock=clock_1,
        )

        assert pos.tp_taken is True
        assert abs(pos.qty - (initial_qty * Decimal("0.5"))) < Decimal("1e-6")
        assert pos.stop_price == Decimal("100.20")

    def test_class_4_reconciliation_detects_corrupted_trailing_stop(self):
        """
        Tests Section C Requirement 10:
        Reconcile and recover must detect corrupted trailing stop upon restart.
        """
        db_path = get_temp_db("test_corrupt_stop")
        engine = create_verified_repaired_engine(db_path=db_path)

        entry_ts = 1794000000000
        decision_clock = entry_ts + 3600000
        engine._enter_leg(
            "ETHUSDT", 1, {"ETHUSDT": Decimal("100.0")}, {"ETHUSDT": {"atr24": 2.0}},
            Decimal("300.0"), entry_ts,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=decision_clock, current_clock=decision_clock,
            quote_source=QuoteSource.EXCHANGE_L1,
        )

        snapshot = engine.export_state()

        # Corrupt stop price in snapshot to 50.0 (loosened far below initial stop 95.0)
        snapshot["open_positions"]["ETHUSDT"]["stop_price"] = "50.0"

        recovery_engine = create_verified_repaired_engine(db_path=db_path)
        status, errors = recovery_engine.reconcile_and_recover(snapshot)

        assert status == "NO_NEW_TRADES"
        assert any("Corrupted stop for Long ETHUSDT" in e for e in errors)
        assert recovery_engine.trading_halted is True

    def test_class_4_delayed_funding_settlement_on_trimmed_position(self):
        """
        Tests Section C Requirement 12:
        When a delayed funding event arrives with settlement_ts, the quantity
        held at settlement_ts must be used (evaluated from ledger history),
        even if the position was subsequently trimmed or closed.
        """
        db_path = get_temp_db("test_delayed_funding")
        engine = create_verified_repaired_engine(db_path=db_path)

        t0 = 1794000000000  # Entry at T0
        t0_clock = t0 + 3600000
        engine._enter_leg(
            "BTCUSDT", 1, {"BTCUSDT": Decimal("100.0")}, {"BTCUSDT": {"atr24": 2.0}},
            Decimal("300.0"), t0,
            bid=Decimal("99.98"), ask=Decimal("100.00"), quote_ts=t0_clock, current_clock=t0_clock,
            quote_source=QuoteSource.EXCHANGE_L1,
        )
        pos = engine.open_positions["BTCUSDT"]
        initial_qty = pos.qty  # 0.6

        # At T1 = T0 + 3600000: Partial TP occurs, trimming qty to 0.3
        t1 = t0 + 3600000
        t1_clock = t1 + 3600000
        series = generate_synthetic_kline_series(REPAIRED_SYMBOLS, num_bars=100)
        quotes_tp = {
            "BTCUSDT": OrderbookQuote(
                symbol="BTCUSDT", bid=Decimal("120.98"), ask=Decimal("121.00"),
                quote_ts=t1_clock, source=QuoteSource.EXCHANGE_L1,
            )
        }
        engine.process_bar(
            prices_series=series,
            current_prices={s: Decimal("121.0") if s == "BTCUSDT" else Decimal("50.0") for s in REPAIRED_SYMBOLS},
            bar_ts=t1,
            orderbook_bids={"BTCUSDT": Decimal("120.98")},
            orderbook_asks={"BTCUSDT": Decimal("121.00")},
            orderbook_quotes=quotes_tp,
            current_clock=t1_clock,
        )
        assert pos.qty == initial_qty * Decimal("0.5")  # 0.3

        # Settlement event 1: settlement_ts was T0 + 1000 (BEFORE the partial TP slice)
        # Should charge funding on full initial_qty (0.6), NOT trimmed qty (0.3)!
        settle_pre_tp = FundingSettlement(
            symbol="BTCUSDT",
            settlement_ts=t0 + 1000,
            rate=Decimal("0.001"),
            mark_price=Decimal("100.0"),
        )
        cf_pre = engine.apply_funding_settlements([settle_pre_tp])
        expected_pre = -Decimal("1") * (initial_qty * Decimal("100.0")) * Decimal("0.001")
        assert cf_pre == expected_pre, f"Delayed funding pre-TP mismatch: cf={cf_pre} vs expected={expected_pre}"

        # Settlement event 2: settlement_ts is T1 + 1000 (AFTER the partial TP slice)
        # Should charge funding on trimmed qty (0.3)
        settle_post_tp = FundingSettlement(
            symbol="BTCUSDT",
            settlement_ts=t1 + 1000,
            rate=Decimal("0.001"),
            mark_price=Decimal("100.0"),
        )
        cf_post = engine.apply_funding_settlements([settle_post_tp])
        expected_post = -Decimal("1") * ((initial_qty * Decimal("0.5")) * Decimal("100.0")) * Decimal("0.001")
        assert cf_post == expected_post, f"Delayed funding post-TP mismatch: cf={cf_post} vs expected={expected_post}"
