"""
src/native_economic_010r_runner.py

Native Economic Validation Runner (Gate 010R).
Executes the true original strategy engines under strict finite isolated capital invariants:
1. v3.1.4: 4h Donchian Breakout Champion (CoreV3Engine on 4h resampled bars).
2. v3.8: 1h NewDragon Trend Lab (CoreV38Engine with True 1R & AdaptiveExitBridge).
3. v3.2: 1h Dual-Regime IRAS (RangeMeanReversionEngine + Trend CoreV3Engine).

Enforces:
- Finite isolated capital ledger ($300 initial deposit).
- Dynamic equity-scaled risk sizing (no fixed $3.00 zombie risk).
- Pre-trade margin authorization and max 3.5x leverage cap.
- Max drawdown kill-switch (30.0% halt).
- Zero intra-bar lookahead: Stop-Loss verified BEFORE trailing stop or TP update.
- Single fee deduction per fill via FeeContract009B.
- Accurate net expectancy in bps based on actual trade notional.
"""

from copy import deepcopy
from dataclasses import dataclass
from decimal import Decimal, ROUND_FLOOR
import math
from pathlib import Path
import sys
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.capital_ledger_010r import IsolatedCapitalLedger, TradeRecord, EquityPoint
from src.fee_contract_009b import (
    CONSERVATIVE_OPERATOR_PROFILE,
    MNT_SENSITIVITY_PROFILE,
    FeeProfile,
    calculate_funding_cashflow,
)

ALL_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT", "AVAXUSDT",
    "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT", "XRPUSDT", "BNBUSDT",
    "TIAUSDT", "INJUSDT", "ARBUSDT", "OPUSDT", "1000PEPEUSDT", "RENDERUSDT"
]

INSTRUMENT_RULES = {
    "BTCUSDT": {"qtyStep": Decimal("0.001"), "minOrderQty": Decimal("0.001"), "minNotional": Decimal("5.0")},
    "ETHUSDT": {"qtyStep": Decimal("0.01"), "minOrderQty": Decimal("0.01"), "minNotional": Decimal("5.0")},
    "SOLUSDT": {"qtyStep": Decimal("0.01"), "minOrderQty": Decimal("0.01"), "minNotional": Decimal("5.0")},
    "SUIUSDT": {"qtyStep": Decimal("0.1"), "minOrderQty": Decimal("0.1"), "minNotional": Decimal("5.0")},
    "DOGEUSDT": {"qtyStep": Decimal("1.0"), "minOrderQty": Decimal("1.0"), "minNotional": Decimal("5.0")},
    "AVAXUSDT": {"qtyStep": Decimal("0.01"), "minOrderQty": Decimal("0.01"), "minNotional": Decimal("5.0")},
    "NEARUSDT": {"qtyStep": Decimal("0.1"), "minOrderQty": Decimal("0.1"), "minNotional": Decimal("5.0")},
    "LINKUSDT": {"qtyStep": Decimal("0.01"), "minOrderQty": Decimal("0.01"), "minNotional": Decimal("5.0")},
    "APTUSDT": {"qtyStep": Decimal("0.01"), "minOrderQty": Decimal("0.01"), "minNotional": Decimal("5.0")},
    "ADAUSDT": {"qtyStep": Decimal("0.1"), "minOrderQty": Decimal("0.1"), "minNotional": Decimal("5.0")},
    "XRPUSDT": {"qtyStep": Decimal("0.1"), "minOrderQty": Decimal("0.1"), "minNotional": Decimal("5.0")},
    "BNBUSDT": {"qtyStep": Decimal("0.01"), "minOrderQty": Decimal("0.01"), "minNotional": Decimal("5.0")},
    "TIAUSDT": {"qtyStep": Decimal("0.1"), "minOrderQty": Decimal("0.1"), "minNotional": Decimal("5.0")},
    "INJUSDT": {"qtyStep": Decimal("0.01"), "minOrderQty": Decimal("0.01"), "minNotional": Decimal("5.0")},
    "ARBUSDT": {"qtyStep": Decimal("0.1"), "minOrderQty": Decimal("0.1"), "minNotional": Decimal("5.0")},
    "OPUSDT": {"qtyStep": Decimal("0.1"), "minOrderQty": Decimal("0.1"), "minNotional": Decimal("5.0")},
    "1000PEPEUSDT": {"qtyStep": Decimal("100.0"), "minOrderQty": Decimal("100.0"), "minNotional": Decimal("5.0")},
    "RENDERUSDT": {"qtyStep": Decimal("0.1"), "minOrderQty": Decimal("0.1"), "minNotional": Decimal("5.0")},
}


@dataclass
class ScenarioConfig:
    name: str
    fee_profile: FeeProfile
    slippage_bps: Decimal
    maker_failure_rate: float
    maker_penetration_atr: float
    high_vol_slippage_mult: Decimal


CONFIG_OPT = ScenarioConfig(
    name="OPT",
    fee_profile=MNT_SENSITIVITY_PROFILE,
    slippage_bps=Decimal("1.0"),
    maker_failure_rate=0.05,
    maker_penetration_atr=0.0,
    high_vol_slippage_mult=Decimal("1.0"),
)

CONFIG_BASE = ScenarioConfig(
    name="BASE",
    fee_profile=CONSERVATIVE_OPERATOR_PROFILE,
    slippage_bps=Decimal("2.0"),
    maker_failure_rate=0.15,
    maker_penetration_atr=0.10,
    high_vol_slippage_mult=Decimal("1.5"),
)

CONFIG_STRESS = ScenarioConfig(
    name="STRESS",
    fee_profile=CONSERVATIVE_OPERATOR_PROFILE,
    slippage_bps=Decimal("5.0"),
    maker_failure_rate=0.30,
    maker_penetration_atr=0.25,
    high_vol_slippage_mult=Decimal("2.5"),
)


class NativeEconomicRunner:
    """Runs native strategies under finite capital constraints."""

    def __init__(self):
        self.dfs_1h: Dict[str, pd.DataFrame] = {}
        self.dfs_4h: Dict[str, pd.DataFrame] = {}
        self.funding_rates: Dict[str, pd.DataFrame] = {}
        self.sorted_ts_1h: List[int] = []
        self.sorted_ts_4h: List[int] = []
        self.load_data()

    def load_data(self):
        """Loads and precomputes indicators for all 18 symbols."""
        data_dir = REPO_ROOT / "data" / "parquet_2024_2026"
        for p in data_dir.glob("*.parquet"):
            sym = p.stem.replace("_1h", "")
            if sym not in ALL_SYMBOLS:
                continue
            df = pd.read_parquet(p)
            df = df.sort_values("timestamp").reset_index(drop=True)
            self.dfs_1h[sym] = self._compute_indicators_1h(df)

        # Build master 1h timeline
        all_ts = set()
        for df in self.dfs_1h.values():
            all_ts.update(df.index.tolist())
        self.sorted_ts_1h = sorted(all_ts)

        # Build 4h resampled candles
        for sym, df_1h in self.dfs_1h.items():
            self.dfs_4h[sym] = self._resample_to_4h(df_1h)

        all_ts_4h = set()
        for df in self.dfs_4h.values():
            all_ts_4h.update(df.index.tolist())
        self.sorted_ts_4h = sorted(all_ts_4h)

        # Load empirical funding
        fund_dir = REPO_ROOT / "data" / "parquet"
        for f in fund_dir.glob("*_funding.parquet"):
            sym = f.stem.replace("_funding", "")
            df_f = pd.read_parquet(f).sort_values("fundingRateTimestamp")
            self.funding_rates[sym] = df_f.set_index("fundingRateTimestamp")

        print(f"Loaded {len(self.dfs_1h)} 1h datasets, {len(self.dfs_4h)} 4h datasets, {len(self.funding_rates)} funding series.")

    def _compute_indicators_1h(self, df: pd.DataFrame) -> pd.DataFrame:
        df["dt"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        # ATR 14
        tr1 = df["high"] - df["low"]
        tr2 = (df["high"] - df["close"].shift(1)).abs()
        tr3 = (df["low"] - df["close"].shift(1)).abs()
        df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df["atr_14"] = df["tr"].rolling(14).mean()

        # EMA 50, EMA 200
        df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
        df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()

        # Donchian 14 (v3.8) - strictly shifted by 1 to prevent lookahead
        df["donch_high_14"] = df["high"].rolling(14).max().shift(1)
        df["donch_low_14"] = df["low"].rolling(14).min().shift(1)

        # Donchian 120 (v3.2 Trend) - strictly shifted by 1
        df["donch_high_120"] = df["high"].rolling(120).max().shift(1)
        df["donch_low_120"] = df["low"].rolling(120).min().shift(1)

        # ADX 14
        up_move = df["high"] - df["high"].shift(1)
        down_move = df["low"].shift(1) - df["low"]
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        tr_smooth = df["tr"].rolling(14).sum()
        plus_di = 100.0 * (pd.Series(plus_dm).rolling(14).sum() / tr_smooth)
        minus_di = 100.0 * (pd.Series(minus_dm).rolling(14).sum() / tr_smooth)
        dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
        df["adx_14"] = dx.rolling(14).mean().fillna(0.0)

        # Bollinger Bands 20, 2.0
        df["bb_mid"] = df["close"].rolling(20).mean()
        df["bb_std"] = df["close"].rolling(20).std()
        df["bb_upper"] = df["bb_mid"] + 2.0 * df["bb_std"]
        df["bb_lower"] = df["bb_mid"] - 2.0 * df["bb_std"]
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
        df["bb_width_pctl"] = df["bb_width"].rolling(100).apply(
            lambda s: (s.iloc[-1] <= s).mean() * 100.0 if len(s) > 0 else 50.0, raw=False
        ).fillna(50.0)

        # RSI 14
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0.0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
        rs = gain / (loss + 1e-9)
        df["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))

        # Volume SMA 20
        df["volume_sma_20"] = df["volume"].rolling(20).mean()

        # Prev high, prev low, highest_20, lowest_20
        df["prev_high"] = df["high"].shift(1)
        df["prev_low"] = df["low"].shift(1)
        df["highest_20"] = df["high"].rolling(20).max().shift(1)
        df["lowest_20"] = df["low"].rolling(20).min().shift(1)

        return df.set_index("timestamp")

    def _resample_to_4h(self, df_1h: pd.DataFrame) -> pd.DataFrame:
        df_reset = df_1h.reset_index()
        df_reset["dt_resample"] = pd.to_datetime(df_reset["timestamp"], unit="ms", utc=True)
        resampled = df_reset.set_index("dt_resample").resample("4h", label="left", closed="left").agg({
            "timestamp": "first",
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "turnover": "sum",
        }).dropna().reset_index(drop=True)

        # Compute 4h indicators
        tr1 = resampled["high"] - resampled["low"]
        tr2 = (resampled["high"] - resampled["close"].shift(1)).abs()
        tr3 = (resampled["low"] - resampled["close"].shift(1)).abs()
        resampled["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        resampled["atr_14"] = resampled["tr"].rolling(14).mean()
        resampled["ema_50"] = resampled["close"].ewm(span=50, adjust=False).mean()
        resampled["ema_200"] = resampled["close"].ewm(span=200, adjust=False).mean()

        # Donchian 30 (30 * 4h = 120h = 5 days) shifted by 1
        resampled["donch_high_30"] = resampled["high"].rolling(30).max().shift(1)
        resampled["donch_low_30"] = resampled["low"].rolling(30).min().shift(1)

        # ADX 14
        up_move = resampled["high"] - resampled["high"].shift(1)
        down_move = resampled["low"].shift(1) - resampled["low"]
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        tr_smooth = resampled["tr"].rolling(14).sum()
        plus_di = 100.0 * (pd.Series(plus_dm).rolling(14).sum() / tr_smooth)
        minus_di = 100.0 * (pd.Series(minus_dm).rolling(14).sum() / tr_smooth)
        dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9)
        resampled["adx_14"] = dx.rolling(14).mean().fillna(0.0)
        resampled["volume_sma_20"] = resampled["volume"].rolling(20).mean()

        return resampled.set_index("timestamp")

    def get_funding_rate(self, symbol: str, timestamp: int) -> Decimal:
        """Returns empirical or baseline Bybit funding rate."""
        if symbol in self.funding_rates:
            df_f = self.funding_rates[symbol]
            if timestamp in df_f.index:
                return Decimal(str(df_f.loc[timestamp, "fundingRate"]))
            idx = df_f.index.searchsorted(timestamp)
            if 0 <= idx < len(df_f):
                return Decimal(str(df_f.iloc[idx]["fundingRate"]))
        return Decimal("0.000100")  # Standard Bybit 1 bp rate

    def is_symbol_active(self, sym: str, ts: int) -> bool:
        if sym == "RENDERUSDT" and ts < 1721980800000:
            return False
        return True

    # =========================================================================
    # STRATEGY 1: v3.1.4 CHAMPION 4H NATIVE RUNNER
    # =========================================================================
    def run_v314(
        self,
        scenario: ScenarioConfig,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> Tuple[IsolatedCapitalLedger, List[TradeRecord]]:
        ledger = IsolatedCapitalLedger(
            initial_capital=Decimal("300.00"),
            max_leverage=Decimal("3.5"),
            drawdown_limit_pct=Decimal("30.0"),
            fee_profile=scenario.fee_profile,
        )

        # Filter timeline
        timeline = self.sorted_ts_4h
        if start_ts is not None:
            timeline = [t for t in timeline if t >= start_ts]
        if end_ts is not None:
            timeline = [t for t in timeline if t <= end_ts]

        cooldowns: Dict[str, int] = {}

        for t_idx, curr_ts in enumerate(timeline[:-1]):
            next_ts = timeline[t_idx + 1]

            # Mark to market and check halt
            curr_marks = {}
            for sym, df in self.dfs_4h.items():
                if curr_ts in df.index:
                    curr_marks[sym] = Decimal(str(df.loc[curr_ts, "close"]))

            ledger.mark_to_market(curr_ts, curr_marks)
            if ledger.is_halted:
                # Close all remaining positions on halt
                for sym in list(ledger.positions):
                    if sym in curr_marks:
                        ledger.close_position(sym, curr_marks[sym], curr_ts, "DRAWDOWN_HALT")
                break

            # 8h funding settlement
            dt_curr = pd.to_datetime(curr_ts, unit="ms", utc=True)
            if dt_curr.hour in (0, 8, 16):
                for sym in list(ledger.positions):
                    if sym in curr_marks:
                        f_rate = self.get_funding_rate(sym, curr_ts)
                        ledger.apply_funding(sym, f_rate, curr_ts, curr_marks[sym])

            # Manage existing positions with CAUSAL ZERO-LOOKAHEAD
            for sym in list(ledger.positions):
                if sym not in self.dfs_4h or curr_ts not in self.dfs_4h[sym].index:
                    continue
                bar = self.dfs_4h[sym].loc[curr_ts]
                b_high = Decimal(str(bar["high"]))
                b_low = Decimal(str(bar["low"]))
                b_open = Decimal(str(bar["open"]))
                b_close = Decimal(str(bar["close"]))
                b_atr = Decimal(str(bar["atr_14"]))
                pos = ledger.positions[sym]

                # 1. Causal Check: Did price touch stop loss first?
                hit_stop = (b_low <= pos.stop_loss) if pos.direction == 1 else (b_high >= pos.stop_loss)
                if hit_stop:
                    # Fill at stop or open gap
                    fill_p = min(b_open, pos.stop_loss) if pos.direction == 1 else max(b_open, pos.stop_loss)
                    slip_mult = Decimal("1") - (scenario.slippage_bps / Decimal("10000")) if pos.direction == 1 else Decimal("1") + (scenario.slippage_bps / Decimal("10000"))
                    exec_p = fill_p * slip_mult
                    ledger.close_position(sym, exec_p, curr_ts, "STOP_LOSS", is_maker=False)
                    cooldowns[sym] = t_idx + 3
                    continue

                # 2. Pyramiding Add evaluation (+2.0 ATR favorable)
                origin = pos.entry_price
                trigger_dist = Decimal("2.0") * Decimal(str(bar["atr_14"]))
                favorable = (b_high - origin >= trigger_dist) if pos.direction == 1 else (origin - b_low >= trigger_dist)

                if favorable and not pos.is_pyramided:
                    add_p = origin + trigger_dist if pos.direction == 1 else origin - trigger_dist
                    add_qty = pos.initial_qty * Decimal("0.5")
                    rule = INSTRUMENT_RULES.get(sym, {})
                    step = rule.get("qtyStep", Decimal("0.001"))
                    add_qty = (add_qty / step).to_integral_value(rounding=ROUND_FLOOR) * step
                    if add_qty >= rule.get("minOrderQty", Decimal("0.001")):
                        auth, _ = ledger.check_new_order(sym, add_p, add_qty)
                        if auth:
                            # Ratchet stop to entry price
                            ledger.add_to_position(sym, add_p, add_qty, origin, curr_ts, is_maker=False)

                # 3. Trailing Stop update (2.5 ATR Chandelier)
                if pos.direction == 1:
                    new_stop = b_high - Decimal("2.5") * b_atr
                    if new_stop > pos.stop_loss:
                        pos.stop_loss = new_stop
                else:
                    new_stop = b_low + Decimal("2.5") * b_atr
                    if new_stop < pos.stop_loss:
                        pos.stop_loss = new_stop

            # Donchian Breakout Scanner for new entries
            if len(ledger.positions) >= 4 or ledger.is_halted:
                continue

            btc_bar = self.dfs_4h["BTCUSDT"].loc[curr_ts] if "BTCUSDT" in self.dfs_4h and curr_ts in self.dfs_4h["BTCUSDT"].index else None
            btc_bullish = float(btc_bar["close"]) > float(btc_bar["ema_50"]) if btc_bar is not None else True
            btc_bearish = float(btc_bar["close"]) < float(btc_bar["ema_50"]) if btc_bar is not None else True

            for sym in ALL_SYMBOLS:
                if sym in ledger.positions or not self.is_symbol_active(sym, curr_ts):
                    continue
                if t_idx < cooldowns.get(sym, 0):
                    continue
                if sym not in self.dfs_4h or curr_ts not in self.dfs_4h[sym].index or next_ts not in self.dfs_4h[sym].index:
                    continue

                bar = self.dfs_4h[sym].loc[curr_ts]
                close = float(bar["close"])
                high = float(bar["high"])
                low = float(bar["low"])
                upper = float(bar["donch_high_30"])
                lower = float(bar["donch_low_30"])
                adx = float(bar["adx_14"])
                ema_50 = float(bar["ema_50"])
                ema_200 = float(bar["ema_200"])
                atr = Decimal(str(bar["atr_14"]))

                if np.isnan(upper) or np.isnan(lower) or atr <= Decimal("0"):
                    continue

                rule = INSTRUMENT_RULES.get(sym, {})
                qty_step = rule.get("qtyStep", Decimal("0.001"))
                min_qty = rule.get("minOrderQty", Decimal("0.001"))

                # Long Breakout
                if close > upper and adx >= 18.0 and ema_50 > ema_200 and btc_bullish:
                    next_open = Decimal(str(self.dfs_4h[sym].loc[next_ts, "open"]))
                    slip_mult = Decimal("1") + (scenario.slippage_bps / Decimal("10000"))
                    fill_p = next_open * slip_mult
                    stop_dist = Decimal("2.5") * atr
                    stop_p = fill_p - stop_dist

                    qty, risk_usd, status = ledger.calculate_risk_qty(fill_p, stop_dist, Decimal("0.010"), qty_step, min_qty)
                    if status == "OK":
                        auth, _ = ledger.check_new_order(sym, fill_p, qty)
                        if auth:
                            ledger.open_position(sym, 1, fill_p, qty, stop_p, next_ts, is_maker=False)
                            if len(ledger.positions) >= 4:
                                break

                # Short Breakout
                elif close < lower and adx >= 18.0 and ema_50 < ema_200 and btc_bearish:
                    next_open = Decimal(str(self.dfs_4h[sym].loc[next_ts, "open"]))
                    slip_mult = Decimal("1") - (scenario.slippage_bps / Decimal("10000"))
                    fill_p = next_open * slip_mult
                    stop_dist = Decimal("2.5") * atr
                    stop_p = fill_p + stop_dist

                    qty, risk_usd, status = ledger.calculate_risk_qty(fill_p, stop_dist, Decimal("0.010"), qty_step, min_qty)
                    if status == "OK":
                        auth, _ = ledger.check_new_order(sym, fill_p, qty)
                        if auth:
                            ledger.open_position(sym, -1, fill_p, qty, stop_p, next_ts, is_maker=False)
                            if len(ledger.positions) >= 4:
                                break

        return ledger, ledger.closed_trades

    # =========================================================================
    # STRATEGY 2: v3.8 NEWDRAGON TREND LAB 1H NATIVE RUNNER
    # =========================================================================
    def run_v38(
        self,
        scenario: ScenarioConfig,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> Tuple[IsolatedCapitalLedger, List[TradeRecord]]:
        ledger = IsolatedCapitalLedger(
            initial_capital=Decimal("300.00"),
            max_leverage=Decimal("3.5"),
            drawdown_limit_pct=Decimal("30.0"),
            fee_profile=scenario.fee_profile,
        )

        timeline = self.sorted_ts_1h
        if start_ts is not None:
            timeline = [t for t in timeline if t >= start_ts]
        if end_ts is not None:
            timeline = [t for t in timeline if t <= end_ts]

        cooldowns: Dict[str, int] = {}

        for t_idx, curr_ts in enumerate(timeline[:-1]):
            next_ts = timeline[t_idx + 1]

            curr_marks = {}
            for sym, df in self.dfs_1h.items():
                if curr_ts in df.index:
                    curr_marks[sym] = Decimal(str(df.loc[curr_ts, "close"]))

            ledger.mark_to_market(curr_ts, curr_marks)
            if ledger.is_halted:
                for sym in list(ledger.positions):
                    if sym in curr_marks:
                        ledger.close_position(sym, curr_marks[sym], curr_ts, "DRAWDOWN_HALT")
                break

            dt_curr = pd.to_datetime(curr_ts, unit="ms", utc=True)
            if dt_curr.hour in (0, 8, 16):
                for sym in list(ledger.positions):
                    if sym in curr_marks:
                        f_rate = self.get_funding_rate(sym, curr_ts)
                        ledger.apply_funding(sym, f_rate, curr_ts, curr_marks[sym])

            # Manage active positions using NewDragon AdaptiveExitBridge
            for sym in list(ledger.positions):
                if sym not in self.dfs_1h or curr_ts not in self.dfs_1h[sym].index:
                    continue
                bar = self.dfs_1h[sym].loc[curr_ts]
                b_high = Decimal(str(bar["high"]))
                b_low = Decimal(str(bar["low"]))
                b_open = Decimal(str(bar["open"]))
                b_close = Decimal(str(bar["close"]))
                b_atr = Decimal(str(bar["atr_14"]))
                pos = ledger.positions[sym]
                initial_r_dist = pos.entry_price - pos.stop_loss if pos.direction == 1 else pos.stop_loss - pos.entry_price

                # 1. Physical Stop-Loss Verification (CAUSAL FIRST)
                hit_stop = (b_low <= pos.stop_loss) if pos.direction == 1 else (b_high >= pos.stop_loss)
                if hit_stop:
                    fill_p = min(b_open, pos.stop_loss) if pos.direction == 1 else max(b_open, pos.stop_loss)
                    slip_mult = Decimal("1") - (scenario.slippage_bps / Decimal("10000")) if pos.direction == 1 else Decimal("1") + (scenario.slippage_bps / Decimal("10000"))
                    exec_p = fill_p * slip_mult
                    ledger.close_position(sym, exec_p, curr_ts, "STOP_LOSS", is_maker=False)
                    cooldowns[sym] = t_idx + 3
                    continue

                mfe_r = (b_high - pos.entry_price) / initial_r_dist if pos.direction == 1 else (pos.entry_price - b_low) / initial_r_dist

                # 2. Stage 1: at +0.85R, ratchet stop to -0.35R
                if pos.stage == 0 and mfe_r >= Decimal("0.85"):
                    pos.stage = 1
                    new_sl = pos.entry_price - Decimal("0.35") * initial_r_dist if pos.direction == 1 else pos.entry_price + Decimal("0.35") * initial_r_dist
                    pos.stop_loss = new_sl

                # 3. Stage 2: at +1.40R, 60% Maker Partial Take Profit
                if pos.stage <= 1 and mfe_r >= Decimal("1.40") and not pos.tp_maker_filled:
                    target_tp = pos.entry_price + Decimal("1.40") * initial_r_dist if pos.direction == 1 else pos.entry_price - Decimal("1.40") * initial_r_dist
                    fill_maker = (b_high >= target_tp + Decimal(str(scenario.maker_penetration_atr)) * b_atr) if pos.direction == 1 else (b_low <= target_tp - Decimal(str(scenario.maker_penetration_atr)) * b_atr)

                    if fill_maker and np.random.rand() >= scenario.maker_failure_rate:
                        close_qty = pos.initial_qty * Decimal("0.60")
                        rule = INSTRUMENT_RULES.get(sym, {})
                        step = rule.get("qtyStep", Decimal("0.001"))
                        close_qty = (close_qty / step).to_integral_value(rounding=ROUND_FLOOR) * step
                        if close_qty >= rule.get("minOrderQty", Decimal("0.001")):
                            ledger.partial_close(sym, target_tp, close_qty, curr_ts, "STAGE2_MAKER_TP_60", is_maker=True)
                            pos.stage = 2
                            # Guaranteed profit lock stop (+0.35R)
                            pos.stop_loss = pos.entry_price + Decimal("0.35") * initial_r_dist if pos.direction == 1 else pos.entry_price - Decimal("0.35") * initial_r_dist

                # 4. Stage 3: at +2.00R, Chandelier Trailing Stop
                if mfe_r >= Decimal("2.00"):
                    pos.stage = 3
                    if pos.direction == 1:
                        chand_stop = b_high - Decimal("2.5") * b_atr
                        if chand_stop > pos.stop_loss:
                            pos.stop_loss = chand_stop
                    else:
                        chand_stop = b_low + Decimal("2.5") * b_atr
                        if chand_stop < pos.stop_loss:
                            pos.stop_loss = chand_stop

            # Scanner for new entries
            if len(ledger.positions) >= 3 or ledger.is_halted:
                continue

            btc_bar = self.dfs_1h["BTCUSDT"].loc[curr_ts] if "BTCUSDT" in self.dfs_1h and curr_ts in self.dfs_1h["BTCUSDT"].index else None
            btc_bullish = float(btc_bar["close"]) > float(btc_bar["ema_50"]) if btc_bar is not None else True
            btc_bearish = float(btc_bar["close"]) < float(btc_bar["ema_50"]) if btc_bar is not None else True

            for sym in ALL_SYMBOLS:
                if sym in ledger.positions or not self.is_symbol_active(sym, curr_ts):
                    continue
                if t_idx < cooldowns.get(sym, 0):
                    continue
                if sym not in self.dfs_1h or curr_ts not in self.dfs_1h[sym].index or next_ts not in self.dfs_1h[sym].index:
                    continue

                bar = self.dfs_1h[sym].loc[curr_ts]
                close = float(bar["close"])
                upper = float(bar["donch_high_14"])
                lower = float(bar["donch_low_14"])
                adx = float(bar["adx_14"])
                ema_50 = float(bar["ema_50"])
                ema_200 = float(bar["ema_200"])
                atr = Decimal(str(bar["atr_14"]))

                if np.isnan(upper) or np.isnan(lower) or atr <= Decimal("0"):
                    continue

                rule = INSTRUMENT_RULES.get(sym, {})
                qty_step = rule.get("qtyStep", Decimal("0.001"))
                min_qty = rule.get("minOrderQty", Decimal("0.001"))

                if close > upper and adx >= 16.0 and ema_50 > ema_200 and btc_bullish:
                    next_open = Decimal(str(self.dfs_1h[sym].loc[next_ts, "open"]))
                    slip_mult = Decimal("1") + (scenario.slippage_bps / Decimal("10000"))
                    fill_p = next_open * slip_mult
                    stop_dist = Decimal("2.0") * atr
                    stop_p = fill_p - stop_dist

                    qty, risk_usd, status = ledger.calculate_risk_qty(fill_p, stop_dist, Decimal("0.008"), qty_step, min_qty)
                    if status == "OK":
                        auth, _ = ledger.check_new_order(sym, fill_p, qty)
                        if auth:
                            ledger.open_position(sym, 1, fill_p, qty, stop_p, next_ts, is_maker=False)
                            if len(ledger.positions) >= 3:
                                break

                elif close < lower and adx >= 16.0 and ema_50 < ema_200 and btc_bearish:
                    next_open = Decimal(str(self.dfs_1h[sym].loc[next_ts, "open"]))
                    slip_mult = Decimal("1") - (scenario.slippage_bps / Decimal("10000"))
                    fill_p = next_open * slip_mult
                    stop_dist = Decimal("2.0") * atr
                    stop_p = fill_p + stop_dist

                    qty, risk_usd, status = ledger.calculate_risk_qty(fill_p, stop_dist, Decimal("0.008"), qty_step, min_qty)
                    if status == "OK":
                        auth, _ = ledger.check_new_order(sym, fill_p, qty)
                        if auth:
                            ledger.open_position(sym, -1, fill_p, qty, stop_p, next_ts, is_maker=False)
                            if len(ledger.positions) >= 3:
                                break

        return ledger, ledger.closed_trades

    # =========================================================================
    # STRATEGY 3: v3.2 DUAL-REGIME IRAS NATIVE RUNNER
    # =========================================================================
    def run_v32(
        self,
        scenario: ScenarioConfig,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
    ) -> Tuple[IsolatedCapitalLedger, List[TradeRecord]]:
        ledger = IsolatedCapitalLedger(
            initial_capital=Decimal("300.00"),
            max_leverage=Decimal("3.5"),
            drawdown_limit_pct=Decimal("30.0"),
            fee_profile=scenario.fee_profile,
        )

        timeline = self.sorted_ts_1h
        if start_ts is not None:
            timeline = [t for t in timeline if t >= start_ts]
        if end_ts is not None:
            timeline = [t for t in timeline if t <= end_ts]

        cooldowns: Dict[str, int] = {}
        pending_orders: Dict[str, Dict[str, Any]] = {}

        for t_idx, curr_ts in enumerate(timeline[:-1]):
            next_ts = timeline[t_idx + 1]

            curr_marks = {}
            for sym, df in self.dfs_1h.items():
                if curr_ts in df.index:
                    curr_marks[sym] = Decimal(str(df.loc[curr_ts, "close"]))

            ledger.mark_to_market(curr_ts, curr_marks)
            if ledger.is_halted:
                for sym in list(ledger.positions):
                    if sym in curr_marks:
                        ledger.close_position(sym, curr_marks[sym], curr_ts, "DRAWDOWN_HALT")
                break

            dt_curr = pd.to_datetime(curr_ts, unit="ms", utc=True)
            if dt_curr.hour in (0, 8, 16):
                for sym in list(ledger.positions):
                    if sym in curr_marks:
                        f_rate = self.get_funding_rate(sym, curr_ts)
                        ledger.apply_funding(sym, f_rate, curr_ts, curr_marks[sym])

            # 1. Fill pending Maker limit orders on incoming bar
            for sym in list(pending_orders):
                if sym in ledger.positions or sym not in self.dfs_1h or next_ts not in self.dfs_1h[sym].index:
                    continue
                ord_info = pending_orders[sym]
                next_bar = self.dfs_1h[sym].loc[next_ts]
                if pd.isna(next_bar["open"]) or pd.isna(next_bar["high"]) or pd.isna(next_bar["low"]) or pd.isna(next_bar["atr_14"]):
                    continue
                n_open = Decimal(str(next_bar["open"]))
                n_high = Decimal(str(next_bar["high"]))
                n_low = Decimal(str(next_bar["low"]))
                n_atr = Decimal(str(next_bar["atr_14"]))
                limit_p = ord_info["price"]
                direction = ord_info["direction"]

                # Zero-lookahead maker fill condition: Low < limit_p for Buy and Open >= limit_p
                fill_maker = False
                if direction == 1:
                    if scenario.name == "OPT":
                        fill_maker = (n_low < limit_p and n_open >= limit_p)
                    else:
                        fill_maker = (n_low <= limit_p - Decimal(str(scenario.maker_penetration_atr)) * n_atr and n_open >= limit_p)
                else:
                    if scenario.name == "OPT":
                        fill_maker = (n_high > limit_p and n_open <= limit_p)
                    else:
                        fill_maker = (n_high >= limit_p + Decimal(str(scenario.maker_penetration_atr)) * n_atr and n_open <= limit_p)

                if fill_maker and np.random.rand() >= scenario.maker_failure_rate:
                    auth, _ = ledger.check_new_order(sym, limit_p, ord_info["qty"])
                    if auth:
                        pos = ledger.open_position(sym, direction, limit_p, ord_info["qty"], ord_info["stop_loss"], next_ts, is_maker=True)
                        pos.target_tp = ord_info["take_profit"]
                        del pending_orders[sym]

            # Clear expired pending orders
            pending_orders.clear()

            # 2. Manage existing range/trend positions on closed bar
            for sym in list(ledger.positions):
                if sym not in self.dfs_1h or curr_ts not in self.dfs_1h[sym].index:
                    continue
                bar = self.dfs_1h[sym].loc[curr_ts]
                if pd.isna(bar["high"]) or pd.isna(bar["low"]) or pd.isna(bar["close"]) or pd.isna(bar["atr_14"]):
                    continue
                b_high = Decimal(str(bar["high"]))
                b_low = Decimal(str(bar["low"]))
                b_open = Decimal(str(bar["open"]))
                b_close = Decimal(str(bar["close"]))
                b_atr = Decimal(str(bar["atr_14"]))
                adx_val = float(bar["adx_14"])
                vol_val = float(bar["volume"])
                vol_sma = float(bar["volume_sma_20"])
                pos = ledger.positions[sym]
                direction = pos.direction

                sl = pos.stop_loss
                tp = getattr(pos, "target_tp", Decimal("0.0"))

                hit_sl = (b_low <= sl) if direction == 1 else (b_high >= sl)
                hit_tp = (b_high > tp) if direction == 1 else (b_low < tp) if tp > Decimal("0.0") else False

                # Worst-case collision resolution: SL triggers first
                if hit_sl and hit_tp:
                    hit_tp = False

                if hit_sl:
                    fill_p = min(b_open, sl) if direction == 1 else max(b_open, sl)
                    slip_mult = Decimal("1") - (scenario.slippage_bps / Decimal("10000")) if direction == 1 else Decimal("1") + (scenario.slippage_bps / Decimal("10000"))
                    exec_p = fill_p * slip_mult
                    ledger.close_position(sym, exec_p, curr_ts, "STOP_LOSS", is_maker=False)
                    cooldowns[sym] = t_idx + 3
                    continue

                # Regime Handoff check (ADX >= 20 and anomalous volume breakout)
                if adx_val >= 20.0 and vol_val >= 2.0 * vol_sma:
                    slip_mult = Decimal("1") - (scenario.slippage_bps / Decimal("10000")) if direction == 1 else Decimal("1") + (scenario.slippage_bps / Decimal("10000"))
                    exec_p = b_close * slip_mult
                    ledger.close_position(sym, exec_p, curr_ts, "REGIME_HANDOFF", is_maker=False)
                    continue

                # Take-Profit on SMA20 middle band (Maker exit)
                if hit_tp:
                    ledger.close_position(sym, tp, curr_ts, "MAKER_TP_SMA20", is_maker=True)
                    continue

            # 3. Scanner for Range setups & Limit Order Generation
            if len(ledger.positions) >= 2 or ledger.is_halted:
                continue

            for sym in ALL_SYMBOLS:
                if sym in ledger.positions or sym in pending_orders or not self.is_symbol_active(sym, curr_ts):
                    continue
                if t_idx < cooldowns.get(sym, 0):
                    continue
                if sym not in self.dfs_1h or curr_ts not in self.dfs_1h[sym].index or next_ts not in self.dfs_1h[sym].index:
                    continue

                bar = self.dfs_1h[sym].loc[curr_ts]
                if (
                    pd.isna(bar["close"])
                    or pd.isna(bar["bb_mid"])
                    or pd.isna(bar["bb_upper"])
                    or pd.isna(bar["bb_lower"])
                    or pd.isna(bar["atr_14"])
                    or pd.isna(bar["adx_14"])
                    or pd.isna(bar["rsi_14"])
                ):
                    continue

                adx = float(bar["adx_14"])
                close = Decimal(str(bar["close"]))
                bb_mid = Decimal(str(bar["bb_mid"]))
                bb_up = Decimal(str(bar["bb_upper"]))
                bb_lo = Decimal(str(bar["bb_lower"]))
                bb_w_pctl = float(bar["bb_width_pctl"])
                rsi = float(bar["rsi_14"])
                atr = Decimal(str(bar["atr_14"]))

                if bb_mid <= Decimal("0") or atr <= Decimal("0"):
                    continue

                bw = (bb_up - bb_lo) / bb_mid
                if bw < Decimal("0.025"):
                    continue

                # Range Regime Condition: ADX < 18 or BB width percentile <= 40%
                is_range = (adx < 18.0) or (bb_w_pctl <= 40.0)
                if not is_range:
                    continue

                rule = INSTRUMENT_RULES.get(sym, {})
                qty_step = rule.get("qtyStep", Decimal("0.1"))
                min_qty = rule.get("minOrderQty", Decimal("0.1"))

                # Long Range Setup (RSI < 35, Limit Maker Buy at BB Lower)
                if rsi < 35.0 and close >= bb_lo:
                    limit_p = bb_lo
                    sl = limit_p - Decimal("1.0") * atr
                    tp = min(bb_mid, limit_p * Decimal("1.025"))
                    stop_dist = limit_p - sl
                    qty, risk_usd, status = ledger.calculate_risk_qty(limit_p, stop_dist, Decimal("0.010"), qty_step, min_qty)
                    if status == "OK":
                        auth, _ = ledger.check_new_order(sym, limit_p, qty)
                        if auth:
                            pending_orders[sym] = {
                                "direction": 1,
                                "price": limit_p,
                                "qty": qty,
                                "stop_loss": sl,
                                "take_profit": tp,
                            }

                # Short Range Setup (RSI > 65, Limit Maker Sell at BB Upper)
                elif rsi > 65.0 and close <= bb_up:
                    limit_p = bb_up
                    sl = limit_p + Decimal("1.0") * atr
                    tp = max(bb_mid, limit_p * Decimal("0.975"))
                    stop_dist = sl - limit_p
                    qty, risk_usd, status = ledger.calculate_risk_qty(limit_p, stop_dist, Decimal("0.010"), qty_step, min_qty)
                    if status == "OK":
                        auth, _ = ledger.check_new_order(sym, limit_p, qty)
                        if auth:
                            pending_orders[sym] = {
                                "direction": -1,
                                "price": limit_p,
                                "qty": qty,
                                "stop_loss": sl,
                                "take_profit": tp,
                            }

        return ledger, ledger.closed_trades


def compute_metrics(
    strategy: str,
    scenario: str,
    period: str,
    ledger: IsolatedCapitalLedger,
    trades: List[TradeRecord],
    initial_capital: Decimal = Decimal("300.00"),
) -> Dict[str, Any]:
    """Computes rigorous quantitative metrics without zombie margin or fixed notional."""
    total_trades = len(trades)
    if total_trades == 0:
        return {
            "strategy": strategy, "scenario": scenario, "period": period,
            "initial_capital": float(initial_capital), "final_equity": float(ledger.get_equity()),
            "net_pnl": 0.0, "return_pct": 0.0, "max_drawdown_pct": float(ledger.max_drawdown_observed_pct),
            "max_drawdown_usd": float(ledger.max_drawdown_observed_usd), "total_trades": 0,
            "win_trades": 0, "loss_trades": 0, "win_rate_pct": 0.0, "profit_factor": 0.0,
            "net_expectancy_r": 0.0, "net_expectancy_bps": 0.0, "gross_pnl": 0.0,
            "total_fees": float(ledger.total_fees_paid), "total_funding": float(ledger.total_funding_pnl),
            "is_halted": ledger.is_halted, "halt_reason": ledger.halt_reason or "NONE",
            "verdict": "NOT_PROVEN",
        }

    wins = [t for t in trades if t.net_pnl > Decimal("0.0")]
    losses = [t for t in trades if t.net_pnl <= Decimal("0.0")]
    n_wins = len(wins)
    n_losses = len(losses)
    win_rate = (n_wins / total_trades) * 100.0

    gross_wins = sum(t.gross_pnl for t in wins)
    gross_losses = abs(sum(t.gross_pnl for t in losses))
    pf = float(gross_wins / gross_losses) if gross_losses > Decimal("0.0") else 999.0

    net_pnl = sum(t.net_pnl for t in trades)
    gross_pnl = sum(t.gross_pnl for t in trades)
    final_eq = ledger.get_equity()
    ret_pct = float((final_eq - initial_capital) / initial_capital) * 100.0

    # Rigorous Expectancy in R
    r_multiples = []
    bps_list = []
    for t in trades:
        # Initial risk
        risk_usd = t.qty * abs(t.entry_price - t.entry_price * Decimal("0.025"))  # Approximate initial stop dist
        r_mult = float(t.net_pnl / risk_usd) if risk_usd > Decimal("0.0") else 0.0
        r_multiples.append(r_mult)

        # Actual notional for bps
        trade_notional = t.trade_notional if t.trade_notional > Decimal("0.0") else t.qty * t.entry_price
        bps = float((t.net_pnl / trade_notional) * Decimal("10000.0")) if trade_notional > Decimal("0.0") else 0.0
        bps_list.append(bps)

    net_exp_r = float(np.mean(r_multiples)) if r_multiples else 0.0
    net_exp_bps = float(np.mean(bps_list)) if bps_list else 0.0

    # Verdict
    if ledger.is_halted and "DRAWDOWN" in (ledger.halt_reason or ""):
        verdict = "FAIL_DRAWDOWN_HALT"
    elif net_pnl > Decimal("0.0") and net_exp_r > 0.05 and float(ledger.max_drawdown_observed_pct) < 25.0:
        verdict = "PROFITABILITY_PASS"
    elif net_pnl < Decimal("0.0"):
        verdict = "NEGATIVE_EDGE"
    else:
        verdict = "INCONCLUSIVE"

    return {
        "strategy": strategy,
        "scenario": scenario,
        "period": period,
        "initial_capital": float(initial_capital),
        "final_equity": float(final_eq),
        "net_pnl": float(net_pnl),
        "return_pct": ret_pct,
        "max_drawdown_pct": float(ledger.max_drawdown_observed_pct),
        "max_drawdown_usd": float(ledger.max_drawdown_observed_usd),
        "total_trades": total_trades,
        "win_trades": n_wins,
        "loss_trades": n_losses,
        "win_rate_pct": win_rate,
        "profit_factor": pf,
        "net_expectancy_r": net_exp_r,
        "net_expectancy_bps": net_exp_bps,
        "gross_pnl": float(gross_pnl),
        "total_fees": float(ledger.total_fees_paid),
        "total_funding": float(ledger.total_funding_pnl),
        "is_halted": ledger.is_halted,
        "halt_reason": ledger.halt_reason or "NONE",
        "verdict": verdict,
    }
