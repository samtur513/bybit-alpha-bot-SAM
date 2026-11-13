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
