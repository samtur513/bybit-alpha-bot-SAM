"""
Comprehensive Unit & Invariant Test Suite for L2 Orderbook Integrity.
Stage 1 Audit Deliverable for FLEET-CAPTURE-RELEASE-008A.

Covers:
1. Normal spread (best_bid < best_ask, spread > 0).
2. Locked spread (best_bid == best_ask, spread == 0).
3. Crossed spread (best_bid > best_ask, spread < 0).
4. Missing / empty side (bids empty, asks empty, or both empty).
5. Stale orderbook timestamp gate (current_ts - last_update_ts > max_stale_ms).
6. Sequence gap detection (u != last_u + 1).
7. Monotonicity sequence violation (seq <= last_seq).
8. Delta arrival prior to snapshot rejection (not is_synchronized).
9. Re-synchronization on subsequent snapshot after gap.
10. Correct deletion of price levels when size is 0.
"""

import sys
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from tools.bybit_public_market_collector import L2Orderbook


def test_normal_orderbook_positive_spread():
    """Confirms that a normal orderbook with best_bid < best_ask yields strictly positive spread."""
    ob = L2Orderbook(symbol="BTCUSDT")
    snapshot_data = {
        "s": "BTCUSDT",
        "b": [["60000.0", "1.5"], ["59990.0", "2.0"]],
        "a": [["60010.0", "1.2"], ["60020.0", "3.0"]],
        "u": 100,
        "seq": 1000,
    }
    ob.apply_snapshot(snapshot_data, ts=1720000000000)

    assert ob.is_synchronized is True
    best_bid, best_ask = ob.get_bbo()
    assert best_bid == 60000.0
    assert best_ask == 60010.0
    assert best_bid < best_ask
    
    spread = best_ask - best_bid
    assert spread == 10.0
    assert spread > 0.0

    # Must be valid for execution
    assert ob.is_valid_for_execution(max_stale_ms=5000, current_ts_ms=1720000001000) is True


def test_locked_orderbook_zero_spread():
    """Confirms that best_bid == best_ask (locked market, spread == 0) is rejected as INVALID."""
    ob = L2Orderbook(symbol="BTCUSDT")
    snapshot_data = {
        "s": "BTCUSDT",
        "b": [["60000.0", "1.5"]],
        "a": [["60000.0", "1.2"]],  # Same price!
        "u": 100,
        "seq": 1000,
    }
    ob.apply_snapshot(snapshot_data, ts=1720000000000)

    best_bid, best_ask = ob.get_bbo()
    assert best_bid == 60000.0
    assert best_ask == 60000.0
    assert (best_ask - best_bid) == 0.0

    # Must fail execution gate
    assert ob.is_valid_for_execution(max_stale_ms=5000, current_ts_ms=1720000001000) is False


def test_crossed_orderbook_negative_spread():
    """Confirms that best_bid > best_ask (crossed market, spread < 0) is rejected as INVALID."""
    ob = L2Orderbook(symbol="BTCUSDT")
    snapshot_data = {
        "s": "BTCUSDT",
        "b": [["60050.0", "1.5"]],  # Bid higher than ask!
        "a": [["60000.0", "1.2"]],
        "u": 100,
        "seq": 1000,
    }
    ob.apply_snapshot(snapshot_data, ts=1720000000000)

    best_bid, best_ask = ob.get_bbo()
    assert best_bid == 60050.0
    assert best_ask == 60000.0
    assert best_bid > best_ask
    assert (best_ask - best_bid) < 0.0

    # Must fail execution gate
    assert ob.is_valid_for_execution(max_stale_ms=5000, current_ts_ms=1720000001000) is False


def test_missing_side_spread():
    """Confirms that an orderbook with empty bids or asks is rejected as INVALID."""
    ob = L2Orderbook(symbol="BTCUSDT")
    
    # 1. Completely empty
    assert ob.is_valid_for_execution(current_ts_ms=1720000000000) is False

    # 2. Bids only
    snapshot_bids_only = {
        "s": "BTCUSDT",
        "b": [["60000.0", "1.0"]],
        "a": [],
        "u": 1,
        "seq": 1,
    }
    ob.apply_snapshot(snapshot_bids_only, ts=1720000000000)
    best_bid, best_ask = ob.get_bbo()
    assert best_bid == 60000.0
    assert best_ask is None
    assert ob.is_valid_for_execution(current_ts_ms=1720000000000) is False

    # 3. Asks only
    snapshot_asks_only = {
        "s": "BTCUSDT",
        "b": [],
        "a": [["60010.0", "1.0"]],
        "u": 2,
        "seq": 2,
    }
    ob.apply_snapshot(snapshot_asks_only, ts=1720000000000)
    best_bid, best_ask = ob.get_bbo()
    assert best_bid is None
    assert best_ask == 60010.0
    assert ob.is_valid_for_execution(current_ts_ms=1720000000000) is False


def test_stale_orderbook_rejection():
    """Confirms that an orderbook exceeding max_stale_ms is rejected."""
    ob = L2Orderbook(symbol="BTCUSDT")
    ob.apply_snapshot({
        "s": "BTCUSDT",
        "b": [["60000.0", "1.0"]],
        "a": [["60010.0", "1.0"]],
        "u": 100,
        "seq": 1000,
    }, ts=1720000000000)

    # Within 5000ms -> Valid
    assert ob.is_valid_for_execution(max_stale_ms=5000, current_ts_ms=1720000004999) is True

    # Exactly 5000ms -> Valid
    assert ob.is_valid_for_execution(max_stale_ms=5000, current_ts_ms=1720000005000) is True

    # Stale (> 5000ms) -> Invalid
    assert ob.is_valid_for_execution(max_stale_ms=5000, current_ts_ms=1720000005001) is False


def test_sequence_gap_detection_and_invalidation():
    """Confirms that a sequence gap (u != last_u + 1) invalidates book until re-snapshot."""
    ob = L2Orderbook(symbol="BTCUSDT")
    ob.apply_snapshot({
        "s": "BTCUSDT",
        "b": [["60000.0", "1.0"]],
        "a": [["60010.0", "1.0"]],
        "u": 100,
        "seq": 1000,
    }, ts=1720000000000)
    assert ob.is_synchronized is True

    # Valid delta: u=101, seq=1001
    delta_valid = {
        "b": [["60001.0", "1.5"]],
        "a": [],
        "u": 101,
        "seq": 1001,
    }
    assert ob.apply_delta(delta_valid, ts=1720000000100) is True
    assert ob.is_synchronized is True
    assert ob.last_u == 101

    # GAP delta: expected u=102, incoming u=105!
    delta_gap = {
        "b": [["60002.0", "1.5"]],
        "a": [],
        "u": 105,
        "seq": 1005,
    }
    assert ob.apply_delta(delta_gap, ts=1720000000200) is False
    assert ob.is_synchronized is False
    assert ob.gaps_detected == 1

    # While unsynchronized, execution is blocked
    assert ob.is_valid_for_execution(current_ts_ms=1720000000200) is False

    # Subsequent delta (even if sequential to the gap) must be rejected because not synchronized
    delta_subsequent = {
        "b": [["60003.0", "1.5"]],
        "a": [],
        "u": 106,
        "seq": 1006,
    }
    assert ob.apply_delta(delta_subsequent, ts=1720000000300) is False
    assert ob.is_valid_for_execution(current_ts_ms=1720000000300) is False

    # New snapshot arrives -> Recover synchronization
    ob.apply_snapshot({
        "s": "BTCUSDT",
        "b": [["60005.0", "2.0"]],
        "a": [["60015.0", "2.0"]],
        "u": 120,
        "seq": 1100,
    }, ts=1720000000400)
    assert ob.is_synchronized is True
    assert ob.is_valid_for_execution(current_ts_ms=1720000000400) is True


def test_sequence_monotonicity_violation():
    """Confirms that non-monotonic seq (seq <= last_seq) invalidates book."""
    ob = L2Orderbook(symbol="BTCUSDT")
    ob.apply_snapshot({
        "s": "BTCUSDT",
        "b": [["60000.0", "1.0"]],
        "a": [["60010.0", "1.0"]],
        "u": 100,
        "seq": 1000,
    }, ts=1720000000000)

    # Monotonicity violation: u is sequential (101), but seq is backwards (999 <= 1000)
    delta_bad_seq = {
        "b": [["60001.0", "1.0"]],
        "a": [],
        "u": 101,
        "seq": 999,
    }
    assert ob.apply_delta(delta_bad_seq, ts=1720000000100) is False
    assert ob.is_synchronized is False
    assert ob.is_valid_for_execution(current_ts_ms=1720000000100) is False


def test_delta_before_snapshot_rejected():
    """Confirms that delta arriving before initial snapshot is rejected."""
    ob = L2Orderbook(symbol="BTCUSDT")
    assert ob.is_synchronized is False

    delta = {
        "b": [["60000.0", "1.0"]],
        "a": [],
        "u": 1,
        "seq": 1,
    }
    assert ob.apply_delta(delta, ts=1720000000000) is False
    assert ob.is_valid_for_execution(current_ts_ms=1720000000000) is False


def test_level_deletion_on_zero_size():
    """Confirms that size == '0' properly deletes price level."""
    ob = L2Orderbook(symbol="BTCUSDT")
    ob.apply_snapshot({
        "s": "BTCUSDT",
        "b": [["60000.0", "1.0"], ["59990.0", "2.0"]],
        "a": [["60010.0", "1.0"], ["60020.0", "2.0"]],
        "u": 100,
        "seq": 1000,
    }, ts=1720000000000)

    # Delete best bid 60000.0
    delta = {
        "b": [["60000.0", "0.0"]],
        "a": [["60010.0", "0"]],
        "u": 101,
        "seq": 1001,
    }
    assert ob.apply_delta(delta, ts=1720000000100) is True
    best_bid, best_ask = ob.get_bbo()
    assert best_bid == 59990.0  # Level 60000.0 removed!
    assert best_ask == 60020.0  # Level 60010.0 removed!
    assert ob.is_valid_for_execution(current_ts_ms=1720000000100) is True
