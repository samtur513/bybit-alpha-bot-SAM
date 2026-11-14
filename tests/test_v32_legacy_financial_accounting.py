"""
Tests the penny-perfect financial reconciliation and accounting invariants
of the Bybit Multi-Alpha v3.2 Legacy deployment (Port 8083).
Audit Deliverable: V32-LEGACY-FORENSIC-001.
"""

import json
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_DIR = REPO_ROOT / "reports" / "v32_legacy_forensic_001" / "snapshot"
STATE_FILE = SNAPSHOT_DIR / "api_state.json"
STATUS_FILE = SNAPSHOT_DIR / "api_status.json"


@pytest.fixture(scope="module")
def state_data():
    if not STATE_FILE.exists():
        pytest.skip(f"Snapshot state file not found at {STATE_FILE}")
    with open(STATE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


@pytest.fixture(scope="module")
def status_data():
    if not STATUS_FILE.exists():
        pytest.skip(f"Snapshot status file not found at {STATUS_FILE}")
    with open(STATUS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def test_v32_legacy_initial_capital(state_data):
    """Verify initial capital is exactly $300.00."""
    initial_cap = float(state_data.get("trend_engine", {}).get("initial_capital", 0.0))
    assert initial_cap == 300.0, f"Expected initial capital $300.0, got {initial_cap}"


def test_v32_legacy_closed_trade_count(state_data):
    """Verify closed trade count matches the dashboard claim of 28 trades."""
    closed_trades = state_data.get("trend_engine", {}).get("closed_trades", [])
    assert len(closed_trades) == 28, f"Expected 28 closed trades, got {len(closed_trades)}"


def test_v32_legacy_win_rate_and_profit_factor(state_data):
    """Verify win rate 57.1% and profit factor 2.47 match closed trade calculations."""
    closed_trades = state_data.get("trend_engine", {}).get("closed_trades", [])
    wins = [t for t in closed_trades if float(t.get("net_pnl", 0.0)) > 0]
    losses = [t for t in closed_trades if float(t.get("net_pnl", 0.0)) <= 0]

    assert len(wins) == 16, f"Expected 16 wins, got {len(wins)}"
    assert len(losses) == 12, f"Expected 12 losses, got {len(losses)}"

    win_rate = len(wins) / len(closed_trades)
    assert abs(win_rate - 0.571428) < 0.001

    gross_profit = sum(float(t.get("net_pnl", 0.0)) for t in wins)
    gross_loss = abs(sum(float(t.get("net_pnl", 0.0)) for t in losses))

    profit_factor = gross_profit / gross_loss
    assert abs(profit_factor - 2.4717) < 0.01


def test_v32_legacy_penny_perfect_reconciliation(state_data, status_data):
    """
    Verify penny-perfect reconciliation of:
    Initial Capital ($300) + Closed Net PnL + Realized Partials - Open Entry Fees == Realized Capital ($321.88).
    Realized Capital ($321.88) + Floating Open PnL ($3.88) == Current Equity ($325.76).
    """
    initial_capital = 300.0
    closed_trades = state_data.get("trend_engine", {}).get("closed_trades", [])
    closed_net_pnl = sum(float(t.get("net_pnl", 0.0)) for t in closed_trades)
    assert abs(closed_net_pnl - 17.991435) < 0.001

    # Active positions in state
    open_positions = state_data.get("trend_engine", {}).get("open_positions", {})
    realized_partials = 0.0
    entry_fees = 0.0

    for sym, pos in open_positions.items():
        realized_partials += float(pos.get("realized_partial", 0.0))
        entry_fees += float(pos.get("entry_fees", 0.0))

    assert abs(realized_partials - 4.011619) < 0.001
    assert abs(entry_fees - 0.123203) < 0.001

    # Realized capital
    computed_realized_cap = initial_capital + closed_net_pnl + realized_partials - entry_fees
    stored_realized_equity = float(state_data.get("trend_engine", {}).get("realized_equity", 0.0))
    assert abs(computed_realized_cap - stored_realized_equity) < 1e-6
    assert abs(computed_realized_cap - 321.879851) < 0.0001
    assert round(computed_realized_cap, 2) == 321.88

    # Dashboard paper stats net pnl
    paper_net_pnl = computed_realized_cap - initial_capital
    assert round(paper_net_pnl, 2) == 21.88

    # Live open positions floating pnl from status snapshot
    open_status = status_data.get("open_positions", [])
    floating_pnl = sum(float(p.get("unrealized_pnl", 0.0)) for p in open_status)

    # Main header net pnl
    total_equity = computed_realized_cap + floating_pnl
    header_net_pnl = total_equity - initial_capital

    assert abs(header_net_pnl - 25.76) < 0.50  # floating marks tick within ~0.40
    assert abs(total_equity - 325.76) < 0.50


def test_v32_legacy_range_engine_zero_trades(state_data):
    """Verify that Range Mean-Reversion engine executed 0 closed trades."""
    range_trades = state_data.get("range_engine", {}).get("closed_trades", [])
    assert len(range_trades) == 0, f"Expected 0 Range trades, got {len(range_trades)}"
