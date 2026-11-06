"""
Script to archive pre-overhaul scalper trades and cleanly reset session state for v3.7 and v3.9.
"""

import json
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

now_ts = int(time.time())
for subdir, name, ver in [("v3_7", "V37", "3.7.0"), ("v3_9", "V39", "3.9.0")]:
    rep = REPO_ROOT / subdir / "reports"
    rep.mkdir(parents=True, exist_ok=True)
    state_f = rep / f"BOT_STATE_PERSISTENT_{name}.json"
    live_f = rep / f"PAPER_TRADING_LIVE_{name}.json"

    # 1. Archive previous state if it exists
    if state_f.exists():
        backup_f = rep / f"PRE_OVERHAUL_STATE_{name}_{now_ts}.json"
        try:
            backup_f.write_text(state_f.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"[ARCHIVE] {state_f.name} -> {backup_f.name}")
        except Exception as e:
            print(f"[ARCHIVE ERROR] {e}")

    # 2. Write fresh state with un-tripped circuit breaker
    fresh_state = {
        "version": ver,
        "name": f"v{ver[:3]} Scalper",
        "equity": 300.0,
        "capital": 300.0,
        "initial_capital": 300.0,
        "daily_pnl_usdt": 0.0,
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
        "net_pnl": 0.0,
        "circuit_breaker": False,
        "circuit_breaker_tripped": False,
        "open_positions": [],
        "pending_orders": [],
        "closed_trades": [],
        "online": True,
        "timestamp": int(time.time() * 1000)
    }

    try:
        live_f.write_text(json.dumps(fresh_state, indent=2), encoding="utf-8")
        state_f.write_text(json.dumps(fresh_state, indent=2), encoding="utf-8")
        print(f"[RESET] {name} session state reset cleanly to $300.00 base equity (Circuit Breaker: OK).")
    except Exception as e:
        print(f"[RESET ERROR] {e}")

if __name__ == "__main__":
    print("Scalper state reset completed successfully.")
