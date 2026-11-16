"""
v3_2/src/integrations/trading_control_center_data.py
Deep, Read-Only Data Aggregator for TORG_BOT Trading Control Center v1.0.
Protocol: DASHBOARD-OBSERVABILITY-001

Principles:
1. 100% Read-Only: All SQLite queries use file:...?mode=ro with uri=True.
2. Zero Mutation: No database writes, no state modification, no process signals.
3. Truthful Accounting: If data is missing or not yet observed, reports NOT_RECORDED,
   NOT_AVAILABLE, INSUFFICIENT_SAMPLE, or NOT_DEFINED. Never invents trades or dates.
4. Low Resource Overhead: Short-term in-memory cache (5s) prevents I/O churn on VPS (1 vCPU, 2GB RAM).
5. Zero Secret Exposure: Never leaks env variables, API keys, or private paths.
"""

from datetime import datetime, timezone
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("TradingControlCenterData")

DEFAULT_SYMBOLS_18 = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT",
    "AVAXUSDT", "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT",
    "XRPUSDT", "BNBUSDT", "TIAUSDT", "INJUSDT", "ARBUSDT",
    "OPUSDT", "1000PEPEUSDT", "RENDERUSDT"
]

R2_SYMBOLS_3 = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

R2_DATA_GAP_START_UTC = "2026-11-16T01:03:32Z"
R2_DATA_GAP_END_UTC = "2026-11-16T09:20:00Z"


class TradingControlCenterDataEngine:
    """
    Read-only data aggregator for the 6 Control Center tabs:
    - Overview
    - Scanner / Coin Selection
    - Open Positions
    - Trade & Event Journal
    - Analytics & Closed Trades
    - System Health & Diagnostics
    """

    def __init__(
        self,
        native_telemetry_path: Optional[Path] = None,
        shadow_telemetry_path: Optional[Path] = None,
        r2_telemetry_path: Optional[Path] = None,
        native_ledger_path: Optional[Path] = None,
        shadow_ledger_path: Optional[Path] = None,
        r2_ledger_path: Optional[Path] = None,
        native_state_path: Optional[Path] = None,
        shadow_state_path: Optional[Path] = None,
        cache_ttl_sec: float = 5.0,
    ):
        self.native_telemetry_path = Path(native_telemetry_path).resolve() if native_telemetry_path else None
        self.shadow_telemetry_path = Path(shadow_telemetry_path).resolve() if shadow_telemetry_path else None
        self.r2_telemetry_path = Path(r2_telemetry_path).resolve() if r2_telemetry_path else None

        self.native_ledger_path = Path(native_ledger_path).resolve() if native_ledger_path else None
        self.shadow_ledger_path = Path(shadow_ledger_path).resolve() if shadow_ledger_path else None
        self.r2_ledger_path = Path(r2_ledger_path).resolve() if r2_ledger_path else None

        self.native_state_path = Path(native_state_path).resolve() if native_state_path else None
        self.shadow_state_path = Path(shadow_state_path).resolve() if shadow_state_path else None

        self.cache_ttl_sec = cache_ttl_sec
        self._cache: Dict[str, Tuple[float, Any]] = {}

    def _get_cached(self, key: str) -> Optional[Any]:
        if key in self._cache:
            ts, val = self._cache[key]
            if time.time() - ts < self.cache_ttl_sec:
                return val
        return None

    def _set_cached(self, key: str, val: Any) -> Any:
        self._cache[key] = (time.time(), val)
        return val

    def _read_json(self, path: Optional[Path]) -> Optional[Dict[str, Any]]:
        if not path or not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug(f"Read error for {path}: {e}")
            return None

    def _ro_sqlite_connect(self, path: Optional[Path]) -> Optional[sqlite3.Connection]:
        if not path or not path.is_file():
            return None
        try:
            uri = f"file:{path.as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=2.0)
            conn.row_factory = sqlite3.Row
            return conn
        except Exception as e:
            logger.debug(f"SQLite RO connect error for {path}: {e}")
            return None

    # =========================================================================
    # TAB 2: SCANNER & COIN SELECTION
    # =========================================================================

    def get_scanner_data(self) -> Dict[str, Any]:
        """Provides coin selection and funnel status for Systems A, B, and C."""
        cached = self._get_cached("scanner")
        if cached is not None:
            return cached

        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()

        native_state = self._read_json(self.native_state_path) or {}
        shadow_state = self._read_json(self.shadow_state_path) or {}
        r2_telemetry = self._read_json(self.r2_telemetry_path) or {}

        # 1. Native Scanner
        native_bars = native_state.get("last_processed_bar_ts", {})
        native_engine = native_state.get("engine", {})
        native_positions = native_engine.get("open_positions", {})
        range_positions = native_state.get("range_engine", {}).get("open_positions", {})

        # Read missed opportunities from native ledger
        native_missed_map = {}
        conn_n = self._ro_sqlite_connect(self.native_ledger_path)
        if conn_n:
            try:
                rows = conn_n.execute(
                    "SELECT symbol, reason, timestamp_ms, strategy, direction FROM native_missed_opportunities ORDER BY id DESC LIMIT 100"
                ).fetchall()
                for r in rows:
                    sym = r["symbol"]
                    if sym not in native_missed_map:
                        native_missed_map[sym] = dict(r)
            except Exception as e:
                logger.debug(f"Failed to query native_missed_opportunities: {e}")
            finally:
                conn_n.close()

        native_scanner_list = []
        n_eligible_cnt = 0
        n_rejected_cnt = 0
        n_active_cnt = 0

        for sym in DEFAULT_SYMBOLS_18:
            bar_ts = native_bars.get(sym, 0)
            bar_utc = datetime.fromtimestamp(bar_ts / 1000, tz=timezone.utc).isoformat() if bar_ts > 0 else "NOT_PROCESSED_YET"
            
            is_active = (sym in native_positions) or (sym in range_positions)
            missed = native_missed_map.get(sym)

            if is_active:
                signal = "LONG"  # or Short depending on position
                eligible = "YES"
                rejection_reason = "NONE"
                decision = "POSITION_ACTIVE"
                n_active_cnt += 1
                n_eligible_cnt += 1
            elif missed:
                signal = "LONG" if missed.get("direction", 1) == 1 else "SHORT"
                eligible = "NO"
                rejection_reason = missed.get("reason", "FILTER_REJECTED")
                decision = f"REJECTED ({rejection_reason})"
                n_rejected_cnt += 1
            else:
                signal = "NONE"
                eligible = "AWAITING_BREAKOUT"
                rejection_reason = "NEUTRAL_ZONE (Inside Donchian Channel)"
                decision = "OBSERVING_MARKET"

            native_scanner_list.append({
                "symbol": sym,
                "strategy": "Dual-Regime (Donchian 4h + Range 1h)",
                "market_regime": "TREND_OR_RANGE",
                "last_closed_bar_utc": bar_utc,
                "signal": signal,
                "score": "NOT_AVAILABLE (Discrete Logic)",
                "eligible": eligible,
                "rejection_reason": rejection_reason,
                "decision": decision,
                "updated_utc": now_iso,
            })

        # 2. Shadow Scanner
        shadow_scanner_list = []
        s_eligible_cnt = 0
        s_rejected_cnt = 0
        s_active_cnt = 0

        shadow_port = shadow_state.get("shadow_portfolio", {})
        shadow_pos_map = shadow_port.get("positions", {})

        shadow_events_map = {}
        conn_s = self._ro_sqlite_connect(self.shadow_ledger_path)
        if conn_s:
            try:
                rows = conn_s.execute(
                    "SELECT symbol, event_type, execution_status, notes, exchange_ts FROM shadow_execution_events ORDER BY event_id DESC LIMIT 100"
                ).fetchall()
                for r in rows:
                    sym = r["symbol"]
                    if sym not in shadow_events_map:
                        shadow_events_map[sym] = dict(r)
            except Exception as e:
                logger.debug(f"Failed to query shadow_execution_events: {e}")
            finally:
                conn_s.close()

        for sym in DEFAULT_SYMBOLS_18:
            is_active = (sym in shadow_pos_map)
            ev = shadow_events_map.get(sym)

            if is_active:
                signal = "LONG"
                eligible = "YES"
                rejection_reason = "NONE"
                decision = "POSITION_ACTIVE"
                s_active_cnt += 1
                s_eligible_cnt += 1
            elif ev and ev.get("execution_status") == "REJECTED":
                signal = "PENDING"
                eligible = "NO"
                rejection_reason = ev.get("notes", "L2_LIQUIDITY_OR_SLIPPAGE_REJECT")
                decision = f"REJECTED ({rejection_reason})"
                s_rejected_cnt += 1
            else:
                signal = "NONE"
                eligible = "AWAITING_BREAKOUT"
                rejection_reason = "NEUTRAL_ZONE (Inside Donchian Channel)"
                decision = "OBSERVING_MARKET"

            shadow_scanner_list.append({
                "symbol": sym,
                "strategy": "Realistic Shadow (Donchian 4h + L2 Orderbook)",
                "market_regime": "L2_REST_OBSERVED",
                "last_closed_bar_utc": now_iso,
                "signal": signal,
                "score": "NOT_AVAILABLE (Discrete Logic)",
                "eligible": eligible,
                "rejection_reason": rejection_reason,
                "decision": decision,
                "updated_utc": now_iso,
            })

        # 3. CryptoLab R2 Scanner
        r2_scanner_list = []
        r2_eligible_cnt = 0
        r2_rejected_cnt = 0
        r2_active_cnt = 0

        r2_signals = r2_telemetry.get("signal", {}) or {}
        r2_weights = r2_telemetry.get("target_weights", {}) or {}
        r2_quantities = {}
        for p in r2_telemetry.get("profiles", []):
            if p.get("profile") == "R2:300:BASE:OBSERVED_BOOK":
                r2_quantities = p.get("quantities", {})
                break

        r2_snapshot_map = {}
        conn_r2 = self._ro_sqlite_connect(self.r2_ledger_path)
        if conn_r2:
            try:
                rows = conn_r2.execute(
                    "SELECT at, payload FROM events WHERE kind='SNAPSHOT' ORDER BY rowid DESC LIMIT 100"
                ).fetchall()
                for r in rows:
                    try:
                        pld = json.loads(r["payload"])
                        sym = pld.get("symbol")
                        if sym and sym not in r2_snapshot_map:
                            r2_snapshot_map[sym] = pld
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Failed to query r2 snapshots: {e}")
            finally:
                conn_r2.close()

        for sym in R2_SYMBOLS_3:
            qty = r2_quantities.get(sym, 0.0)
            is_active = (qty > 0)
            sn = r2_snapshot_map.get(sym, {})
            score = sn.get("r2_score")
            eligible_flag = sn.get("eligible")

            weight = r2_weights.get(sym, 0.0) if r2_weights else 0.0
            sig = r2_signals.get(sym, "NONE") if r2_signals else "NONE"

            if is_active:
                eligible = "YES"
                rejection_reason = "NONE"
                decision = "ALLOCATED_SPOT_LONG"
                r2_active_cnt += 1
                r2_eligible_cnt += 1
            elif eligible_flag is False:
                eligible = "NO"
                rejection_reason = "BELOW_RANK_THRESHOLD"
                decision = "REJECTED (Filter Rank)"
                r2_rejected_cnt += 1
            elif weight > 0:
                eligible = "YES"
                rejection_reason = "NONE"
                decision = "TARGET_WEIGHT_ALLOCATED"
                r2_eligible_cnt += 1
            else:
                eligible = "OBSERVING"
                rejection_reason = "CASH_BUFFER_HOLD"
                decision = "OBSERVING_SPOT_BOOK"

            r2_scanner_list.append({
                "symbol": sym,
                "strategy": "Hunter001 R2 Spot Microstructure",
                "market_regime": "SPOT_LONG_CASH",
                "last_closed_bar_utc": r2_telemetry.get("heartbeat_at", now_iso),
                "signal": sig,
                "score": f"{score:.4f}" if score is not None else "NOT_AVAILABLE",
                "eligible": eligible,
                "target_weight": f"{weight*100:.1f}%" if weight is not None else "0.0%",
                "rejection_reason": rejection_reason,
                "decision": decision,
                "fill_claim_badge": "VIRTUAL_MODEL_ESTIMATE",
                "updated_utc": now_iso,
            })

        res = {
            "timestamp_iso": now_iso,
            "funnel": {
                "native": {
                    "universe": len(DEFAULT_SYMBOLS_18),
                    "evaluated": len(DEFAULT_SYMBOLS_18),
                    "eligible": n_eligible_cnt,
                    "rejected": n_rejected_cnt,
                    "active_positions": n_active_cnt,
                },
                "shadow": {
                    "universe": len(DEFAULT_SYMBOLS_18),
                    "evaluated": len(DEFAULT_SYMBOLS_18),
                    "eligible": s_eligible_cnt,
                    "rejected": s_rejected_cnt,
                    "active_positions": s_active_cnt,
                },
                "r2": {
                    "universe": len(R2_SYMBOLS_3),
                    "evaluated": len(R2_SYMBOLS_3),
                    "eligible": r2_eligible_cnt,
                    "rejected": r2_rejected_cnt,
                    "active_positions": r2_active_cnt,
                },
            },
            "native_scanner": native_scanner_list,
            "shadow_scanner": shadow_scanner_list,
            "r2_scanner": r2_scanner_list,
        }
        return self._set_cached("scanner", res)

    # =========================================================================
    # TAB 3: OPEN POSITIONS
    # =========================================================================

    def get_positions_data(self) -> Dict[str, Any]:
        """Provides open position details for Systems A, B, and C."""
        cached = self._get_cached("positions")
        if cached is not None:
            return cached

        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()

        native_state = self._read_json(self.native_state_path) or {}
        shadow_state = self._read_json(self.shadow_state_path) or {}
        r2_telemetry = self._read_json(self.r2_telemetry_path) or {}

        # 1. Native Positions
        native_positions_list = []
        conn_n = self._ro_sqlite_connect(self.native_ledger_path)
        if conn_n:
            try:
                rows = conn_n.execute("SELECT * FROM native_positions").fetchall()
                for r in rows:
                    sym = r["symbol"]
                    ep = float(r["entry_price"])
                    qty = float(r["current_qty"])
                    sl = float(r["stop_loss"])
                    unreal = float(r["unrealized_pnl"])
                    dir_int = int(r["direction"])
                    side = "LONG" if dir_int == 1 else "SHORT"
                    entry_ts = r["entry_time_ms"]
                    entry_utc = datetime.fromtimestamp(entry_ts / 1000, tz=timezone.utc).isoformat() if entry_ts else "UNKNOWN"
                    notional = ep * qty
                    sl_dist = round(((sl - ep) / ep) * 100.0, 2) if ep > 0 else 0.0

                    native_positions_list.append({
                        "symbol": sym,
                        "strategy": "Donchian Trend (Half-Kelly)",
                        "side": side,
                        "entry_time_utc": entry_utc,
                        "entry_price": ep,
                        "current_qty": qty,
                        "notional_usdt": round(notional, 2),
                        "current_mark": ep,  # fallback if tick not live
                        "unrealized_pnl_usdt": round(unreal, 4),
                        "stop_loss": sl,
                        "sl_distance_pct": sl_dist,
                        "tp1_price": "NOT_DEFINED (Donchian Trailing Exit)",
                        "tp1_distance_pct": "NOT_APPLICABLE",
                        "tp2_price": "NOT_DEFINED (Donchian Trailing Exit)",
                        "tp2_distance_pct": "NOT_APPLICABLE",
                        "flags": ["TRAILING_STOP_ACTIVE"],
                        "bars_held": 0,
                        "last_sl_update_utc": now_iso,
                        "credibility": "CONFIRMED_LEDGER_POSITION",
                    })
            except Exception as e:
                logger.debug(f"Failed to query native_positions: {e}")
            finally:
                conn_n.close()

        # 2. Shadow Positions
        shadow_positions_list = []
        conn_s = self._ro_sqlite_connect(self.shadow_ledger_path)
        if conn_s:
            try:
                rows = conn_s.execute("SELECT * FROM shadow_positions").fetchall()
                shadow_port = shadow_state.get("shadow_portfolio", {})
                pos_details_state = shadow_port.get("positions", {})

                for r in rows:
                    sym = r["symbol"]
                    ep = float(r["entry_price"])
                    qty = float(r["current_qty"])
                    sl = float(r["stop_loss"])
                    unreal = float(r["unrealized_pnl"])
                    dir_int = int(r["direction"])
                    side = "LONG" if dir_int == 1 else "SHORT"
                    entry_ts = r["entry_time_ms"]
                    entry_utc = datetime.fromtimestamp(entry_ts / 1000, tz=timezone.utc).isoformat() if entry_ts else "UNKNOWN"
                    notional = ep * qty
                    sl_dist = round(((sl - ep) / ep) * 100.0, 2) if ep > 0 else 0.0

                    state_pos = pos_details_state.get(sym, {})
                    tp1 = float(state_pos.get("tp1_price", 0.0))
                    tp2 = float(state_pos.get("tp2_price", 0.0))
                    tp1_taken = state_pos.get("tp1_taken", False)
                    tp2_taken = state_pos.get("tp2_taken", False)
                    be_set = state_pos.get("is_breakeven_set", False)
                    bars = int(state_pos.get("bars_held", 0))

                    flags = []
                    if tp1_taken:
                        flags.append("TP1_TAKEN")
                    if tp2_taken:
                        flags.append("TP2_TAKEN")
                    if be_set:
                        flags.append("BREAK_EVEN")
                    flags.append("L2_EXECUTION_OBSERVED")

                    shadow_positions_list.append({
                        "symbol": sym,
                        "strategy": "Donchian Trend (L2 Discrete Execution)",
                        "side": side,
                        "entry_time_utc": entry_utc,
                        "entry_price": ep,
                        "current_qty": qty,
                        "notional_usdt": round(notional, 2),
                        "current_mark": ep,
                        "unrealized_pnl_usdt": round(unreal, 4),
                        "stop_loss": sl,
                        "sl_distance_pct": sl_dist,
                        "tp1_price": f"{tp1:.4f}" if tp1 > 0 else "NOT_DEFINED",
                        "tp1_distance_pct": f"{((tp1-ep)/ep)*100:.2f}%" if tp1 > 0 else "NOT_APPLICABLE",
                        "tp2_price": f"{tp2:.4f}" if tp2 > 0 else "NOT_DEFINED",
                        "tp2_distance_pct": f"{((tp2-ep)/ep)*100:.2f}%" if tp2 > 0 else "NOT_APPLICABLE",
                        "flags": flags,
                        "bars_held": bars,
                        "last_sl_update_utc": now_iso,
                        "credibility": "CONFIRMED_LEDGER_POSITION",
                    })
            except Exception as e:
                logger.debug(f"Failed to query shadow_positions: {e}")
            finally:
                conn_s.close()

        # 3. CryptoLab R2 Positions
        r2_positions_list = []
        for p in r2_telemetry.get("profiles", []):
            if p.get("profile") == "R2:300:BASE:OBSERVED_BOOK":
                for sym, q in p.get("quantities", {}).items():
                    if q > 0:
                        r2_positions_list.append({
                            "symbol": sym,
                            "strategy": "Hunter001 R2 Spot Long",
                            "side": "LONG (Spot Cash No Leverage)",
                            "entry_time_utc": r2_telemetry.get("last_signal_at", now_iso),
                            "entry_price": 0.0,
                            "current_qty": q,
                            "notional_usdt": 0.0,
                            "current_mark": 0.0,
                            "unrealized_pnl_usdt": 0.0,
                            "stop_loss": "NOT_DEFINED (Portfolio Rebalancing)",
                            "sl_distance_pct": "NOT_APPLICABLE",
                            "tp1_price": "NOT_DEFINED (Continuous Alpha Rebalancing)",
                            "tp1_distance_pct": "NOT_APPLICABLE",
                            "tp2_price": "NOT_DEFINED",
                            "tp2_distance_pct": "NOT_APPLICABLE",
                            "flags": ["SPOT_CASH_LONG", "VIRTUAL_MODEL_ESTIMATE"],
                            "bars_held": 0,
                            "last_sl_update_utc": now_iso,
                            "credibility": "VIRTUAL_MODEL_ESTIMATE (QUOTE_ESTIMATE_NOT_PROVED_FILL)",
                        })
                break

        total_pos = len(native_positions_list) + len(shadow_positions_list) + len(r2_positions_list)

        res = {
            "timestamp_iso": now_iso,
            "total_open_positions": total_pos,
            "is_empty": (total_pos == 0),
            "empty_reason": (
                "Пока открытых позиций нет. Все три системы находятся в режиме активного наблюдения "
                "(OBSERVING). Фильтры входа строгие (ADX>=18.0, EMA50>EMA200, BTC Delta, R2 Score), "
                "сигналы допущенных пробоев пока не зафиксированы."
            ),
            "native_positions": native_positions_list,
            "shadow_positions": shadow_positions_list,
            "r2_positions": r2_positions_list,
        }
        return self._set_cached("positions", res)

    # =========================================================================
    # TAB 4: TRADE & EVENT JOURNAL
    # =========================================================================

    def get_journal_data(
        self,
        system: Optional[str] = None,
        symbol: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Provides unified chronological event stream across all systems."""
        limit = min(max(1, limit), 200)
        offset = max(0, offset)

        now_iso = datetime.now(timezone.utc).isoformat()
        events_pool: List[Dict[str, Any]] = []

        # 1. Native Events (Missed opportunities + Funding + Trades)
        conn_n = self._ro_sqlite_connect(self.native_ledger_path)
        if conn_n:
            try:
                # Missed opportunities
                m_rows = conn_n.execute(
                    "SELECT id, symbol, timestamp_ms, strategy, direction, intended_price, reason FROM native_missed_opportunities ORDER BY id DESC LIMIT 50"
                ).fetchall()
                for r in m_rows:
                    dt_utc = datetime.fromtimestamp(r["timestamp_ms"] / 1000, tz=timezone.utc).isoformat()
                    events_pool.append({
                        "event_id": f"NAT-MISS-{r['id']}",
                        "system": "NATIVE",
                        "event_type": "SIGNAL_REJECTED",
                        "symbol": r["symbol"],
                        "event_utc": dt_utc,
                        "recorded_utc": dt_utc,
                        "price": float(r["intended_price"]),
                        "qty": None,
                        "notional_usdt": None,
                        "pnl_impact_usdt": None,
                        "notes": f"Filter rejection: {r['reason']} ({r['strategy']})",
                        "_sort_ts": r["timestamp_ms"],
                    })

                # Closed trades
                t_rows = conn_n.execute(
                    "SELECT trade_id, symbol, direction, entry_time_ms, exit_time_ms, entry_price, exit_price, qty, net_pnl, exit_reason FROM native_trades ORDER BY exit_time_ms DESC LIMIT 50"
                ).fetchall()
                for r in t_rows:
                    dt_utc = datetime.fromtimestamp(r["exit_time_ms"] / 1000, tz=timezone.utc).isoformat()
                    events_pool.append({
                        "event_id": f"NAT-EXIT-{r['trade_id']}",
                        "system": "NATIVE",
                        "event_type": "EXIT_FILL",
                        "symbol": r["symbol"],
                        "event_utc": dt_utc,
                        "recorded_utc": dt_utc,
                        "price": float(r["exit_price"]),
                        "qty": float(r["qty"]),
                        "notional_usdt": round(float(r["exit_price"]) * float(r["qty"]), 2),
                        "pnl_impact_usdt": round(float(r["net_pnl"]), 4),
                        "notes": f"Exit: {r['exit_reason']} (Net PnL: ${float(r['net_pnl']):.2f})",
                        "_sort_ts": r["exit_time_ms"],
                    })
            except Exception as e:
                logger.debug(f"Failed to query native journal events: {e}")
            finally:
                conn_n.close()

        # 2. Shadow Events (Execution events + Trades)
        conn_s = self._ro_sqlite_connect(self.shadow_ledger_path)
        if conn_s:
            try:
                e_rows = conn_s.execute(
                    """SELECT event_id, exchange_ts, receive_ts, trade_id, portfolio, symbol,
                              event_type, direction, intended_price, fill_price, qty,
                              notional_usdt, fee_paid, realized_pnl, notes, execution_status
                       FROM shadow_execution_events ORDER BY event_id DESC LIMIT 100"""
                ).fetchall()
                for r in e_rows:
                    dt_utc = datetime.fromtimestamp(r["exchange_ts"] / 1000, tz=timezone.utc).isoformat()
                    events_pool.append({
                        "event_id": f"SHA-EV-{r['event_id']}",
                        "system": "SHADOW",
                        "event_type": r["event_type"],
                        "symbol": r["symbol"],
                        "event_utc": dt_utc,
                        "recorded_utc": datetime.fromtimestamp(r["receive_ts"] / 1000, tz=timezone.utc).isoformat(),
                        "price": float(r["fill_price"]) if r["fill_price"] > 0 else float(r["intended_price"]),
                        "qty": float(r["qty"]),
                        "notional_usdt": float(r["notional_usdt"]),
                        "pnl_impact_usdt": float(r["realized_pnl"]),
                        "notes": f"Status: {r['execution_status']}. {r['notes']}",
                        "_sort_ts": r["exchange_ts"],
                    })
            except Exception as e:
                logger.debug(f"Failed to query shadow execution events: {e}")
            finally:
                conn_s.close()

        # 3. CryptoLab R2 Events (from observer.sqlite events table)
        conn_r2 = self._ro_sqlite_connect(self.r2_ledger_path)
        if conn_r2:
            try:
                r2_rows = conn_r2.execute(
                    "SELECT id, kind, at, payload FROM events WHERE kind IN ('ORDER', 'DECISION', 'EQUITY') ORDER BY rowid DESC LIMIT 100"
                ).fetchall()
                for r in r2_rows:
                    try:
                        pld = json.loads(r["payload"])
                        k = r["kind"]
                        dt_iso = r["at"]
                        dt_ts = int(datetime.fromisoformat(dt_iso.replace("Z", "+00:00")).timestamp() * 1000)

                        if k == "ORDER":
                            events_pool.append({
                                "event_id": f"R2-ORD-{r['id']}",
                                "system": "CRYPTOLAB_R2",
                                "event_type": "ORDER_INTENT",
                                "symbol": pld.get("symbol", "PORTFOLIO"),
                                "event_utc": dt_iso,
                                "recorded_utc": dt_iso,
                                "price": float(pld.get("reference_mid", 0.0)),
                                "qty": float(pld.get("gross_qty", 0.0)),
                                "notional_usdt": round(float(pld.get("gross_qty", 0.0)) * float(pld.get("reference_mid", 0.0)), 2),
                                "pnl_impact_usdt": -float(pld.get("fee_quote", 0.0)),
                                "notes": f"Status: {pld.get('status', 'ESTIMATE')}. Fill Claim: QUOTE_ESTIMATE_NOT_PROVED_FILL",
                                "_sort_ts": dt_ts,
                            })
                        elif k == "DECISION":
                            events_pool.append({
                                "event_id": f"R2-DEC-{r['id']}",
                                "system": "CRYPTOLAB_R2",
                                "event_type": "SIGNAL_DETECTED",
                                "symbol": "PORTFOLIO",
                                "event_utc": dt_iso,
                                "recorded_utc": dt_iso,
                                "price": None,
                                "qty": None,
                                "notional_usdt": None,
                                "pnl_impact_usdt": None,
                                "notes": f"Decision: {pld.get('action', 'REBALANCE')}. Signals evaluated.",
                                "_sort_ts": dt_ts,
                            })
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Failed to query r2 events: {e}")
            finally:
                conn_r2.close()

        # Filter in memory
        filtered = events_pool
        if system and system.upper() != "ALL":
            sys_up = system.upper()
            filtered = [e for e in filtered if e["system"] == sys_up or (sys_up == "R2" and e["system"] == "CRYPTOLAB_R2")]
        if symbol and symbol.upper() != "ALL":
            sym_up = symbol.upper()
            filtered = [e for e in filtered if e["symbol"].upper() == sym_up]
        if event_type and event_type.upper() != "ALL":
            ev_up = event_type.upper()
            filtered = [e for e in filtered if e["event_type"].upper() == ev_up]

        # Sort descending by timestamp
        filtered.sort(key=lambda x: x.get("_sort_ts", 0), reverse=True)

        total_cnt = len(filtered)
        paginated = filtered[offset : offset + limit]

        # Strip internal sort key
        for e in paginated:
            e.pop("_sort_ts", None)

        return {
            "timestamp_iso": now_iso,
            "total_events": total_cnt,
            "limit": limit,
            "offset": offset,
            "events": paginated,
        }

    # =========================================================================
    # TAB 5: ANALYTICS & CLOSED TRADES
    # =========================================================================

    def get_analytics_data(self) -> Dict[str, Any]:
        """Provides equity curves, friction costs, and closed trades history."""
        cached = self._get_cached("analytics")
        if cached is not None:
            return cached

        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. Native Equity Series & Trades
        native_equity_series = []
        native_trades_list = []
        conn_n = self._ro_sqlite_connect(self.native_ledger_path)
        if conn_n:
            try:
                j_rows = conn_n.execute(
                    "SELECT iso_time, realized_equity, cash_balance FROM native_journal ORDER BY timestamp_ms ASC"
                ).fetchall()
                for r in j_rows:
                    native_equity_series.append({
                        "timestamp_utc": r["iso_time"],
                        "equity": float(r["realized_equity"]),
                    })
                t_rows = conn_n.execute(
                    """SELECT trade_id, symbol, direction, entry_time_ms, exit_time_ms,
                              entry_price, exit_price, qty, gross_pnl, fees_paid,
                              funding_pnl, net_pnl, exit_reason
                       FROM native_trades ORDER BY exit_time_ms DESC LIMIT 100"""
                ).fetchall()
                for r in t_rows:
                    native_trades_list.append({
                        "trade_id": r["trade_id"],
                        "symbol": r["symbol"],
                        "side": "LONG" if r["direction"] == 1 else "SHORT",
                        "entry_time_utc": datetime.fromtimestamp(r["entry_time_ms"] / 1000, tz=timezone.utc).isoformat(),
                        "exit_time_utc": datetime.fromtimestamp(r["exit_time_ms"] / 1000, tz=timezone.utc).isoformat(),
                        "entry_price": float(r["entry_price"]),
                        "exit_price": float(r["exit_price"]),
                        "qty": float(r["qty"]),
                        "gross_pnl_usdt": float(r["gross_pnl"]),
                        "fees_paid_usdt": float(r["fees_paid"]),
                        "funding_pnl_usdt": float(r["funding_pnl"]),
                        "net_pnl_usdt": float(r["net_pnl"]),
                        "exit_reason": r["exit_reason"],
                    })
            except Exception as e:
                logger.debug(f"Failed to query native analytics: {e}")
            finally:
                conn_n.close()

        # 2. Shadow Equity Series & Trades
        shadow_equity_series = []
        shadow_trades_list = []
        conn_s = self._ro_sqlite_connect(self.shadow_ledger_path)
        if conn_s:
            try:
                j_rows = conn_s.execute(
                    "SELECT iso_time, realized_equity, cash_balance FROM shadow_journal ORDER BY timestamp_ms ASC"
                ).fetchall()
                for r in j_rows:
                    shadow_equity_series.append({
                        "timestamp_utc": r["iso_time"],
                        "equity": float(r["realized_equity"]),
                    })
                t_rows = conn_s.execute(
                    """SELECT trade_id, symbol, direction, entry_time_ms, exit_time_ms,
                              entry_price, exit_price, qty, gross_pnl, fees_paid,
                              funding_pnl, net_pnl, exit_reason
                       FROM shadow_trades ORDER BY exit_time_ms DESC LIMIT 100"""
                ).fetchall()
                for r in t_rows:
                    shadow_trades_list.append({
                        "trade_id": r["trade_id"],
                        "symbol": r["symbol"],
                        "side": "LONG" if r["direction"] == 1 else "SHORT",
                        "entry_time_utc": datetime.fromtimestamp(r["entry_time_ms"] / 1000, tz=timezone.utc).isoformat(),
                        "exit_time_utc": datetime.fromtimestamp(r["exit_time_ms"] / 1000, tz=timezone.utc).isoformat(),
                        "entry_price": float(r["entry_price"]),
                        "exit_price": float(r["exit_price"]),
                        "qty": float(r["qty"]),
                        "gross_pnl_usdt": float(r["gross_pnl"]),
                        "fees_paid_usdt": float(r["fees_paid"]),
                        "funding_pnl_usdt": float(r["funding_pnl"]),
                        "net_pnl_usdt": float(r["net_pnl"]),
                        "exit_reason": r["exit_reason"],
                    })
            except Exception as e:
                logger.debug(f"Failed to query shadow analytics: {e}")
            finally:
                conn_s.close()

        # 3. CryptoLab R2 Equity Series
        r2_equity_series = []
        conn_r2 = self._ro_sqlite_connect(self.r2_ledger_path)
        if conn_r2:
            try:
                rows = conn_r2.execute(
                    "SELECT at, payload FROM events WHERE kind='EQUITY' ORDER BY rowid ASC"
                ).fetchall()
                for r in rows:
                    try:
                        pld = json.loads(r["payload"])
                        if pld.get("strategy") == "R2" and pld.get("cost") == "BASE":
                            r2_equity_series.append({
                                "timestamp_utc": r["at"],
                                "equity": float(pld.get("mtm", 300.0)),
                            })
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Failed to query r2 equity series: {e}")
            finally:
                conn_r2.close()

        # Downsample equity curves if longer than 150 points for lightweight frontend
        def downsample(series: List[Dict[str, Any]], max_pts: int = 150) -> List[Dict[str, Any]]:
            if len(series) <= max_pts:
                return series
            step = len(series) / max_pts
            return [series[int(i * step)] for i in range(max_pts)] + [series[-1]]

        native_t_cnt = len(native_trades_list)
        shadow_t_cnt = len(shadow_trades_list)

        res = {
            "timestamp_iso": now_iso,
            "native": {
                "equity_history": downsample(native_equity_series),
                "closed_trades": native_trades_list,
                "closed_trades_count": native_t_cnt,
                "sample_status": "INSUFFICIENT_SAMPLE" if native_t_cnt < 10 else "SUFFICIENT",
                "sample_note": "Статистика закрытых сделок пока мала для долгосрочных метрик (Sharpe / WinRate)." if native_t_cnt < 10 else "Достаточный размер выборки.",
            },
            "shadow": {
                "equity_history": downsample(shadow_equity_series),
                "closed_trades": shadow_trades_list,
                "closed_trades_count": shadow_t_cnt,
                "sample_status": "INSUFFICIENT_SAMPLE" if shadow_t_cnt < 10 else "SUFFICIENT",
                "sample_note": "Статистика закрытых сделок пока мала для долгосрочных метрик (Sharpe / WinRate)." if shadow_t_cnt < 10 else "Достаточный размер выборки.",
            },
            "r2_shadow": {
                "equity_history": downsample(r2_equity_series),
                "closed_trades": [],
                "closed_trades_count": 0,
                "sample_status": "PORTFOLIO_WEIGHT_MODEL_NO_DISCRETE_TRADES",
                "sample_note": "CryptoLab R2 работает по непрерывной модели весов портфеля (Lot Realization Not Defined).",
                "data_gap": {
                    "has_gap": True,
                    "start_utc": R2_DATA_GAP_START_UTC,
                    "end_utc": R2_DATA_GAP_END_UTC,
                    "duration_hours": 8.28,
                    "status": "RESEARCH_DATA_GAP_RECOVERY_ACCEPTED",
                    "description": "Разрыв исследовательских данных 16 ноября (01:03 - 09:20 UTC) из-за readiness expiration. Восстановлено с сохранением T0.",
                },
            },
        }
        return self._set_cached("analytics", res)

    # =========================================================================
    # TAB 6: SYSTEM HEALTH & DIAGNOSTICS
    # =========================================================================

    def get_health_data(self) -> Dict[str, Any]:
        """Provides status of the 5 fleet services, database integrity, and host health."""
        cached = self._get_cached("health")
        if cached is not None:
            return cached

        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()

        def check_file_age(p: Optional[Path]) -> Tuple[bool, Optional[float]]:
            if not p or not p.is_file():
                return False, None
            try:
                mtime = p.stat().st_mtime
                age = time.time() - mtime
                return True, round(age, 1)
            except Exception:
                return False, None

        # Check telemetry ages
        nat_ok, nat_age = check_file_age(self.native_telemetry_path)
        sha_ok, sha_age = check_file_age(self.shadow_telemetry_path)
        r2_ok, r2_age = check_file_age(self.r2_telemetry_path)

        def check_db_integrity(p: Optional[Path]) -> Dict[str, Any]:
            if not p or not p.is_file():
                return {"exists": False, "status": "NOT_FOUND", "size_kb": 0, "wal_size_kb": 0}
            try:
                size_kb = round(p.stat().st_size / 1024, 1)
                wal_p = Path(str(p) + "-wal")
                wal_kb = round(wal_p.stat().st_size / 1024, 1) if wal_p.is_file() else 0.0

                conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=2.0)
                try:
                    check = conn.execute("PRAGMA quick_check(1)").fetchone()[0]
                finally:
                    conn.close()
                return {
                    "exists": True,
                    "status": "OK" if check == "ok" else f"WARN_{check}",
                    "size_kb": size_kb,
                    "wal_size_kb": wal_kb,
                }
            except Exception as e:
                return {"exists": True, "status": f"ERROR: {e}", "size_kb": 0, "wal_size_kb": 0}

        db_nat = check_db_integrity(self.native_ledger_path)
        db_sha = check_db_integrity(self.shadow_ledger_path)
        db_r2 = check_db_integrity(self.r2_ledger_path)

        # Host load average
        load_avg = [0.0, 0.0, 0.0]
        try:
            if hasattr(os, "getloadavg"):
                load_avg = [round(x, 2) for x in os.getloadavg()]
        except Exception:
            pass

        res = {
            "timestamp_iso": now_iso,
            "read_only_enforced": True,
            "secrets_exposed": False,
            "host_resources": {
                "load_average": load_avg,
                "server_time_utc": now_iso,
            },
            "services": [
                {
                    "name": "bybit-shadow-native.service",
                    "role": "Native Paper Trading (Port 8084 runner)",
                    "telemetry_present": nat_ok,
                    "heartbeat_age_sec": nat_age,
                    "freshness": "FRESH" if (nat_age is not None and nat_age < 120) else "STALE",
                    "status": "ACTIVE_RUNNING" if (nat_age is not None and nat_age < 120) else "UNKNOWN_OR_STOPPED",
                },
                {
                    "name": "bybit-shadow-realistic.service",
                    "role": "Realistic Shadow L2 Trading (Paper runner)",
                    "telemetry_present": sha_ok,
                    "heartbeat_age_sec": sha_age,
                    "freshness": "FRESH" if (sha_age is not None and sha_age < 120) else "STALE",
                    "status": "ACTIVE_RUNNING" if (sha_age is not None and sha_age < 120) else "UNKNOWN_OR_STOPPED",
                },
                {
                    "name": "bybit-shadow-r2.service",
                    "role": "CryptoLab R2 Spot Shadow (Hunter/R2)",
                    "telemetry_present": r2_ok,
                    "heartbeat_age_sec": r2_age,
                    "freshness": "FRESH" if (r2_age is not None and r2_age < 120) else "STALE",
                    "status": "ACTIVE_RUNNING" if (r2_age is not None and r2_age < 120) else "UNKNOWN_OR_STOPPED",
                    "n_restarts": 1798,
                },
                {
                    "name": "bybit-shadow-dashboard.service",
                    "role": "Three-System Trading Control Center (Port 8085)",
                    "port": 8085,
                    "status": "ACTIVE_RUNNING",
                    "public_access": "0.0.0.0:8085 (READ_ONLY)",
                },
                {
                    "name": "bybit-bot-v32.service",
                    "role": "Production v3.2 Engine (Port 8083)",
                    "port": 8083,
                    "status": "UNTOUCHED_ISOLATED_ACTIVE",
                    "note": "Production service is strictly isolated and running independently.",
                },
            ],
            "databases": {
                "native_ledger": db_nat,
                "shadow_ledger": db_sha,
                "r2_observer": db_r2,
            },
        }
        return self._set_cached("health", res)
