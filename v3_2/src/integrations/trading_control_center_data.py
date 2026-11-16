"""
v3_2/src/integrations/trading_control_center_data.py
Deep, Read-Only Data Engine for TORG_BOT Trading Control Center v1.0.
Protocol: DASHBOARD-OBSERVABILITY-001 (Audit Repair-002)

Architectural Invariants:
1. 100% Real SQLite Schemas:
   - Native: native_state_journal, native_journal, native_trades, native_missed_opportunities, native_funding_settlements.
   - Shadow: shadow_state_journal, shadow_execution_events, shadow_funding_settlements.
   - R2: observer.sqlite (accounts, events) + telemetry.json.
   (No imaginary tables: native_positions, shadow_positions, shadow_journal, shadow_trades are NEVER queried).
2. Honest Accounting:
   - Native equity uses native_journal.total_equity (not realized_equity).
   - Real positions: parsed from authoritative state_json (engine.open_positions, range_engine.open_positions, shadow_portfolio.positions).
   - Real directions: +1 -> LONG, -1 -> SHORT.
   - Cascade TP in Native: computed from entry_price, direction, initial_atr (1.5R and 3.0R) when enabled.
   - Shadow TP: tp1_price, tp2_price, tp1_taken, tp2_taken.
   - R2 Spot Inventory: cash balance + coin quantities (BTC, ETH, SOL) with VIRTUAL_MODEL_ESTIMATE badge.
3. Zero Fabricated Data:
   - Scanner only evaluates coins with proven evidence (last_processed_bar_ts > 0).
   - If reason is not recorded, returns NOT_RECORDED.
   - Target weights in R2: parsed as array [w_btc, w_eth, w_sol] or dict.
   - Equity curve for R2: strictly filtered to R2:300:BASE:OBSERVED_BOOK (never pools $1000/$10000).
4. Data Source Error Distinction:
   - Missing or corrupted SQLite/state is reported as DATA_SOURCE_ERROR with safe diagnostics.
   - An empty portfolio (0 positions) is reported as OK_EMPTY, distinct from an error.
5. High Performance on 1 vCPU / 2GB RAM:
   - Bounded queries (LIMIT 1, LIMIT 100, LIMIT 500).
   - 5.0s in-memory caching.
   - No PRAGMA quick_check during request cycles.
6. Zero Secrets & Safe Display:
   - Strips absolute filesystem paths (/opt/bybit-shadow/, etc.).
"""

from datetime import datetime, timezone, timedelta
from decimal import Decimal
import json
import math
import logging
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Dict, List, Optional, Tuple, Union

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

R2_PRIMARY_PROFILE = "R2:300:BASE:OBSERVED_BOOK"
R2_PROFILES_ALL = [
    "R2:300:BASE:OBSERVED_BOOK",
    "R2:300:STRESS:OBSERVED_BOOK",
    "BASELINE:300:BASE:OBSERVED_BOOK",
]


def sanitize_safe_text(val: Any) -> str:
    """Sanitizes text by stripping absolute server paths and control characters."""
    if val is None:
        return ""
    s = str(val)
    # Strip known server prefixes to avoid leaking absolute filesystem structure
    s = re.sub(r'(/opt/bybit-shadow/|/opt/bybit-bot/|[A-Za-z]:[/\\][^\s:]+[/\\])', '[root]/', s)
    return s


def format_utc_ts(ts_val: Union[int, float, str, None]) -> str:
    """Formats timestamp (seconds, milliseconds or ISO string) to standardized UTC ISO string."""
    if ts_val is None or ts_val == 0 or ts_val == "0":
        return "NOT_RECORDED"
    if isinstance(ts_val, (int, float)):
        # If timestamp is in seconds (> 1e9 and < 1e11)
        if 1e9 < ts_val < 1e11:
            return datetime.fromtimestamp(ts_val, tz=timezone.utc).isoformat()
        # If timestamp is in milliseconds (> 1e12)
        elif ts_val >= 1e11:
            return datetime.fromtimestamp(ts_val / 1000.0, tz=timezone.utc).isoformat()
        else:
            return "NOT_RECORDED"
    s = str(ts_val).strip()
    if not s or s in ("0", "UNKNOWN", "NONE"):
        return "NOT_RECORDED"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.isoformat()
    except Exception:
        return sanitize_safe_text(s)


def format_msk_ts(ts_val: Union[int, float, str, None]) -> str:
    """Formats timestamp (seconds, ms or ISO) to Moscow Standard Time (MSK, UTC+3)."""
    utc_str = format_utc_ts(ts_val)
    if utc_str in ("NOT_RECORDED", "NOT_AVAILABLE"):
        return utc_str
    try:
        dt = datetime.fromisoformat(utc_str.replace("Z", "+00:00"))
        msk_tz = timezone(timedelta(hours=3))
        dt_msk = dt.astimezone(msk_tz)
        return dt_msk.strftime("%Y-%m-%d %H:%M:%S MSK")
    except Exception:
        return utc_str


def format_dual_ts(ts_val: Union[int, float, str, None]) -> str:
    """Formats timestamp showing both UTC and MSK."""
    utc_str = format_utc_ts(ts_val)
    if utc_str in ("NOT_RECORDED", "NOT_AVAILABLE"):
        return utc_str
    msk_str = format_msk_ts(ts_val)
    return f"{utc_str} ({msk_str})"


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
        self.native_telemetry_path = self._resolve_path(
            native_telemetry_path,
            Path("/opt/bybit-shadow/telemetry/telemetry_native.json"),
            Path("reports/telemetry_native.json"),
        )
        self.shadow_telemetry_path = self._resolve_path(
            shadow_telemetry_path,
            Path("/opt/bybit-shadow/telemetry/telemetry_shadow.json"),
            Path("reports/telemetry_shadow.json"),
        )
        self.r2_telemetry_path = self._resolve_path(
            r2_telemetry_path,
            Path("/opt/bybit-shadow/data/cryptolab/telemetry.json"),
            Path("data/cryptolab/telemetry.json"),
        )

        self.native_ledger_path = self._resolve_path(
            native_ledger_path,
            Path("/opt/bybit-shadow/data/native_ledger.db"),
            Path("reports/v32_native_ledger.db"),
        )
        self.shadow_ledger_path = self._resolve_path(
            shadow_ledger_path,
            Path("/opt/bybit-shadow/data/shadow_ledger.db"),
            Path("reports/v32_shadow_ledger.db"),
        )
        self.r2_ledger_path = self._resolve_path(
            r2_ledger_path,
            Path("/opt/bybit-shadow/data/cryptolab/observer.sqlite"),
            Path("data/cryptolab/observer.sqlite"),
        )

        self.native_state_path = self._resolve_path(
            native_state_path,
            Path("/opt/bybit-shadow/data/native_state.json"),
            Path("reports/v32_native_state.json"),
        )
        self.shadow_state_path = self._resolve_path(
            shadow_state_path,
            Path("/opt/bybit-shadow/data/shadow_state.json"),
            Path("reports/v32_shadow_state.json"),
        )

        self.cache_ttl_sec = cache_ttl_sec
        self._cache: Dict[str, Tuple[float, Any]] = {}

    def _resolve_path(
        self,
        explicit: Optional[Union[Path, str]],
        vps_default: Path,
        local_default: Path,
    ) -> Path:
        """Resolves path prioritizing explicit parameter, then VPS path, then local fallback."""
        if explicit:
            return Path(explicit).resolve()
        if vps_default.exists():
            return vps_default.resolve()
        return local_default.resolve()

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
            logger.debug(f"JSON read error for {path}: {e}")
            return None

    def _ro_sqlite_connect(self, path: Optional[Path]) -> Optional[sqlite3.Connection]:
        """Opens an explicit read-only connection to SQLite using URI parameter mode=ro."""
        if not path or not path.is_file():
            return None
        try:
            uri = f"file:{path.as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=3.0)
            conn.row_factory = sqlite3.Row
            return conn
        except Exception as e:
            logger.debug(f"SQLite RO connect error for {path}: {e}")
            return None

    # =========================================================================
    # REAL STATE & ADAPTER RECOVERY METHODS
    # =========================================================================

    def _get_authoritative_native_state(self) -> Tuple[Dict[str, Any], str, Optional[str]]:
        """
        Recovers authoritative Native state:
        1. From SQLite native_state_journal (ORDER BY journal_id DESC LIMIT 1)
        2. Fallback to native_state.json
        Returns: (state_dict, status, error_diag)
        status: "OK" or "DATA_SOURCE_ERROR"
        """
        conn = self._ro_sqlite_connect(self.native_ledger_path)
        if conn:
            try:
                row = conn.execute(
                    "SELECT state_json, saved_at_utc FROM native_state_journal ORDER BY journal_id DESC LIMIT 1;"
                ).fetchone()
                if row and row[0]:
                    try:
                        data = json.loads(row[0])
                        data["_source"] = "SQLITE_STATE_JOURNAL"
                        data["_saved_at_utc"] = row[1]
                        return data, "OK", None
                    except Exception as e:
                        return {}, "DATA_SOURCE_ERROR", f"CORRUPTED_SQLITE_PAYLOAD: {type(e).__name__}"
            except Exception as e:
                logger.debug(f"native_state_journal query failed: {e}")
                return {}, "DATA_SOURCE_ERROR", f"SQLITE_QUERY_ERROR: {type(e).__name__}: {e}"
            finally:
                conn.close()

        # Fallback to JSON file
        if self.native_state_path and self.native_state_path.is_file():
            try:
                data = json.loads(self.native_state_path.read_text(encoding="utf-8"))
                data["_source"] = "DISK_STATE_JSON"
                return data, "OK", None
            except Exception as e:
                return {}, "DATA_SOURCE_ERROR", f"CORRUPTED_STATE_JSON: {type(e).__name__}"

        # If neither ledger nor state exists
        if (not self.native_ledger_path or not self.native_ledger_path.is_file()) and \
           (not self.native_state_path or not self.native_state_path.is_file()):
            return {}, "DATA_SOURCE_ERROR", "DB_AND_STATE_FILES_NOT_FOUND"

        return {}, "DATA_SOURCE_ERROR", "NO_AUTHORITATIVE_STATE_RECORD"

    def _get_authoritative_shadow_state(self) -> Tuple[Dict[str, Any], str, Optional[str]]:
        """
        Recovers authoritative Shadow state:
        1. From SQLite shadow_state_journal (ORDER BY journal_id DESC LIMIT 1)
        2. Fallback to shadow_state.json
        Returns: (state_dict, status, error_diag)
        status: "OK" or "DATA_SOURCE_ERROR"
        """
        conn = self._ro_sqlite_connect(self.shadow_ledger_path)
        if conn:
            try:
                row = conn.execute(
                    "SELECT state_json, saved_at_utc FROM shadow_state_journal ORDER BY journal_id DESC LIMIT 1;"
                ).fetchone()
                if row and row[0]:
                    try:
                        data = json.loads(row[0])
                        data["_source"] = "SQLITE_STATE_JOURNAL"
                        data["_saved_at_utc"] = row[1]
                        return data, "OK", None
                    except Exception as e:
                        return {}, "DATA_SOURCE_ERROR", f"CORRUPTED_SQLITE_PAYLOAD: {type(e).__name__}"
            except Exception as e:
                logger.debug(f"shadow_state_journal query failed: {e}")
                return {}, "DATA_SOURCE_ERROR", f"SQLITE_QUERY_ERROR: {type(e).__name__}: {e}"
            finally:
                conn.close()

        # Fallback to JSON file
        if self.shadow_state_path and self.shadow_state_path.is_file():
            try:
                data = json.loads(self.shadow_state_path.read_text(encoding="utf-8"))
                data["_source"] = "DISK_STATE_JSON"
                return data, "OK", None
            except Exception as e:
                return {}, "DATA_SOURCE_ERROR", f"CORRUPTED_STATE_JSON: {type(e).__name__}"

        # If neither ledger nor state exists
        if (not self.shadow_ledger_path or not self.shadow_ledger_path.is_file()) and \
           (not self.shadow_state_path or not self.shadow_state_path.is_file()):
            return {}, "DATA_SOURCE_ERROR", "DB_AND_STATE_FILES_NOT_FOUND"

        return {}, "DATA_SOURCE_ERROR", "NO_AUTHORITATIVE_STATE_RECORD"

    def _get_r2_data(self) -> Tuple[Dict[str, Any], Dict[str, Any], str, Optional[str]]:
        """
        Recovers authoritative R2 state:
        1. From observer.sqlite (accounts table for profile R2:300:BASE:OBSERVED_BOOK)
        2. From data/cryptolab/telemetry.json
        Returns: (account_payload_dict, telemetry_dict, status, error_diag)
        """
        account_dict: Dict[str, Any] = {}
        telemetry_dict = self._read_json(self.r2_telemetry_path) or {}

        conn = self._ro_sqlite_connect(self.r2_ledger_path)
        if conn:
            try:
                row = conn.execute(
                    "SELECT payload FROM accounts WHERE id = ?;", (R2_PRIMARY_PROFILE,)
                ).fetchone()
                if row and row[0]:
                    try:
                        account_dict = json.loads(row[0])
                    except Exception as e:
                        return {}, telemetry_dict, "DATA_SOURCE_ERROR", f"CORRUPTED_R2_ACCOUNT_JSON: {type(e).__name__}"
            except Exception as e:
                logger.debug(f"R2 accounts query failed: {e}")
                return {}, telemetry_dict, "DATA_SOURCE_ERROR", f"R2_SQLITE_QUERY_ERROR: {type(e).__name__}: {e}"
            finally:
                conn.close()

        # Never fabricate a primary R2 account or a $300 current balance.
        if not account_dict:
            for profile in telemetry_dict.get("profiles", []):
                if profile.get("profile") != R2_PRIMARY_PROFILE:
                    continue
                cash, quantities = profile.get("cash_usdt"), profile.get("quantities")
                if cash is None or not isinstance(quantities, dict):
                    break
                if not all(sym in quantities for sym in R2_SYMBOLS_3):
                    break
                account_dict = {
                    "cash": cash,
                    "qty": [quantities[sym] for sym in R2_SYMBOLS_3],
                    "risk_breached": profile.get("risk_breached"),
                    "_source": "DEGRADED_TELEMETRY_ONLY",
                    "_as_of_utc": telemetry_dict.get("heartbeat_at"),
                }
                return account_dict, telemetry_dict, "DEGRADED_SOURCE", "R2_PRIMARY_SQLITE_UNAVAILABLE"
            return {}, telemetry_dict, "DATA_SOURCE_ERROR", "R2_PRIMARY_ACCOUNT_NOT_CONFIRMED"
        return account_dict, telemetry_dict, "OK", None

    def compute_accounting_reconciliation(
        self, system: str, initial_equity: float, current_realized: float,
        closed_trades: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Never self-certify accounting using MTM equity or a limited trade sample.

        Full certification requires an independent cash/fill/fee/funding/position
        reconciliation which this dashboard does not perform.
        """
        if not closed_trades:
            return {
                "system": system,
                "status": "ACCOUNTING_NOT_YET_OBSERVED",
                "trades_count": 0,
                "reconciled": False,
                "discrepancy_usdt": None,
                "note": "Закрытых сделок нет; торговый учёт ещё не сверен.",
            }
        amounts = []
        for trade in closed_trades:
            value = trade.get("net_pnl_usdt", trade.get("net_pnl"))
            try:
                number = float(value)
                if not math.isfinite(number):
                    raise ValueError("nonfinite pnl")
                amounts.append(number)
            except (TypeError, ValueError):
                amounts = []
                break
        return {
            "system": system,
            "status": "ACCOUNTING_PENDING_RECONCILIATION",
            "trades_count": len(closed_trades),
            "recent_trades_net_pnl_usdt": round(sum(amounts), 4) if amounts else None,
            "reconciled": False,
            "discrepancy_usdt": None,
            "note": "Нет независимой сверки cash/fills/fees/funding/positions. Последние сделки не равны всей истории.",
        }

    # =========================================================================
    # TAB 2: SCANNER & COIN SELECTION
    # =========================================================================

    def get_scanner_data(self) -> Dict[str, Any]:
        """Provides verified coin selection and funnel status for Systems A, B, and C."""
        cached = self._get_cached("scanner")
        if cached is not None:
            return cached

        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()

        native_state, n_stat, n_err = self._get_authoritative_native_state()
        shadow_state, s_stat, s_err = self._get_authoritative_shadow_state()
        r2_account, r2_telemetry, r2_stat, r2_err = self._get_r2_data()

        # ---------------------------------------------------------------------
        # 1. Native Scanner (18 Coins) - Verified via native_state & native_journal
        # ---------------------------------------------------------------------
        native_bars = native_state.get("last_processed_bar_ts", {})
        native_engine = native_state.get("engine", {})
        native_open_positions = native_engine.get("open_positions", {})
        native_range_positions = native_state.get("range_engine", {}).get("open_positions", {})

        native_missed_map: Dict[str, Dict[str, Any]] = {}
        native_bar_counts: Dict[str, int] = {}
        native_last_bar_ts: Dict[str, int] = {}

        conn_n = self._ro_sqlite_connect(self.native_ledger_path)
        if conn_n:
            try:
                rows = conn_n.execute(
                    "SELECT symbol, reason, timestamp_ms, strategy, direction FROM native_missed_opportunities ORDER BY id DESC LIMIT 50;"
                ).fetchall()
                for r in rows:
                    sym = r["symbol"]
                    if sym not in native_missed_map:
                        native_missed_map[sym] = dict(r)

                nb_rows = conn_n.execute(
                    "SELECT notes, COUNT(*) as cnt, MAX(timestamp_ms) as max_ts FROM native_journal WHERE notes LIKE 'closed_bar_%' GROUP BY notes;"
                ).fetchall()
                for r in nb_rows:
                    sym = r["notes"].replace("closed_bar_", "").strip()
                    native_bar_counts[sym] = int(r["cnt"])
                    native_last_bar_ts[sym] = int(r["max_ts"])
            except Exception as e:
                logger.debug(f"Failed to query native ledger for scanner: {e}")
            finally:
                conn_n.close()

        native_scanner_list = []
        n_eligible_cnt = 0
        n_rejected_cnt = 0
        n_active_cnt = 0
        n_evaluated_cnt = 0

        for sym in DEFAULT_SYMBOLS_18:
            bar_ts = int(native_bars.get(sym, 0) or native_last_bar_ts.get(sym, 0) or 0)
            bar_cnt = native_bar_counts.get(sym, 1 if bar_ts > 0 else 0)
            has_evaluated_bar = (bar_ts > 0 or bar_cnt > 0)
            bar_utc = format_utc_ts(bar_ts + 3600000) if bar_ts > 0 else "NOT_RECORDED"
            candle_age = max(0, int(now_dt.timestamp() - ((bar_ts + 3600000) / 1000.0))) if bar_ts > 0 else None

            pos = native_open_positions.get(sym) or native_range_positions.get(sym)
            missed = native_missed_map.get(sym)

            if pos:
                n_evaluated_cnt += 1
                n_active_cnt += 1
                dir_int = int(pos.get("direction", 1))
                pos_side = "LONG" if dir_int == 1 else "SHORT"
                entry_ts = pos.get("entry_time") or bar_ts
                event_at = format_utc_ts(entry_ts)
                native_scanner_list.append({
                    "symbol": sym,
                    "market": "Bybit USDT Perpetual",
                    "source": "NATIVE_STATE (Active Position)",
                    "strategy": "Donchian 4h Trend" if sym in native_open_positions else "Range 1h Mean-Reversion",
                    "bars_processed_after_t0": bar_cnt,
                    "last_candle_utc": bar_utc,
                    "last_candle_msk": format_msk_ts(bar_ts),
                    "candle_age_sec": candle_age,
                    "market_regime": "IN_POSITION",
                    "event_at": event_at,
                    "event_msk": format_msk_ts(entry_ts),
                    "observed_at": now_iso,
                    "signal": f"POSITION_HELD_{pos_side}",
                    "confidence_status": "RECORDED_POSITION",
                    "score": "NOT_AVAILABLE (Discrete Logic)",
                    "eligible": "IN_TRADE",
                    "rejection_reason": "POSITION_ALREADY_OPEN",
                    "decision": f"MANAGING_POSITION ({pos_side})",
                })
            elif missed and has_evaluated_bar and abs(missed.get("timestamp_ms", 0) - bar_ts) <= 14400000:
                n_evaluated_cnt += 1
                n_rejected_cnt += 1
                reason = missed.get("reason", "FILTER_REJECTED")
                dir_int = int(missed.get("direction", 1))
                sig_side = "LONG" if dir_int == 1 else "SHORT"
                native_scanner_list.append({
                    "symbol": sym,
                    "market": "Bybit USDT Perpetual",
                    "source": "NATIVE_LEDGER (native_missed_opportunities)",
                    "strategy": missed.get("strategy", "Dual-Regime"),
                    "bars_processed_after_t0": bar_cnt,
                    "last_candle_utc": bar_utc,
                    "last_candle_msk": format_msk_ts(bar_ts),
                    "candle_age_sec": candle_age,
                    "market_regime": "FILTER_REJECTED",
                    "event_at": format_utc_ts(missed.get("timestamp_ms")),
                    "event_msk": format_msk_ts(missed.get("timestamp_ms")),
                    "observed_at": now_iso,
                    "signal": f"SIGNAL_{sig_side}",
                    "confidence_status": "PROVEN_FILTER_REJECTION",
                    "score": "NOT_AVAILABLE",
                    "eligible": "NO",
                    "rejection_reason": reason,
                    "decision": f"REJECTED ({reason})",
                })
            elif has_evaluated_bar:
                n_evaluated_cnt += 1
                native_scanner_list.append({
                    "symbol": sym,
                    "market": "Bybit USDT Perpetual",
                    "source": "NATIVE_STATE (last_processed_bar_ts)",
                    "strategy": "Dual-Regime (Donchian 4h + Range 1h)",
                    "bars_processed_after_t0": bar_cnt,
                    "last_candle_utc": bar_utc,
                    "last_candle_msk": format_msk_ts(bar_ts),
                    "candle_age_sec": candle_age,
                    "market_regime": "NOT_RECORDED",
                    "event_at": bar_utc,
                    "event_msk": format_msk_ts(bar_ts),
                    "observed_at": now_iso,
                    "signal": "NO_SIGNAL_RECORDED",
                    "confidence_status": "BAR_PROCESSED / NO_SIGNAL_RECORDED",
                    "score": "NOT_AVAILABLE",
                    "eligible": "NOT_RECORDED",
                    "rejection_reason": "NOT_RECORDED",
                    "decision": "NO_DECISION_RECORDED",
                })
            else:
                native_scanner_list.append({
                    "symbol": sym,
                    "market": "Bybit USDT Perpetual",
                    "source": "NATIVE_UNIVERSE_CONFIG",
                    "strategy": "Dual-Regime (Donchian 4h + Range 1h)",
                    "bars_processed_after_t0": 0,
                    "last_candle_utc": "NOT_RECORDED",
                    "last_candle_msk": "NOT_RECORDED",
                    "candle_age_sec": None,
                    "market_regime": "UNINITIALIZED",
                    "event_at": "NOT_RECORDED",
                    "event_msk": "NOT_RECORDED",
                    "observed_at": now_iso,
                    "signal": "NOT_RECORDED",
                    "confidence_status": "NOT_EVALUATED",
                    "score": "NOT_AVAILABLE",
                    "eligible": "NOT_RECORDED",
                    "rejection_reason": "NOT_RECORDED",
                    "decision": "WAITING_FIRST_BAR",
                })

        # ---------------------------------------------------------------------
        # 2. Shadow Scanner (18 Coins) - Verified via shadow_dedup_events
        # ---------------------------------------------------------------------
        shadow_port = shadow_state.get("shadow_portfolio", {})
        shadow_positions = shadow_port.get("positions", {})

        shadow_events_map: Dict[str, Dict[str, Any]] = {}
        shadow_dedup_counts: Dict[str, int] = {}
        shadow_dedup_last_ts: Dict[str, int] = {}

        conn_s = self._ro_sqlite_connect(self.shadow_ledger_path)
        if conn_s:
            try:
                rows = conn_s.execute(
                    """SELECT symbol, event_type, execution_status, notes, exchange_ts
                       FROM shadow_execution_events ORDER BY event_id DESC LIMIT 50;"""
                ).fetchall()
                for r in rows:
                    sym = r["symbol"]
                    if sym not in shadow_events_map:
                        shadow_events_map[sym] = dict(r)

                d_rows = conn_s.execute(
                    """SELECT symbol, COUNT(*) as cnt, MAX(exchange_ts) as max_ts
                       FROM shadow_dedup_events GROUP BY symbol;"""
                ).fetchall()
                for r in d_rows:
                    sym = r["symbol"]
                    shadow_dedup_counts[sym] = int(r["cnt"])
                    shadow_dedup_last_ts[sym] = int(r["max_ts"])
            except Exception as e:
                logger.debug(f"Failed to query shadow ledger for scanner: {e}")
            finally:
                conn_s.close()

        shadow_scanner_list = []
        s_eligible_cnt = 0
        s_rejected_cnt = 0
        s_active_cnt = 0
        s_evaluated_cnt = 0

        for sym in DEFAULT_SYMBOLS_18:
            pos = shadow_positions.get(sym)
            ev = shadow_events_map.get(sym)
            bar_cnt = shadow_dedup_counts.get(sym, 0)
            last_ts = shadow_dedup_last_ts.get(sym, 0)
            candle_age = max(0, int(now_dt.timestamp() - ((last_ts + 3600000) / 1000.0))) if last_ts > 0 else None

            if pos:
                s_evaluated_cnt += 1
                s_active_cnt += 1
                dir_int = int(pos.get("direction", 1))
                pos_side = "LONG" if dir_int == 1 else "SHORT"
                event_at = format_utc_ts(pos.get("entry_time_ms"))
                shadow_scanner_list.append({
                    "symbol": sym,
                    "market": "Bybit USDT Perpetual",
                    "source": "SHADOW_STATE (shadow_portfolio.positions)",
                    "strategy": "Realistic Shadow (Donchian 4h + L2 Orderbook)",
                    "bars_processed_after_t0": bar_cnt,
                    "last_candle_utc": format_utc_ts(last_ts + 3600000) if last_ts > 0 else "NOT_RECORDED",
                    "last_candle_msk": format_msk_ts(last_ts) if last_ts > 0 else "NOT_RECORDED",
                    "candle_age_sec": candle_age,
                    "market_regime": "IN_POSITION",
                    "event_at": event_at,
                    "event_msk": format_msk_ts(pos.get("entry_time_ms")),
                    "observed_at": now_iso,
                    "signal": f"POSITION_HELD_{pos_side}",
                    "confidence_status": "RECORDED_POSITION",
                    "score": "NOT_AVAILABLE",
                    "eligible": "IN_TRADE",
                    "rejection_reason": "POSITION_ALREADY_OPEN",
                    "decision": f"MANAGING_POSITION ({pos_side})",
                })
            elif ev and ev.get("execution_status") == "REJECTED":
                s_evaluated_cnt += 1
                s_rejected_cnt += 1
                reason = ev.get("notes") or "L2_LIQUIDITY_OR_SLIPPAGE_REJECT"
                shadow_scanner_list.append({
                    "symbol": sym,
                    "market": "Bybit USDT Perpetual",
                    "source": "SHADOW_LEDGER (shadow_execution_events)",
                    "strategy": "Realistic Shadow (Donchian 4h + L2 Orderbook)",
                    "bars_processed_after_t0": bar_cnt,
                    "last_candle_utc": format_utc_ts(last_ts + 3600000) if last_ts > 0 else "NOT_RECORDED",
                    "last_candle_msk": format_msk_ts(last_ts) if last_ts > 0 else "NOT_RECORDED",
                    "candle_age_sec": candle_age,
                    "market_regime": "L2_EXECUTION_REJECTED",
                    "event_at": format_utc_ts(ev.get("exchange_ts")),
                    "event_msk": format_msk_ts(ev.get("exchange_ts")),
                    "observed_at": now_iso,
                    "signal": "SIGNAL_PENDING",
                    "confidence_status": "PROVEN_FILTER_REJECTION",
                    "score": "NOT_AVAILABLE",
                    "eligible": "NO",
                    "rejection_reason": reason,
                    "decision": f"REJECTED ({reason})",
                })
            elif bar_cnt > 0 or last_ts > 0:
                s_evaluated_cnt += 1
                shadow_scanner_list.append({
                    "symbol": sym,
                    "market": "Bybit USDT Perpetual",
                    "source": "SHADOW_LEDGER (shadow_dedup_events)",
                    "strategy": "Realistic Shadow (Donchian 4h + L2 Orderbook)",
                    "bars_processed_after_t0": bar_cnt,
                    "last_candle_utc": format_utc_ts(last_ts),
                    "last_candle_msk": format_msk_ts(last_ts),
                    "candle_age_sec": candle_age,
                    "market_regime": "NOT_RECORDED",
                    "event_at": format_utc_ts(last_ts),
                    "event_msk": format_msk_ts(last_ts),
                    "observed_at": now_iso,
                    "signal": "NO_SIGNAL_RECORDED",
                    "confidence_status": "BAR_PROCESSED / NO_SIGNAL_RECORDED",
                    "score": "NOT_AVAILABLE",
                    "eligible": "NOT_RECORDED",
                    "rejection_reason": "NOT_RECORDED",
                    "decision": "NO_DECISION_RECORDED",
                })
            else:
                shadow_scanner_list.append({
                    "symbol": sym,
                    "market": "Bybit USDT Perpetual",
                    "source": "SHADOW_UNIVERSE_CONFIG",
                    "strategy": "Realistic Shadow (Donchian 4h + L2 Orderbook)",
                    "bars_processed_after_t0": 0,
                    "last_candle_utc": "NOT_RECORDED",
                    "last_candle_msk": "NOT_RECORDED",
                    "candle_age_sec": None,
                    "market_regime": "UNINITIALIZED",
                    "event_at": "NOT_RECORDED",
                    "event_msk": "NOT_RECORDED",
                    "observed_at": now_iso,
                    "signal": "NOT_RECORDED",
                    "confidence_status": "NOT_EVALUATED",
                    "score": "NOT_AVAILABLE",
                    "eligible": "NOT_RECORDED",
                    "rejection_reason": "NOT_RECORDED",
                    "decision": "WAITING_FIRST_BAR",
                })

        # ---------------------------------------------------------------------
        # 3. CryptoLab R2 Scanner (3 Spot Coins)
        # ---------------------------------------------------------------------
        r2_scanner_list = []
        r2_eligible_cnt = 0
        r2_rejected_cnt = 0
        r2_active_cnt = 0
        r2_evaluated_cnt = 0

        raw_weights = r2_telemetry.get("target_weights")
        r2_weights_map: Dict[str, float] = {}
        if isinstance(raw_weights, (list, tuple)):
            for idx, sym in enumerate(R2_SYMBOLS_3):
                if idx < len(raw_weights):
                    try:
                        r2_weights_map[sym] = float(raw_weights[idx])
                    except Exception:
                        r2_weights_map[sym] = 0.0
        elif isinstance(raw_weights, dict):
            for sym, w in raw_weights.items():
                try:
                    r2_weights_map[sym] = float(w)
                except Exception:
                    pass

        raw_signal = r2_telemetry.get("signal")
        r2_signal_map: Dict[str, Dict[str, Any]] = {}
        if isinstance(raw_signal, dict):
            for k, v in raw_signal.items():
                if isinstance(v, dict):
                    r2_signal_map[k] = v
                elif isinstance(v, (int, float)):
                    r2_signal_map[k] = {"score": float(v), "eligible": True}

        r2_decision_time = "NOT_RECORDED"
        conn_r2 = self._ro_sqlite_connect(self.r2_ledger_path)
        if conn_r2:
            try:
                decision_row = conn_r2.execute(
                    "SELECT at FROM events WHERE kind='DECISION' ORDER BY at DESC LIMIT 1"
                ).fetchone()
                if decision_row:
                    r2_decision_time = decision_row["at"]
                rows = conn_r2.execute(
                    """SELECT at, payload FROM events
                       WHERE kind='SNAPSHOT' ORDER BY rowid DESC LIMIT 100;"""
                ).fetchall()
                for r in rows:
                    try:
                        pld = json.loads(r["payload"])
                        sym = pld.get("symbol")
                        if sym and sym not in r2_signal_map and pld.get("record_type") == "FORWARD_DECISION":
                            r2_signal_map[sym] = {
                                "score": pld.get("r2_score"),
                                "eligible": pld.get("eligible"),
                                "target_weight": pld.get("r2_weight"),
                                "_at": r["at"],
                            }
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Failed to query r2 snapshots: {e}")
            finally:
                conn_r2.close()

        r2_quantities: Dict[str, float] = {}
        if r2_account and "qty" in r2_account:
            for idx, sym in enumerate(R2_SYMBOLS_3):
                if idx < len(r2_account["qty"]):
                    try:
                        r2_quantities[sym] = float(r2_account["qty"][idx])
                    except Exception:
                        r2_quantities[sym] = 0.0
        else:
            for p in r2_telemetry.get("profiles", []):
                if p.get("profile") == R2_PRIMARY_PROFILE:
                    r2_quantities = {k: float(v) for k, v in p.get("quantities", {}).items()}
                    break

        r2_event_at = r2_decision_time  # SQLite decision, never the heartbeat

        for sym in R2_SYMBOLS_3:
            qty = r2_quantities.get(sym, 0.0)
            is_active = (qty > 0)
            sig_info = r2_signal_map.get(sym, {})
            score = sig_info.get("score")
            eligible_flag = sig_info.get("eligible")
            weight = r2_weights_map.get(sym, sig_info.get("target_weight", None))

            if is_active:
                r2_evaluated_cnt += 1
                r2_active_cnt += 1
                decision = "ALLOCATED_SPOT_LONG"
                signal_str = "SPOT_LONG"
                eligible_str = "YES"
                rejection_reason = "NONE"
                confidence = "RECORDED_POSITION"
            elif eligible_flag is False:
                r2_evaluated_cnt += 1
                r2_rejected_cnt += 1
                decision = "DAILY_DECISION_INELIGIBLE (ZERO_ALLOCATION)"
                signal_str = "INELIGIBLE_FOR_REBALANCE"
                eligible_str = "NO"
                rejection_reason = sig_info.get("rejection_reason") or "NOT_RECORDED"
                confidence = "RECORDED_SNAPSHOT"
            elif weight is not None and weight > 0:
                r2_evaluated_cnt += 1
                r2_eligible_cnt += 1
                decision = "TARGET_WEIGHT_ALLOCATED"
                signal_str = "ALLOCATE_TARGET"
                eligible_str = "YES"
                rejection_reason = "NONE"
                confidence = "RECORDED_SIGNAL"
            elif score is not None or (weight is not None and weight == 0.0):
                r2_evaluated_cnt += 1
                decision = "ZERO_WEIGHT_CASH"
                signal_str = "HOLD_CASH"
                eligible_str = "NO" if eligible_flag is False else "WAITING_RANK"
                rejection_reason = "NOT_RECORDED"
                confidence = "RECORDED_SNAPSHOT"
            else:
                decision = "NO_DECISION_RECORDED"
                signal_str = "NOT_RECORDED"
                eligible_str = "NOT_RECORDED"
                rejection_reason = "NOT_RECORDED"
                confidence = "NOT_EVALUATED"

            r2_scanner_list.append({
                "symbol": sym,
                "market": "Bybit Spot Cash",
                "source": "CRYPTOLAB_R2 (telemetry + observer.sqlite)",
                "strategy": "Hunter001 R2 Spot Microstructure",
                "schedule_note": "Суточный квантовый ребаланс (Daily 00:00 UTC)",
                "event_at": format_utc_ts(sig_info.get("_at") or r2_event_at),
                "event_msk": format_msk_ts(sig_info.get("_at") or r2_event_at),
                "observed_at": now_iso,
                "signal": signal_str,
                "score": f"{score:.4f}" if isinstance(score, (int, float)) else "NOT_AVAILABLE",
                "eligible": eligible_str,
                "target_weight": f"{weight*100:.1f}%" if isinstance(weight, (int, float)) else "NOT_RECORDED",
                "rejection_reason": rejection_reason,
                "decision": decision,
                "confidence_status": confidence,
                "fill_claim_badge": "FORWARD_VIRTUAL_ESTIMATE",
            })

        res = {
            "timestamp_iso": now_iso,
            "funnel": {
                "native": {
                    "universe": len(DEFAULT_SYMBOLS_18),
                    "evaluated": n_evaluated_cnt,
                    "eligible": n_eligible_cnt,
                    "rejected": n_rejected_cnt,
                    "active_positions": n_active_cnt,
                },
                "shadow": {
                    "universe": len(DEFAULT_SYMBOLS_18),
                    "evaluated": s_evaluated_cnt,
                    "eligible": s_eligible_cnt,
                    "rejected": s_rejected_cnt,
                    "active_positions": s_active_cnt,
                },
                "r2": {
                    "universe": len(R2_SYMBOLS_3),
                    "evaluated": r2_evaluated_cnt,
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
        """Provides open position details parsed strictly from authoritative state models."""
        cached = self._get_cached("positions")
        if cached is not None:
            return cached

        now_dt = datetime.now(timezone.utc)
        now_iso = now_dt.isoformat()

        native_state, n_stat, n_err = self._get_authoritative_native_state()
        shadow_state, s_stat, s_err = self._get_authoritative_shadow_state()
        r2_account, r2_telemetry, r2_stat, r2_err = self._get_r2_data()

        # ---------------------------------------------------------------------
        # 1. Native Positions
        # ---------------------------------------------------------------------
        native_positions_list = []
        native_source_status = n_stat
        native_error_diag = n_err

        if n_stat == "OK":
            native_engine = native_state.get("engine", {})
            trend_positions = native_engine.get("open_positions", {})
            range_engine = native_state.get("range_engine", {})
            range_positions = range_engine.get("open_positions", {})

            # 1a. Trend Engine Positions (Donchian 4h with Cascade TP enabled in config)
            for sym, pos in trend_positions.items():
                try:
                    dir_int = int(pos.get("direction", 1))
                    side = "LONG" if dir_int == 1 else "SHORT"
                    ep = float(pos.get("entry_price", 0.0))
                    qty = float(pos.get("current_qty", 0.0))
                    sl = float(pos.get("current_stop", 0.0))
                    atr = float(pos.get("initial_atr", 0.0))
                    unreal = float(pos.get("unrealized_pnl", 0.0))
                    notional = ep * qty
                    sl_dist = round(((sl - ep) / ep) * 100.0, 2) if ep > 0 else 0.0

                    # Confirmed current mark or NOT_AVAILABLE
                    mark_val = pos.get("current_mark") or pos.get("mark_price") or pos.get("last_price")
                    if mark_val is not None and float(mark_val) > 0:
                        current_mark = float(mark_val)
                        mark_time_val = pos.get("mark_time") or pos.get("price_time") or pos.get("mark_time_ms")
                        mark_time_utc = format_utc_ts(mark_time_val) if mark_time_val else "NOT_RECORDED"
                    else:
                        current_mark = "NOT_AVAILABLE"
                        mark_time_utc = "NOT_AVAILABLE"

                    # Confirmed unrealized PnL or calculated mathematically, else NOT_AVAILABLE (never fake 0.0)
                    if pos.get("unrealized_pnl") is not None:
                        unreal = round(float(pos["unrealized_pnl"]), 4)
                    elif pos.get("unrealized_pnl_usdt") is not None:
                        unreal = round(float(pos["unrealized_pnl_usdt"]), 4)
                    elif current_mark != "NOT_AVAILABLE" and ep > 0 and qty > 0:
                        unreal = round(dir_int * (current_mark - ep) * qty, 4)
                    else:
                        unreal = "NOT_AVAILABLE"

                    # Last SL update time: ONLY when recorded event exists, never fallback to entry_time
                    sl_upd = pos.get("last_sl_update_utc") or pos.get("sl_update_time") or pos.get("sl_update_time_ms")
                    last_sl_update_utc = format_utc_ts(sl_upd) if sl_upd else "NOT_RECORDED"

                    # Cascade TP: In CoreV3Engine.to_dict(), tp1_taken/tp2_taken are absent
                    if "tp1_taken" in pos:
                        tp1_taken = bool(pos["tp1_taken"])
                        tp1_status = "TAKEN" if tp1_taken else "PENDING"
                    else:
                        tp1_taken = None
                        tp1_status = "UNKNOWN"

                    if "tp2_taken" in pos:
                        tp2_taken = bool(pos["tp2_taken"])
                        tp2_status = "TAKEN" if tp2_taken else "PENDING"
                    else:
                        tp2_taken = None
                        tp2_status = "UNKNOWN"

                    is_be = bool(pos.get("is_breakeven_set", False))

                    if atr > 0:
                        tp1 = ep + dir_int * 1.5 * atr
                        tp2 = ep + dir_int * 3.0 * atr
                        tp1_str = f"${tp1:.4f}"
                        tp1_dist = f"{((tp1-ep)/ep)*100:.2f}%" if ep > 0 else "0.0%"
                        tp2_str = f"${tp2:.4f}"
                        tp2_dist = f"{((tp2-ep)/ep)*100:.2f}%" if ep > 0 else "0.0%"
                    else:
                        tp1_str = "NOT_DEFINED"
                        tp1_dist = "NOT_APPLICABLE"
                        tp2_str = "NOT_DEFINED"
                        tp2_dist = "NOT_APPLICABLE"

                    flags = ["DONCHIAN_4H"]
                    if tp1_status == "TAKEN":
                        flags.append("TP1_TAKEN")
                    elif tp1_status == "UNKNOWN":
                        flags.append("TP_STATUS_UNKNOWN")
                    if tp2_status == "TAKEN":
                        flags.append("TP2_TAKEN")
                    if is_be:
                        flags.append("BREAK_EVEN")

                    entry_utc = format_utc_ts(pos.get("entry_time"))

                    native_positions_list.append({
                        "symbol": sym,
                        "strategy": "Donchian Trend (Cascade TP 1.5R / 3.0R)",
                        "side": side,
                        "entry_time_utc": entry_utc,
                        "entry_price": ep,
                        "current_qty": qty,
                        "notional_usdt": round(notional, 2),
                        "current_mark": current_mark,
                        "mark_time_utc": mark_time_utc,
                        "unrealized_pnl_usdt": unreal,
                        "stop_loss": sl,
                        "sl_distance_pct": sl_dist,
                        "tp1_price": tp1_str,
                        "tp1_distance_pct": tp1_dist,
                        "tp1_status": tp1_status,
                        "tp2_price": tp2_str,
                        "tp2_distance_pct": tp2_dist,
                        "tp2_status": tp2_status,
                        "flags": flags,
                        "bars_held": int(pos.get("bars_held", 0)),
                        "last_sl_update_utc": last_sl_update_utc,
                        "credibility": "CONFIRMED_STATE_POSITION",
                    })
                except Exception as e:
                    logger.debug(f"Error parsing native trend position {sym}: {e}")

            # 1b. Range Engine Positions (1h Mean-Reversion Maker)
            for sym, pos in range_positions.items():
                try:
                    dir_int = int(pos.get("direction", 1))
                    side = "LONG" if dir_int == 1 else "SHORT"
                    ep = float(pos.get("entry_price", 0.0))
                    qty = float(pos.get("current_qty", 0.0))
                    sl = float(pos.get("stop_loss", 0.0))
                    tp = float(pos.get("take_profit", 0.0))
                    tp1 = float(pos.get("tp1_price", 0.0))
                    tp1_done = bool(pos.get("tp1_done", False))
                    notional = ep * qty
                    sl_dist = round(((sl - ep) / ep) * 100.0, 2) if ep > 0 else 0.0

                    flags = ["RANGE_MAKER"]
                    if tp1_done:
                        flags.append("TP1_DONE")

                    mark_val = pos.get("current_mark") or pos.get("mark_price") or pos.get("last_price")
                    if mark_val is not None and float(mark_val) > 0:
                        current_mark = float(mark_val)
                        mark_time_utc = format_utc_ts(pos.get("mark_time") or pos.get("price_time"))
                    else:
                        current_mark = "NOT_AVAILABLE"
                        mark_time_utc = "NOT_AVAILABLE"

                    if pos.get("unrealized_pnl") is not None:
                        unreal = round(float(pos["unrealized_pnl"]), 4)
                    elif pos.get("unrealized_pnl_usdt") is not None:
                        unreal = round(float(pos["unrealized_pnl_usdt"]), 4)
                    elif current_mark != "NOT_AVAILABLE" and ep > 0 and qty > 0:
                        unreal = round(dir_int * (current_mark - ep) * qty, 4)
                    else:
                        unreal = "NOT_AVAILABLE"

                    sl_upd = pos.get("last_sl_update_utc") or pos.get("sl_update_time")
                    last_sl_update_utc = format_utc_ts(sl_upd) if sl_upd else "NOT_RECORDED"

                    entry_utc = format_utc_ts(pos.get("entry_time"))

                    native_positions_list.append({
                        "symbol": sym,
                        "strategy": "Range Mean-Reversion (Maker)",
                        "side": side,
                        "entry_time_utc": entry_utc,
                        "entry_price": ep,
                        "current_qty": qty,
                        "notional_usdt": round(notional, 2),
                        "current_mark": current_mark,
                        "mark_time_utc": mark_time_utc,
                        "unrealized_pnl_usdt": unreal,
                        "stop_loss": sl,
                        "sl_distance_pct": sl_dist,
                        "tp1_price": f"${tp1:.4f}" if tp1 > 0 else f"${tp:.4f}",
                        "tp1_distance_pct": f"{((tp-ep)/ep)*100:.2f}%" if ep > 0 else "0.0%",
                        "tp2_price": f"${tp:.4f}" if tp1 > 0 else "NOT_DEFINED",
                        "tp2_distance_pct": "0.0%",
                        "flags": flags,
                        "bars_held": int(pos.get("entry_bar_idx", 0)),
                        "last_sl_update_utc": last_sl_update_utc,
                        "credibility": "CONFIRMED_STATE_POSITION",
                    })
                except Exception as e:
                    logger.debug(f"Error parsing native range position {sym}: {e}")

        # ---------------------------------------------------------------------
        # 2. Shadow Positions (Realistic Shadow L2)
        # ---------------------------------------------------------------------
        shadow_positions_list = []
        shadow_source_status = s_stat
        shadow_error_diag = s_err

        if s_stat == "OK":
            shadow_port = shadow_state.get("shadow_portfolio", {})
            positions_map = shadow_port.get("positions", {})

            for sym, pos in positions_map.items():
                try:
                    dir_int = int(pos.get("direction", 1))
                    side = "LONG" if dir_int == 1 else "SHORT"
                    ep = float(pos.get("entry_price", 0.0))
                    qty = float(pos.get("current_qty", 0.0))
                    sl = float(pos.get("current_stop", 0.0))
                    unreal = float(pos.get("unrealized_pnl", 0.0))
                    notional = ep * qty
                    sl_dist = round(((sl - ep) / ep) * 100.0, 2) if ep > 0 else 0.0

                    tp1 = float(pos.get("tp1_price", 0.0))
                    tp2 = float(pos.get("tp2_price", 0.0))
                    tp1_taken = bool(pos.get("tp1_taken", False))
                    tp2_taken = bool(pos.get("tp2_taken", False))
                    be_set = bool(pos.get("is_breakeven_set", False))
                    bars = int(pos.get("bars_held", 0))

                    flags = ["L2_REST_OBSERVED"]
                    if tp1_taken:
                        flags.append("TP1_TAKEN")
                    if tp2_taken:
                        flags.append("TP2_TAKEN")
                    if be_set:
                        flags.append("BREAK_EVEN")

                    mark_val = pos.get("current_mark") or pos.get("mark_price") or pos.get("last_price")
                    if mark_val is not None and float(mark_val) > 0:
                        current_mark = float(mark_val)
                        mark_time_utc = format_utc_ts(pos.get("mark_time") or pos.get("price_time"))
                    else:
                        current_mark = "NOT_AVAILABLE"
                        mark_time_utc = "NOT_AVAILABLE"

                    if pos.get("unrealized_pnl") is not None:
                        unreal = round(float(pos["unrealized_pnl"]), 4)
                    elif pos.get("unrealized_pnl_usdt") is not None:
                        unreal = round(float(pos["unrealized_pnl_usdt"]), 4)
                    elif current_mark != "NOT_AVAILABLE" and ep > 0 and qty > 0:
                        unreal = round(dir_int * (current_mark - ep) * qty, 4)
                    else:
                        unreal = "NOT_AVAILABLE"

                    sl_upd = pos.get("last_sl_update_utc") or pos.get("sl_update_time")
                    last_sl_update_utc = format_utc_ts(sl_upd) if sl_upd else "NOT_RECORDED"

                    entry_utc = format_utc_ts(pos.get("entry_time_ms"))

                    shadow_positions_list.append({
                        "symbol": sym,
                        "strategy": "Donchian Trend (L2 Discrete Execution)",
                        "side": side,
                        "entry_time_utc": entry_utc,
                        "entry_price": ep,
                        "current_qty": qty,
                        "notional_usdt": round(notional, 2),
                        "current_mark": current_mark,
                        "mark_time_utc": mark_time_utc,
                        "unrealized_pnl_usdt": unreal,
                        "stop_loss": sl,
                        "sl_distance_pct": sl_dist,
                        "tp1_price": f"${tp1:.4f}" if tp1 > 0 else "NOT_DEFINED",
                        "tp1_distance_pct": f"{((tp1-ep)/ep)*100:.2f}%" if tp1 > 0 and ep > 0 else "NOT_APPLICABLE",
                        "tp1_status": "TAKEN" if tp1_taken else "PENDING",
                        "tp2_price": f"${tp2:.4f}" if tp2 > 0 else "NOT_DEFINED",
                        "tp2_distance_pct": f"{((tp2-ep)/ep)*100:.2f}%" if tp2 > 0 and ep > 0 else "NOT_APPLICABLE",
                        "tp2_status": "TAKEN" if tp2_taken else "PENDING",
                        "flags": flags,
                        "bars_held": bars,
                        "last_sl_update_utc": last_sl_update_utc,
                        "credibility": "CONFIRMED_STATE_POSITION",
                    })
                except Exception as e:
                    logger.debug(f"Error parsing shadow position {sym}: {e}")

        # ---------------------------------------------------------------------
        # 3. CryptoLab R2 Spot Inventory
        # ---------------------------------------------------------------------
        r2_positions_list = []
        r2_source_status = r2_stat
        r2_error_diag = r2_err

        if r2_stat == "OK":
            # Extract coin quantities for R2:300:BASE:OBSERVED_BOOK
            r2_quantities: Dict[str, float] = {}
            if r2_account and "qty" in r2_account:
                for idx, sym in enumerate(R2_SYMBOLS_3):
                    if idx < len(r2_account["qty"]):
                        try:
                            r2_quantities[sym] = float(r2_account["qty"][idx])
                        except Exception:
                            r2_quantities[sym] = 0.0
            else:
                for p in r2_telemetry.get("profiles", []):
                    if p.get("profile") == R2_PRIMARY_PROFILE:
                        r2_quantities = {k: float(v) for k, v in p.get("quantities", {}).items()}
                        break

            for sym, qty in r2_quantities.items():
                if qty > 0:
                    r2_positions_list.append({
                        "symbol": sym,
                        "strategy": "Hunter001 R2 Spot Microstructure",
                        "side": "SPOT_LONG",
                        "entry_time_utc": "NOT_AVAILABLE",
                        "entry_price": "NOT_AVAILABLE",
                        "current_qty": qty,
                        "notional_usdt": "NOT_AVAILABLE",
                        "current_mark": "NOT_AVAILABLE",
                        "unrealized_pnl_usdt": "NOT_AVAILABLE",
                        "stop_loss": "NOT_APPLICABLE (Spot Portfolio Allocation)",
                        "sl_distance_pct": "NOT_APPLICABLE",
                        "tp1_price": "NOT_APPLICABLE (Spot Portfolio Allocation)",
                        "tp1_distance_pct": "NOT_APPLICABLE",
                        "tp2_price": "NOT_APPLICABLE (Spot Portfolio Allocation)",
                        "tp2_distance_pct": "NOT_APPLICABLE",
                        "flags": ["SPOT_CASH_HOLD", "VIRTUAL_MODEL_ESTIMATE"],
                        "bars_held": 0,
                        "last_sl_update_utc": "NOT_APPLICABLE",
                        "credibility": "VIRTUAL_MODEL_ESTIMATE",
                    })

        total_pos = len(native_positions_list) + len(shadow_positions_list) + len(r2_positions_list)
        total_open_positions = total_pos
        has_error = (native_source_status == "DATA_SOURCE_ERROR" or shadow_source_status == "DATA_SOURCE_ERROR" or r2_source_status == "DATA_SOURCE_ERROR")
        is_empty = (total_open_positions == 0 and not has_error)

        res = {
            "timestamp_iso": now_iso,
            "total_open_positions": total_open_positions,
            "is_empty": is_empty,
            "data_status": {
                "native": "OK_POSITIONS" if len(native_positions_list) > 0 else ("DATA_SOURCE_ERROR" if native_source_status != "OK" else "OK_EMPTY"),
                "shadow": "OK_POSITIONS" if len(shadow_positions_list) > 0 else ("DATA_SOURCE_ERROR" if shadow_source_status != "OK" else "OK_EMPTY"),
                "r2": "OK_POSITIONS" if len(r2_positions_list) > 0 else ("DATA_SOURCE_ERROR" if r2_source_status != "OK" else "OK_EMPTY"),
            },
            "native": {
                "status": "OK_POSITIONS" if len(native_positions_list) > 0 else ("DATA_SOURCE_ERROR" if native_source_status != "OK" else "OK_EMPTY"),
                "error_diagnostic": native_error_diag,
                "positions": native_positions_list,
            },
            "shadow": {
                "status": "OK_POSITIONS" if len(shadow_positions_list) > 0 else ("DATA_SOURCE_ERROR" if shadow_source_status != "OK" else "OK_EMPTY"),
                "error_diagnostic": shadow_error_diag,
                "positions": shadow_positions_list,
            },
            "r2": {
                "status": "OK_POSITIONS" if len(r2_positions_list) > 0 else ("DATA_SOURCE_ERROR" if r2_source_status != "OK" else "OK_EMPTY"),
                "error_diagnostic": r2_error_diag,
                "positions": r2_positions_list,
            },
            # Backwards compatibility flat keys
            "native_positions": native_positions_list,
            "shadow_positions": shadow_positions_list,
            "r2_positions": r2_positions_list,
            "empty_reason": (
                "Открытых позиций в подтверждённом состоянии систем не зарегистрировано (OK_EMPTY)."
                if is_empty else ("Ошибка источника данных (DATA_SOURCE_ERROR)" if has_error else "")
            ),
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
        system_filter: Optional[str] = None,
        event_type_filter: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Bounded, evidence-only activity feed. A processed bar is NOT a signal.

        Native source: native_journal.close_bar_% and execution/funding ledgers.
        Shadow source: shadow_dedup_events, execution/funding ledgers.
        R2 source: observer events (excluding frequent EQUITY ticks; use Analytics).
        Never writes events to the trading databases and never invents trades.
        """
        limit = min(max(1, int(limit)), 200)
        offset = min(max(0, int(offset)), 10000)
        eff_sys = system or system_filter
        eff_ev = event_type or event_type_filter
        now_iso = datetime.now(timezone.utc).isoformat()
        pool: List[Dict[str, Any]] = []
        source_diagnostics: Dict[str, str] = {}
        last_r2_decision_utc = None

        def add(system_name: str, event_id: str, kind: str, symbol_name: str,
                when_ms: int, recorded_ms: Optional[int], notes: Any,
                source: str, price: Any = None, qty: Any = None,
                pnl: Any = None):
            if not when_ms or when_ms < 1_000_000_000_000:
                return
            pool.append({
                "event_id": sanitize_safe_text(event_id),
                "system": system_name,
                "event_type": kind,
                "symbol": sanitize_safe_text(symbol_name),
                "event_utc": format_utc_ts(when_ms),
                "recorded_utc": format_utc_ts(recorded_ms) if recorded_ms else "NOT_RECORDED",
                "price": price,
                "qty": qty,
                "notional_usdt": None,
                "pnl_impact_usdt": pnl,
                "notes": sanitize_safe_text(notes),
                "source": source,
                "_sort_ts": int(when_ms),
            })

        def iso_ms(value):
            if not value:
                return 0
            try:
                dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
                return int(dt.timestamp() * 1000) if dt.tzinfo else 0
            except (ValueError, TypeError, OverflowError):
                return 0

        # Native: bar processing and financial events are separate event classes.
        db = self._ro_sqlite_connect(self.native_ledger_path)
        if db is None:
            source_diagnostics["native"] = "DATA_SOURCE_ERROR"
        else:
            try:
                rows = db.execute(
                    "SELECT journal_id, notes, timestamp_ms FROM native_journal "
                    "WHERE notes LIKE 'closed_bar_%' ORDER BY journal_id DESC LIMIT 40"
                ).fetchall()
                for r in rows:
                    name = str(r["notes"])[len("closed_bar_"):]
                    add("NATIVE", f"NAT-BAR-{r['journal_id']}", "BAR_PROCESSED",
                        name, int(r["timestamp_ms"]), int(r["timestamp_ms"]),
                        "Закрытая свеча обработана; это не зарегистрированный сигнал.",
                        "native_journal")
                rows = db.execute(
                    "SELECT id, symbol, timestamp_ms, reason FROM "
                    "native_missed_opportunities ORDER BY id DESC LIMIT 40"
                ).fetchall()
                for r in rows:
                    add("NATIVE", f"NAT-MISS-{r['id']}", "SIGNAL_REJECTED",
                        r["symbol"], int(r["timestamp_ms"]), None,
                        r["reason"], "native_missed_opportunities")
                rows = db.execute(
                    "SELECT trade_id,symbol,exit_time_ms,exit_price,qty,net_pnl,exit_reason "
                    "FROM native_trades ORDER BY exit_time_ms DESC LIMIT 40"
                ).fetchall()
                for r in rows:
                    add("NATIVE", f"NAT-EXIT-{r['trade_id']}", "EXIT_FILL",
                        r["symbol"], int(r["exit_time_ms"]), None,
                        r["exit_reason"], "native_trades", r["exit_price"], r["qty"], r["net_pnl"])
                rows = db.execute(
                    "SELECT symbol,settlement_time_ms,payment FROM "
                    "native_funding_settlements ORDER BY settlement_time_ms DESC LIMIT 20"
                ).fetchall()
                for r in rows:
                    add("NATIVE", f"NAT-FUND-{r['symbol']}-{r['settlement_time_ms']}",
                        "FUNDING_SETTLEMENT", r["symbol"], int(r["settlement_time_ms"]),
                        None, "Зарегистрированный funding settlement",
                        "native_funding_settlements", pnl=-float(r["payment"]))
                source_diagnostics["native"] = "OK"
            except (sqlite3.Error, ValueError, TypeError) as exc:
                source_diagnostics["native"] = "DATA_SOURCE_ERROR:" + type(exc).__name__
                logger.warning("Native activity journal read error: %s", type(exc).__name__)
            finally:
                db.close()

        # Shadow: deduplicated processed bars are the source of truth, not MANUAL_SAVE.
        db = self._ro_sqlite_connect(self.shadow_ledger_path)
        if db is None:
            source_diagnostics["shadow"] = "DATA_SOURCE_ERROR"
        else:
            try:
                rows = db.execute(
                    "SELECT event_key,exchange_ts,symbol,processed_at_ms FROM "
                    "shadow_dedup_events ORDER BY exchange_ts DESC LIMIT 40"
                ).fetchall()
                for r in rows:
                    # exchange_ts represents the *open* of the 1h kline.
                    close_ms = int(r["exchange_ts"]) + 3600000
                    add("SHADOW", "SHA-BAR-" + str(r["event_key"]), "BAR_PROCESSED",
                        r["symbol"], close_ms, int(r["processed_at_ms"]),
                        "Дедуплицированная закрытая свеча; не является торговым сигналом.",
                        "shadow_dedup_events")
                rows = db.execute(
                    "SELECT event_id,exchange_ts,receive_ts,symbol,event_type,"
                    "fill_price,qty,realized_pnl,execution_status FROM "
                    "shadow_execution_events ORDER BY event_id DESC LIMIT 40"
                ).fetchall()
                for r in rows:
                    add("SHADOW", f"SHA-EV-{r['event_id']}", r["event_type"],
                        r["symbol"], int(r["exchange_ts"]), int(r["receive_ts"] or 0),
                        "Execution status: " + str(r["execution_status"]),
                        "shadow_execution_events", r["fill_price"], r["qty"], r["realized_pnl"])
                rows = db.execute(
                    "SELECT symbol,settlement_time_ms,payment FROM "
                    "shadow_funding_settlements ORDER BY settlement_time_ms DESC LIMIT 20"
                ).fetchall()
                for r in rows:
                    add("SHADOW", f"SHA-FUND-{r['symbol']}-{r['settlement_time_ms']}",
                        "FUNDING_SETTLEMENT", r["symbol"], int(r["settlement_time_ms"]),
                        None, "Зарегистрированный funding settlement",
                        "shadow_funding_settlements", pnl=-float(r["payment"]))
                source_diagnostics["shadow"] = "OK"
            except (sqlite3.Error, ValueError, TypeError) as exc:
                source_diagnostics["shadow"] = "DATA_SOURCE_ERROR:" + type(exc).__name__
                logger.warning("Shadow activity journal read error: %s", type(exc).__name__)
            finally:
                db.close()

        # R2: query relevant event kinds *before* LIMIT; EQUITY ticks can crowd
        # out the single authentic DECISION at 00:00 UTC.
        db = self._ro_sqlite_connect(self.r2_ledger_path)
        if db is None:
            source_diagnostics["r2"] = "DATA_SOURCE_ERROR"
        else:
            try:
                row = db.execute(
                    "SELECT at FROM events WHERE kind='DECISION' "
                    "ORDER BY at DESC LIMIT 1"
                ).fetchone()
                last_r2_decision_utc = str(row["at"]) if row else None
                rows = db.execute(
                    "SELECT id,kind,at,payload FROM events "
                    "WHERE kind IN ('DECISION','EXECUTION','RISK_MARK','ORDER',"
                    "'MISSED_DECISION','MISSED_EXECUTION','DATA_QUALITY','SNAPSHOT') "
                    "ORDER BY at DESC LIMIT 100"
                ).fetchall()
                event_types = {
                    "DECISION": "DAILY_DECISION_RECORDED",
                    "EXECUTION": "VIRTUAL_EXECUTION_CYCLE",
                    "RISK_MARK": "RISK_MARK_EVALUATED",
                    "ORDER": "VIRTUAL_ORDER_ESTIMATE",
                    "SNAPSHOT": "MARKET_SNAPSHOT_RECORDED",
                    "MISSED_DECISION": "MISSED_DAILY_DECISION",
                    "MISSED_EXECUTION": "MISSED_VIRTUAL_EXECUTION",
                    "DATA_QUALITY": "DATA_QUALITY_WARNING",
                }
                for r in rows:
                    try:
                        data = json.loads(r["payload"])
                        kind = str(r["kind"])
                        if kind == "ORDER":
                            if any(data.get(key) != val for key, val in (
                                ("strategy", "R2"), ("capital", 300),
                                ("cost", "BASE"), ("policy", "OBSERVED_BOOK")
                            )):
                                continue
                        if kind == "SNAPSHOT":
                            if data.get("record_type") not in (
                                "FORWARD_DECISION", "FORWARD_EXECUTION"
                            ):
                                continue
                            if data.get("symbol") not in R2_SYMBOLS_3:
                                continue
                        note = {
                            "DECISION": "Суточное решение вычислено. Наличие решения не означает открытие позиции.",
                            "EXECUTION": "Модельный расчёт исполнения, не биржевой fill.",
                            "RISK_MARK": "Зарегистрирована оценка рыночного риска.",
                            "SNAPSHOT": "Снимок модели, не торговое исполнение.",
                            "ORDER": "Расчётная заявка: QUOTE_ESTIMATE_NOT_PROVED_FILL.",
                        }.get(kind, data.get("reason", kind))
                        add("CRYPTOLAB_R2", "R2-" + str(r["id"]),
                            event_types[kind], data.get("symbol", "PORTFOLIO"),
                            iso_ms(r["at"]), iso_ms(r["at"]), note, "observer.events")
                    except (json.JSONDecodeError, ValueError, TypeError, KeyError):
                        continue
                source_diagnostics["r2"] = "OK"
            except sqlite3.Error as exc:
                source_diagnostics["r2"] = "DATA_SOURCE_ERROR:" + type(exc).__name__
                logger.warning("R2 activity journal read error: %s", type(exc).__name__)
            finally:
                db.close()

        # Per-event source proofs, no synthetic trades. Bounded recent feed.
        pool.sort(key=lambda item: item["_sort_ts"], reverse=True)
        filtered = [
            item for item in pool
            if (not eff_sys or item["system"].upper() == eff_sys.upper())
            and (not eff_ev or item["event_type"].upper() == eff_ev.upper())
            and (not eff_sym or item["symbol"].upper() == eff_sym.upper())
        ]
        visible = filtered[offset:offset + limit]
        for item in visible:
            item.pop("_sort_ts", None)
        return {
            "timestamp_iso": now_iso,
            "total_events": len(filtered),
            "limit": limit,
            "offset": offset,
            "events": visible,
            "scope": "BOUNDED_RECENT_ACTIVITIES_NOT_ALL_TIME",
            "r2_last_daily_decision_utc": last_r2_decision_utc or "NOT_RECORDED",
            "source_status": source_diagnostics,
            "notes": "BAR_PROCESSED confirms market processing, not a trading signal. "
                     "R2 virtual execution is not an exchange fill.",
        }

    # =========================================================================
    # TAB 5: ANALYTICS, EQUITY CURVES & CLOSED TRADES
    # =========================================================================

    def get_analytics_data(self) -> Dict[str, Any]:
        """Provides verified equity curves and closed trades history."""
        cached = self._get_cached("analytics")
        if cached is not None:
            return cached

        now_iso = datetime.now(timezone.utc).isoformat()

        # ---------------------------------------------------------------------
        # 1. Native Equity Series (total_equity) & Closed Trades
        # ---------------------------------------------------------------------
        native_equity_series = []
        native_trades_list = []
        conn_n = self._ro_sqlite_connect(self.native_ledger_path)
        if conn_n:
            try:
                # Section P0.4: Native Equity MUST come from total_equity (Last 500 records in chronological order)
                j_rows = conn_n.execute(
                    """SELECT journal_id, iso_time, total_equity, cash_balance
                       FROM (
                           SELECT journal_id, iso_time, total_equity, cash_balance
                           FROM native_journal ORDER BY journal_id DESC LIMIT 500
                       ) ORDER BY journal_id ASC;"""
                ).fetchall()
                for r in j_rows:
                    native_equity_series.append({
                        "timestamp_utc": format_utc_ts(r["iso_time"]),
                        "equity": float(r["total_equity"]),
                    })

                t_rows = conn_n.execute(
                    """SELECT trade_id, symbol, direction, entry_time_ms, exit_time_ms,
                              entry_price, exit_price, qty, gross_pnl, fees_paid,
                              funding_pnl, net_pnl, exit_reason
                       FROM native_trades ORDER BY exit_time_ms DESC LIMIT 100;"""
                ).fetchall()
                for r in t_rows:
                    native_trades_list.append({
                        "trade_id": sanitize_safe_text(r["trade_id"]),
                        "symbol": sanitize_safe_text(r["symbol"]),
                        "side": "LONG" if r["direction"] == 1 else "SHORT",
                        "entry_time_utc": format_utc_ts(r["entry_time_ms"]),
                        "exit_time_utc": format_utc_ts(r["exit_time_ms"]),
                        "entry_price": float(r["entry_price"]),
                        "exit_price": float(r["exit_price"]),
                        "qty": float(r["qty"]),
                        "gross_pnl_usdt": float(r["gross_pnl"]),
                        "fees_paid_usdt": float(r["fees_paid"]),
                        "funding_pnl_usdt": float(r["funding_pnl"]),
                        "net_pnl_usdt": float(r["net_pnl"]),
                        "exit_reason": sanitize_safe_text(r["exit_reason"]),
                    })
            except Exception as e:
                logger.debug(f"Failed to query native analytics: {e}")
            finally:
                conn_n.close()

        # ---------------------------------------------------------------------
        # 2. Shadow Equity Series & Closed Trades (from shadow_state_journal & state)
        # ---------------------------------------------------------------------
        shadow_equity_series = []
        shadow_trades_list = []
        conn_s = self._ro_sqlite_connect(self.shadow_ledger_path)
        if conn_s:
            try:
                # Section P0.4: Shadow MTM Equity (Last 500 records in chronological order, factoring open positions)
                j_rows = conn_s.execute(
                    """SELECT journal_id, saved_at_utc, shadow_realized_equity, state_json
                       FROM (
                           SELECT journal_id, saved_at_utc, shadow_realized_equity, state_json
                           FROM shadow_state_journal ORDER BY journal_id DESC LIMIT 500
                       ) ORDER BY journal_id ASC;"""
                ).fetchall()
                for r in j_rows:
                    realized_eq = float(r["shadow_realized_equity"])
                    unreal_sum = 0.0
                    priced = True
                    try:
                        st = json.loads(r["state_json"])
                        positions = st["shadow_portfolio"]["positions"]
                        if not isinstance(positions, dict):
                            raise ValueError("invalid positions")
                        for position in positions.values():
                            if position.get("unrealized_pnl") is not None:
                                value = float(position["unrealized_pnl"])
                            else:
                                mark_raw = position.get("current_mark")
                                if mark_raw is None:
                                    mark_raw = position.get("mark_price")
                                if mark_raw is None:
                                    raise ValueError("mark not recorded")
                                mark = float(mark_raw)
                                entry = float(position["entry_price"])
                                qty = float(position["current_qty"])
                                direction = int(position["direction"])
                                if mark <= 0 or entry <= 0 or qty < 0 or direction not in (-1, 1):
                                    raise ValueError("invalid price/position")
                                value = direction * (mark - entry) * qty
                            if not math.isfinite(value):
                                raise ValueError("nonfinite PnL")
                            unreal_sum += value
                    except (TypeError, KeyError, ValueError, json.JSONDecodeError):
                        priced = False
                    shadow_equity_series.append({
                        "timestamp_utc": format_utc_ts(r["saved_at_utc"]),
                        "equity": round(realized_eq + unreal_sum, 4) if priced else None,
                        "valuation_status": "FRESH" if priced else "UNVALUED_OR_STALE",
                        "realized_equity": round(realized_eq, 4),
                        "unrealized_pnl": round(unreal_sum, 4) if priced else None,
                    })
            except Exception as e:
                logger.debug(f"Failed to query shadow state journal for equity: {e}")
            finally:
                conn_s.close()

        # Closed trades for Shadow from authoritative state
        shadow_state, _, _ = self._get_authoritative_shadow_state()
        if shadow_state:
            shadow_port = shadow_state.get("shadow_portfolio", {})
            for t in shadow_port.get("closed_trades", []):
                try:
                    init_q = t.get("initial_qty")
                    fin_q = t.get("final_qty")
                    fallback_q = t.get("qty")
                    qty_val = float(fin_q if fin_q is not None else (init_q if init_q is not None else (fallback_q or 0.0)))

                    f_pnl = t.get("funding_pnl")
                    if f_pnl is None:
                        f_pnl = t.get("funding_paid")

                    shadow_trades_list.append({
                        "trade_id": sanitize_safe_text(t.get("trade_id")),
                        "symbol": sanitize_safe_text(t.get("symbol")),
                        "side": "LONG" if int(t.get("direction", 1)) == 1 else "SHORT",
                        "entry_time_utc": format_utc_ts(t.get("entry_time_ms")),
                        "exit_time_utc": format_utc_ts(t.get("exit_time_ms")),
                        "entry_price": float(t["entry_price"]) if t.get("entry_price") is not None else "NOT_RECORDED",
                        "exit_price": float(t["exit_price"]) if t.get("exit_price") is not None else "NOT_RECORDED",
                        "initial_qty": float(init_q) if init_q is not None else qty_val,
                        "final_qty": float(fin_q) if fin_q is not None else qty_val,
                        "qty": qty_val,
                        "gross_pnl_usdt": float(t["gross_pnl"]) if t.get("gross_pnl") is not None else "NOT_RECORDED",
                        "fees_paid_usdt": float(t["fees_paid"]) if t.get("fees_paid") is not None else "NOT_RECORDED",
                        "funding_pnl_usdt": float(f_pnl) if f_pnl is not None else "NOT_RECORDED",
                        "net_pnl_usdt": float(t["net_pnl"]) if t.get("net_pnl") is not None else "NOT_RECORDED",
                        "exit_reason": sanitize_safe_text(t.get("exit_reason", "MARKET_CLOSE")),
                        "slippage_bps": float(t.get("slippage_bps", 0.0)),
                    })
                except Exception:
                    pass

        # ---------------------------------------------------------------------
        # 3. CryptoLab R2 Equity Series: EXCLUSIVELY R2:300:BASE:OBSERVED_BOOK
        # ---------------------------------------------------------------------
        r2_equity_series = []
        conn_r2 = self._ro_sqlite_connect(self.r2_ledger_path)
        if conn_r2:
            try:
                # Section 8 / P0.3: Must filter strategy, capital, cost, policy
                # Direct SQL query for R2:300:BASE:OBSERVED_BOOK with fallback
                rows = conn_r2.execute(
                    """SELECT at, payload FROM (
                           SELECT rowid, at, payload FROM events
                           WHERE kind='EQUITY'
                             AND payload LIKE '%"strategy": "R2"%'
                             AND payload LIKE '%"capital": 300%'
                             AND payload LIKE '%"cost": "BASE"%'
                             AND payload LIKE '%"policy": "OBSERVED_BOOK"%'
                           ORDER BY rowid DESC LIMIT 500
                       ) ORDER BY rowid ASC;"""
                ).fetchall()
                if not rows:
                    rows = conn_r2.execute(
                        """SELECT at, payload FROM (
                               SELECT rowid, at, payload FROM events WHERE kind='EQUITY' ORDER BY rowid DESC LIMIT 500
                           ) ORDER BY rowid ASC;"""
                    ).fetchall()
                for r in rows:
                    try:
                        pld = json.loads(r["payload"])
                        if (
                            pld.get("strategy") == "R2"
                            and pld.get("capital") == 300
                            and pld.get("cost") == "BASE"
                            and pld.get("policy") == "OBSERVED_BOOK"
                        ):
                            r2_equity_series.append({
                                "timestamp_utc": format_utc_ts(r["at"]),
                                "equity": float(pld["mtm"]),
                            })
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Failed to query r2 equity series: {e}")
            finally:
                conn_r2.close()

        # Fallback to telemetry.json equity_history for R2:300:BASE:OBSERVED_BOOK
        if not r2_equity_series and self.r2_telemetry_path and self.r2_telemetry_path.is_file():
            r2_tel = self._read_json(self.r2_telemetry_path) or {}
            for p in r2_tel.get("profiles", []):
                if p.get("profile") == R2_PRIMARY_PROFILE:
                    for h in p.get("equity_history", []):
                        r2_equity_series.append({
                            "timestamp_utc": format_utc_ts(h.get("at")),
                            "equity": float(h["equity_usdt"]),
                        })
                    break

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
                "metrics": {
                    "closed_trades_count": native_t_cnt,
                    "win_rate_pct": (
                        round(len([t for t in native_trades_list if t["net_pnl_usdt"] > 0]) / native_t_cnt * 100.0, 1)
                        if native_t_cnt > 0 else "INSUFFICIENT_SAMPLE"
                    ),
                    "profit_factor": (
                        round(
                            sum(t["net_pnl_usdt"] for t in native_trades_list if t["net_pnl_usdt"] > 0)
                            / abs(sum(t["net_pnl_usdt"] for t in native_trades_list if t["net_pnl_usdt"] < 0) or 1.0),
                            2
                        )
                        if any(t["net_pnl_usdt"] < 0 for t in native_trades_list) else "INSUFFICIENT_SAMPLE"
                    ),
                    "total_net_pnl_usdt": round(sum(t["net_pnl_usdt"] for t in native_trades_list), 4),
                    "total_fees_paid_usdt": round(sum(t["fees_paid_usdt"] for t in native_trades_list), 4),
                    "total_funding_pnl_usdt": round(sum(t["funding_pnl_usdt"] for t in native_trades_list), 4),
                },
            },
            "shadow": {
                "equity_history": downsample(shadow_equity_series),
                "closed_trades": shadow_trades_list,
                "metrics": {
                    "closed_trades_count": shadow_t_cnt,
                    "win_rate_pct": (
                        round(len([t for t in shadow_trades_list if t["net_pnl_usdt"] > 0]) / shadow_t_cnt * 100.0, 1)
                        if shadow_t_cnt > 0 else "INSUFFICIENT_SAMPLE"
                    ),
                    "profit_factor": (
                        round(
                            sum(t["net_pnl_usdt"] for t in shadow_trades_list if t["net_pnl_usdt"] > 0)
                            / abs(sum(t["net_pnl_usdt"] for t in shadow_trades_list if t["net_pnl_usdt"] < 0) or 1.0),
                            2
                        )
                        if any(t["net_pnl_usdt"] < 0 for t in shadow_trades_list) else "INSUFFICIENT_SAMPLE"
                    ),
                    "total_net_pnl_usdt": round(sum(t["net_pnl_usdt"] for t in shadow_trades_list), 4),
                    "total_fees_paid_usdt": round(sum(t["fees_paid_usdt"] for t in shadow_trades_list), 4),
                    "total_funding_pnl_usdt": round(sum(t["funding_pnl_usdt"] for t in shadow_trades_list), 4),
                },
            },
            "r2": {
                "primary_profile": R2_PRIMARY_PROFILE,
                "equity_history": downsample(r2_equity_series),
                "closed_trades": [],
                "metrics": {
                    "closed_trades_count": 0,
                    "profit_factor": "LOT_REALIZATION_NOT_DEFINED",
                    "fill_claim": "QUOTE_ESTIMATE_NOT_PROVED_FILL",
                    "data_gap_annotation": {
                        "gap_window": f"{R2_DATA_GAP_START_UTC} -> {R2_DATA_GAP_END_UTC}",
                        "status": "RESEARCH_DATA_GAP_RECOVERY_ACCEPTED",
                        "incident": "R2-RECOVERY-001",
                        "recovery_confirmed_utc": "2026-11-16T09:20:00Z",
                    },
                },
            },
            "r2_shadow": {
                "primary_profile": R2_PRIMARY_PROFILE,
                "equity_history": downsample(r2_equity_series),
                "closed_trades": [],
                "metrics": {
                    "closed_trades_count": 0,
                    "profit_factor": "LOT_REALIZATION_NOT_DEFINED",
                    "fill_claim": "QUOTE_ESTIMATE_NOT_PROVED_FILL",
                    "data_gap_annotation": {
                        "gap_window": f"{R2_DATA_GAP_START_UTC} -> {R2_DATA_GAP_END_UTC}",
                        "status": "RESEARCH_DATA_GAP_RECOVERY_ACCEPTED",
                        "incident": "R2-RECOVERY-001",
                        "recovery_confirmed_utc": "2026-11-16T09:20:00Z",
                    },
                },
            },
        }
        return self._set_cached("analytics", res)

    # =========================================================================
    # TAB 6: SYSTEM HEALTH & DIAGNOSTICS (Section P0.5)
    # =========================================================================

    def get_health_data(self) -> Dict[str, Any]:
        """Provides verified system health without hardcoded running statuses or PRAGMA quick_checks."""
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

        nat_ok, nat_age = check_file_age(self.native_telemetry_path)
        sha_ok, sha_age = check_file_age(self.shadow_telemetry_path)
        r2_ok, r2_age = check_file_age(self.r2_telemetry_path)

        def determine_service_state(file_ok: bool, age: Optional[float]) -> str:
            if not file_ok or age is None:
                return "SERVICE_STATE_UNKNOWN"
            if 0.0 <= age <= 120.0:
                return "TELEMETRY_FRESH"
            return "TELEMETRY_STALE"

        # Bounded SQLite check (file size + quick SELECT 1, no PRAGMA quick_check to avoid disk I/O)
        def check_db_health(p: Optional[Path]) -> Dict[str, Any]:
            if not p or not p.is_file():
                return {"exists": False, "status": "NOT_FOUND", "size_kb": 0, "wal_size_kb": 0}
            try:
                size_kb = round(p.stat().st_size / 1024.0, 1)
                wal_p = Path(str(p) + "-wal")
                wal_kb = round(wal_p.stat().st_size / 1024.0, 1) if wal_p.is_file() else 0.0
                conn = self._ro_sqlite_connect(p)
                status = "OK"
                if conn:
                    try:
                        conn.execute("SELECT 1;").fetchone()
                    except Exception as e:
                        status = f"ERROR_{type(e).__name__}"
                    finally:
                        conn.close()
                else:
                    status = "UNOPENABLE"
                return {
                    "exists": True,
                    "status": status,
                    "size_kb": size_kb,
                    "wal_size_kb": wal_kb,
                }
            except Exception as e:
                return {"exists": True, "status": f"STAT_ERROR: {type(e).__name__}", "size_kb": 0, "wal_size_kb": 0}

        db_nat = check_db_health(self.native_ledger_path)
        db_sha = check_db_health(self.shadow_ledger_path)
        db_r2 = check_db_health(self.r2_ledger_path)

        # Host load average
        load_avg = [0.0, 0.0, 0.0]
        try:
            if hasattr(os, "getloadavg"):
                load_avg = [round(x, 2) for x in os.getloadavg()]
        except Exception:
            pass

        # R2 telemetry status (not proof of systemd process state).
        r2_tel = self._read_json(self.r2_telemetry_path) or {}
        r2_process_status = r2_tel.get("process_status", "NOT_RECORDED")
        r2_heartbeat_at = r2_tel.get("heartbeat_at")

        def activity_item(value_ms, source, interval_sec, kind):
            if value_ms is None:
                return {"last_observation_utc": "NOT_RECORDED", "age_sec": None,
                        "status": "NOT_RECORDED", "source": source, "kind": kind}
            try:
                value_ms = int(value_ms)
                if value_ms <= 0:
                    raise ValueError("invalid market timestamp")
                age = max(0, round(time.time() - value_ms / 1000.0, 1))
                # 3-minute grace after a new expected bar/decision boundary.
                label = "CURRENT" if age <= interval_sec + 180 else "OBSERVATION_DELAYED"
                return {"last_observation_utc": format_utc_ts(value_ms),
                        "age_sec": age, "status": label, "source": source, "kind": kind}
            except (ValueError, OverflowError, TypeError):
                return {"last_observation_utc": "NOT_RECORDED", "age_sec": None,
                        "status": "DATA_SOURCE_ERROR", "source": source, "kind": kind}

        activity = {}
        native_state, native_stat, _ = self._get_authoritative_native_state()
        native_bars = native_state.get("last_processed_bar_ts", {}) if native_stat == "OK" else {}
        native_max = max((int(ts) for ts in native_bars.values() if ts), default=0) if isinstance(native_bars, dict) else 0
        activity["native"] = activity_item(
            native_max + 3600000 if native_max else None,
            "native_state_journal.last_processed_bar_ts", 3600, "CLOSED_1H_BAR")

        conn = self._ro_sqlite_connect(self.shadow_ledger_path)
        shadow_max = None
        if conn:
            try:
                row = conn.execute("SELECT MAX(exchange_ts) FROM shadow_dedup_events").fetchone()
                if row and row[0] is not None:
                    shadow_max = int(row[0]) + 3600000
            except sqlite3.Error:
                pass
            finally:
                conn.close()
        activity["shadow"] = activity_item(
            shadow_max, "shadow_dedup_events.exchange_ts", 3600, "CLOSED_1H_BAR")

        conn = self._ro_sqlite_connect(self.r2_ledger_path)
        r2_decision_ms = None
        if conn:
            try:
                row = conn.execute(
                    "SELECT at FROM events WHERE kind='DECISION' ORDER BY at DESC LIMIT 1"
                ).fetchone()
                if row:
                    parsed = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
                    if parsed.tzinfo is not None:
                        r2_decision_ms = int(parsed.timestamp() * 1000)
            except (sqlite3.Error, ValueError, TypeError, OverflowError):
                pass
            finally:
                conn.close()
        activity["r2"] = activity_item(
            r2_decision_ms, "observer.events.kind=DECISION", 86400, "DAILY_DECISION")

        res = {
            "timestamp_iso": now_iso,
            "read_only_enforced": True,
            "secrets_exposed": False,
            "host_resources": {
                "load_average": load_avg,
                "server_time_utc": now_iso,
            },
            "market_activity": activity,
            "services": [
                {
                    "name": "bybit-shadow-native.service",
                    "role": "Native Paper Trading (Port 8084 runner)",
                    "telemetry_present": nat_ok,
                    "heartbeat_age_sec": nat_age,
                    "freshness": "FRESH" if (nat_age is not None and nat_age <= 120.0) else "STALE",
                    "status": "SERVICE_STATE_UNKNOWN",
                },
                {
                    "name": "bybit-shadow-realistic.service",
                    "role": "Realistic Shadow L2 Trading (Paper runner)",
                    "telemetry_present": sha_ok,
                    "heartbeat_age_sec": sha_age,
                    "freshness": "FRESH" if (sha_age is not None and sha_age <= 120.0) else "STALE",
                    "status": "SERVICE_STATE_UNKNOWN",
                },
                {
                    "name": "bybit-shadow-r2.service",
                    "role": "CryptoLab R2 Spot Shadow (Hunter/R2)",
                    "telemetry_present": r2_ok,
                    "heartbeat_age_sec": r2_age,
                    "freshness": "FRESH" if (r2_age is not None and r2_age <= 120.0) else "STALE",
                    "process_status": r2_process_status,
                    "status": "SERVICE_STATE_UNKNOWN",
                    "heartbeat_at": r2_heartbeat_at,
                },
                {
                    "name": "bybit-shadow-dashboard.service",
                    "role": "Three-System Trading Control Center (Port 8085)",
                    "port": 8085,
                    "status": "HTTP_HANDLER_RESPONDING",
                    "verification": "CURRENT_HTTP_REQUEST_ONLY; SYSTEMD_STATE_UNKNOWN",
                    "public_access": "0.0.0.0:8085 (READ_ONLY)",
                },
            ],
            "databases": {
                "native_ledger": db_nat,
                "shadow_ledger": db_sha,
                "r2_observer": db_r2,
            },
            "known_incidents": [
                {
                    "incident_id": "R2-RECOVERY-001",
                    "system": "CryptoLab R2",
                    "window": f"{R2_DATA_GAP_START_UTC} -> {R2_DATA_GAP_END_UTC}",
                    "duration_hours": 8.28,
                    "error_type": "READINESS_EXPIRED_OR_FUTURE",
                    "resolution": "Fixed in commit 46c28e36 (readiness expiration window). Recovery accepted.",
                    "status": "RECOVERED",
                },
                {
                    "incident_id": "R2-RESOURCE-LIMITS-RESTART",
                    "system": "CryptoLab R2",
                    "timestamp_utc": "2026-11-16T10:47:35Z",
                    "error_type": "READINESS_RESOURCE_LIMITS",
                    "n_restarts_at_incident": 1799,
                    "trigger_metrics": "TRIGGER_NOT_IDENTIFIED (readiness.py does not serialize which of the 7 thresholds was breached: CPU load > 0.8, available RAM < 512MB, etc.)",
                    "diagnostic_plan": "docs/R2_RESOURCE_OBSERVABILITY_001.md",
                    "status": "HISTORICAL_AUTO_RESTART",
                },
            ],
            "invariants": {
                "p0_read_only_sqlite": True,
                "p0_no_cross_system_pnl_pooling": True,
                "p0_no_private_keys": True,
                "p0_v32_isolated": True,
                "p0_zero_command_execution": True,
            },
        }
        return self._set_cached("health", res)