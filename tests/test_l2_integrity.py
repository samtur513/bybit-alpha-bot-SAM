"""
Unit tests for Bybit V5 L2 Orderbook sequence integrity, gap detection, and stale watchdog.
FLEET-PAPER-GATE-006 Step 1.F.
"""

from decimal import Decimal
import time
import pytest

from tools.bybit_public_market_collector import L2Orderbook


def test_l2_orderbook_snapshot_and_delta_continuity():
    ob = L2Orderbook(symbol="BTCUSDT")
    now_ms = int(time.time() * 1000)

    # 1. Snapshot
    snap_data = {
        "s": "BTCUSDT",
        "b": [["60000.0", "1.5"], ["59990.0", "3.0"]],
        "a": [["60010.0", "2.0"], ["60020.0", "4.0"]],
        "u": 1000,
        "seq": 500000,
    }
    ob.apply_snapshot(snap_data, now_ms)
    assert ob.is_synchronized is True
    assert ob.last_u == 1000
    assert ob.last_seq == 500000
    assert ob.get_bbo() == (60000.0, 60010.0)
    assert ob.is_valid_for_execution() is True

    # 2. Contiguous delta (u = 1001, seq = 500001)
    delta1 = {
        "s": "BTCUSDT",
        "b": [["60005.0", "1.0"]],
        "a": [],
        "u": 1001,
        "seq": 500001,
    }
    applied = ob.apply_delta(delta1, now_ms + 20)
    assert applied is True
    assert ob.is_synchronized is True
    assert ob.last_u == 1001
    assert ob.last_seq == 500001
    assert ob.get_bbo() == (60005.0, 60010.0)
    assert ob.is_valid_for_execution() is True


def test_l2_orderbook_gap_detection_blocks_execution():
    ob = L2Orderbook(symbol="BTCUSDT")
    now_ms = int(time.time() * 1000)

    # Snapshot
    ob.apply_snapshot({"s": "BTCUSDT", "b": [["60000.0", "1.0"]], "a": [["60010.0", "1.0"]], "u": 2000, "seq": 100}, now_ms)
    assert ob.is_synchronized is True

    # Delta with packet loss (u jumps to 2003 instead of 2001)
    delta_gap = {"s": "BTCUSDT", "b": [["60001.0", "1.0"]], "a": [], "u": 2003, "seq": 105}
    applied = ob.apply_delta(delta_gap, now_ms + 50)
    assert applied is False
    assert ob.is_synchronized is False
    assert ob.gaps_detected == 1
    assert ob.is_valid_for_execution() is False  # Paper execution blocked!

    # Further deltas rejected while unsynchronized
    delta_next = {"s": "BTCUSDT", "b": [], "a": [["60009.0", "1.0"]], "u": 2004, "seq": 106}
    assert ob.apply_delta(delta_next, now_ms + 100) is False
    assert ob.is_valid_for_execution() is False

    # Fresh snapshot resynchronizes the book
    ob.apply_snapshot({"s": "BTCUSDT", "b": [["60002.0", "2.0"]], "a": [["60008.0", "2.0"]], "u": 2010, "seq": 120}, now_ms + 150)
    assert ob.is_synchronized is True
    assert ob.is_valid_for_execution() is True


def test_l2_orderbook_seq_monotonicity_violation():
    ob = L2Orderbook(symbol="BTCUSDT")
    now_ms = int(time.time() * 1000)

    # Snapshot
    ob.apply_snapshot({"s": "BTCUSDT", "b": [["60000.0", "1.0"]], "a": [["60010.0", "1.0"]], "u": 3000, "seq": 500}, now_ms)

    # Delta with non-monotonic seq (seq=490 <= last_seq=500)
    delta_bad_seq = {"s": "BTCUSDT", "b": [["60001.0", "1.0"]], "a": [], "u": 3001, "seq": 490}
    applied = ob.apply_delta(delta_bad_seq, now_ms + 20)
    assert applied is False
    assert ob.is_synchronized is False
    assert ob.is_valid_for_execution() is False


def test_l2_orderbook_stale_watchdog():
    ob = L2Orderbook(symbol="BTCUSDT")
    past_ms = int(time.time() * 1000) - 10000  # 10s ago

    ob.apply_snapshot({"s": "BTCUSDT", "b": [["60000.0", "1.0"]], "a": [["60010.0", "1.0"]], "u": 4000, "seq": 700}, past_ms)
    assert ob.is_synchronized is True
    # Stale watchdog check with threshold 5000 ms
    assert ob.is_valid_for_execution(max_stale_ms=5000) is False


def test_l2_orderbook_virtual_replay_clock():
    """
    Verifies that when replaying historical market data, stale-guard operates
    strictly on the virtual replay clock (current_ts_ms), decoupled from host wall clock:
    1. Historical event at t=1720000000000 is NOT flagged stale when replay clock is at 1720000002000 (delta=2s <= 5s).
    2. Historical event at t=1720000000000 IS flagged stale when replay clock advances to 1720000007000 (delta=7s > 5s).
    """
    ob = L2Orderbook(symbol="BTCUSDT")
    hist_update_ts = 1720000000000  # Historical timestamp from 2024

    ob.apply_snapshot({
        "s": "BTCUSDT",
        "b": [["60000.0", "1.0"]],
        "a": [["60010.0", "1.0"]],
        "u": 5000,
        "seq": 800,
    }, hist_update_ts)
    assert ob.is_synchronized is True

    # 1. Replay clock 2000 ms later: Valid!
    replay_ts_valid = hist_update_ts + 2000
    assert ob.is_valid_for_execution(max_stale_ms=5000, current_ts_ms=replay_ts_valid) is True

    # 2. Replay clock 7000 ms later: Blocked as stale!
    replay_ts_stale = hist_update_ts + 7000
    assert ob.is_valid_for_execution(max_stale_ms=5000, current_ts_ms=replay_ts_stale) is False
