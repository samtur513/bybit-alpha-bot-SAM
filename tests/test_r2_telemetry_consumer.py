"""
tests/test_r2_telemetry_consumer.py
Unit test suite for CryptoLab R2-SHADOW read-only telemetry consumer.
Protocol: TORG-BOT — THREE-SYSTEM-INTEGRATION-005 / TELEMETRY_CONTRACT.md
"""

import copy
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from v3_2.src.integrations.r2_consumer import (
    R2TelemetryConsumer,
    R2ConsumerResult,
    SCHEMA_VERSION,
    SYSTEM_NAME,
    MARKET_TYPE,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_DIR = REPO_ROOT / "docs" / "integrations" / "r2_shadow"
SCHEMA_PATH = CONTRACT_DIR / "telemetry.schema.json"
EXAMPLE_PATH = CONTRACT_DIR / "telemetry.example.json"


@pytest.fixture
def consumer():
    return R2TelemetryConsumer(schema_path=SCHEMA_PATH)


@pytest.fixture
def example_data():
    return json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))


def test_example_file_validates_successfully(consumer, example_data):
    """Verifies that the canonical telemetry.example.json passes validation."""
    now_dt = datetime.fromisoformat(example_data["generated_at"].replace("Z", "+00:00")) + timedelta(seconds=10)
    res = consumer.parse_and_validate(example_data, now_dt=now_dt)
    
    assert res.is_valid is True
    assert res.r2_base_profile == "R2:300:BASE:OBSERVED_BOOK"
    assert res.cash_usdt == 300.0
    assert res.equity_usdt is None  # Initial example is unvalued
    assert res.valuation_status == "UNVALUED_OR_STALE"
    assert res.real_orders == 0
    assert res.demo_orders == 0


def test_fresh_valuation_parses_metrics_correctly(consumer, example_data):
    """Verifies parsing of active live valuation with edge calculation."""
    now_dt = datetime.now(timezone.utc)
    data = copy.deepcopy(example_data)
    
    data["generated_at"] = now_dt.isoformat()
    data["heartbeat_at"] = (now_dt - timedelta(seconds=5)).isoformat()
    data["process_status"] = "OBSERVING"
    data["deployment_status"] = "STARTED_UNVERIFIED"
    
    # R2 BASE
    data["profiles"][0].update({
        "equity_usdt": 305.25,
        "net_pnl_usdt": 5.25,
        "net_return_pct": 1.75,
        "mdd_pct": -0.50,
        "cash_usdt": 200.0,
        "exposure_pct": 34.5,
        "cash_fraction_pct": 65.5,
        "valuation_at": (now_dt - timedelta(seconds=10)).isoformat(),
        "valuation_status": "FRESH",
        "totals": {
            "fees_usdt": 0.25,
            "buffer_usdt": 0.10,
            "spread_impact_usdt": 0.15,
            "turnover_usdt": 150.0,
        }
    })
    
    # BASELINE T01
    data["profiles"][2].update({
        "equity_usdt": 302.00,
        "net_pnl_usdt": 2.00,
        "net_return_pct": 0.67,
        "mdd_pct": -0.80,
        "cash_usdt": 200.0,
        "valuation_at": (now_dt - timedelta(seconds=10)).isoformat(),
        "valuation_status": "FRESH",
    })
    
    # Comparison
    data["comparison"].update({
        "r2_minus_t01_usdt": 3.25,
        "r2_minus_t01_pp": 1.08,
        "status": "PAIRED_PRELIMINARY",
    })
    
    res = consumer.parse_and_validate(data, now_dt=now_dt)
    assert res.is_valid is True
    assert res.status == "OBSERVING"
    assert res.valuation_status == "FRESH"
    assert res.equity_usdt == 305.25
    assert res.net_pnl_usdt == 5.25
    assert res.net_return_pct == 1.75
    assert res.r2_minus_t01_usdt == 3.25
    assert res.r2_minus_t01_pp == 1.08
    assert res.comparison_status == "PAIRED_PRELIMINARY"
    assert res.fees_usdt == 0.25
    assert res.spread_impact_usdt == 0.15


def test_stale_snapshot_triggers_stale_status(consumer, example_data):
    """Verifies that snapshot older than 45 seconds is marked STALE."""
    now_dt = datetime.now(timezone.utc)
    data = copy.deepcopy(example_data)
    
    # Generated 50 seconds ago (> 45s limit)
    data["generated_at"] = (now_dt - timedelta(seconds=50)).isoformat()
    data["heartbeat_at"] = (now_dt - timedelta(seconds=5)).isoformat()
    data["process_status"] = "OBSERVING"
    
    res = consumer.parse_and_validate(data, now_dt=now_dt)
    assert res.is_valid is True
    assert res.status == "STALE"
    assert "exceeded limit" in res.rejection_reason


def test_stale_valuation_nullifies_equity(consumer, example_data):
    """
    Critical Invariant: If valuation_at is older than 120s, equity_usdt MUST be None.
    Old equity must NEVER be displayed as current equity.
    """
    now_dt = datetime.now(timezone.utc)
    data = copy.deepcopy(example_data)
    
    data["generated_at"] = now_dt.isoformat()
    data["heartbeat_at"] = now_dt.isoformat()
    data["process_status"] = "OBSERVING"
    
    # Valuation is 150 seconds old (> 120s limit)
    data["profiles"][0].update({
        "equity_usdt": 310.0,
        "net_pnl_usdt": 10.0,
        "valuation_at": (now_dt - timedelta(seconds=150)).isoformat(),
        "valuation_status": "FRESH",  # Producer claims fresh, but consumer enforces timestamp check
    })
    
    res = consumer.parse_and_validate(data, now_dt=now_dt)
    assert res.is_valid is True
    assert res.valuation_status == "UNVALUED_OR_STALE"
    assert res.equity_usdt is None  # MUST BE NULL
    assert res.net_pnl_usdt is None
    assert res.net_return_pct == None
    assert res.r2_minus_t01_usdt is None


def test_missing_heartbeat_sets_no_fresh_heartbeat(consumer, example_data):
    """Verifies that heartbeat older than 45s resets process status to NO_FRESH_HEARTBEAT."""
    now_dt = datetime.now(timezone.utc)
    data = copy.deepcopy(example_data)
    
    data["generated_at"] = now_dt.isoformat()
    data["heartbeat_at"] = (now_dt - timedelta(seconds=60)).isoformat()
    data["process_status"] = "ARMED"  # Producer claimed ARMED
    
    res = consumer.parse_and_validate(data, now_dt=now_dt)
    assert res.is_valid is True
    assert res.status == "NO_FRESH_HEARTBEAT"


def test_forbidden_additional_properties_rejected(consumer, example_data):
    """Verifies that additional private fields are strictly rejected per schema."""
    data = copy.deepcopy(example_data)
    data["private_key"] = "secret_trading_code"
    
    res = consumer.parse_and_validate(data)
    assert res.is_valid is False
    assert res.status == "INVALID"
    assert "additional properties" in res.rejection_reason.lower()


def test_wrong_profile_order_rejected(consumer, example_data):
    """Verifies that profiles must be in exact order: BASE, STRESS, BASELINE."""
    data = copy.deepcopy(example_data)
    # Swap profile 0 and 1
    data["profiles"][0], data["profiles"][1] = data["profiles"][1], data["profiles"][0]
    
    res = consumer.parse_and_validate(data)
    assert res.is_valid is False
    assert res.status == "INVALID"
    assert "Profiles order mismatch" in res.rejection_reason


def test_profit_factor_non_null_rejected(consumer, example_data):
    """Verifies that non-null profit_factor is rejected per contract."""
    data = copy.deepcopy(example_data)
    data["profiles"][0]["profit_factor"] = 1.5
    
    res = consumer.parse_and_validate(data)
    assert res.is_valid is False
    assert res.status == "INVALID"
    assert "profit_factor" in res.rejection_reason


def test_future_timestamp_rejected(consumer, example_data):
    """Verifies that timestamps >15s in the future are rejected as clock drift."""
    now_dt = datetime.now(timezone.utc)
    data = copy.deepcopy(example_data)
    data["generated_at"] = (now_dt + timedelta(seconds=30)).isoformat()
    
    res = consumer.parse_and_validate(data, now_dt=now_dt)
    assert res.is_valid is False
    assert res.status == "INVALID"
    assert "Future timestamp" in res.rejection_reason


def test_read_telemetry_file_from_disk(consumer):
    """Verifies read_telemetry_file reads directly from disk."""
    now_dt = datetime(2026, 11, 15, 0, 0, 10, tzinfo=timezone.utc)
    res = consumer.read_telemetry_file(EXAMPLE_PATH, now_dt=now_dt)
    assert res.is_valid is True
    assert res.r2_base_profile == "R2:300:BASE:OBSERVED_BOOK"


def test_read_nonexistent_file_returns_invalid(consumer):
    """Verifies reading a non-existent file returns INVALID safely."""
    res = consumer.read_telemetry_file(Path("non_existent_telemetry_12345.json"))
    assert res.is_valid is False
    assert res.status == "INVALID"
    assert "does not exist" in res.rejection_reason
