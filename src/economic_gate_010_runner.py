"""
ECONOMIC-GATE-010: Independent Net Profitability Audit of 7 Strategies.
Module: src/economic_gate_010_runner.py

Executes a zero-lookahead, point-in-time quantitative backtest and cost attribution
for 7 target strategies across 34 months of historical Bybit linear perpetual data (2024-01-01 to 2026-10-22).

Adheres strictly to:
1. FEE-CONTRACT-009B: Conservative Operator Profile (Maker 0.0360%, Taker 0.1000%).
2. Real simulated execution: Market Taker vs Limit Maker with reachability / queue penetration.
3. Strict Point-in-Time universe (listing dates enforced, e.g. RENDER starting 2024-07-26).
4. No double counting of spread, slippage, and fees.
5. 8-hour funding cashflows credited/debited directly to financial Ledger.
6. IS (18m) / Retrospective Holdout (16m) temporal isolation.
7. Uncompounded $300.00 base equity per strategy run.
"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR, ROUND_HALF_UP
import json
import logging
import math
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
import pandas as pd

# Add repo root to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.fee_contract_009b import (
    FeeProfile,
    FeeSource,
    CONSERVATIVE_OPERATOR_PROFILE,
    MNT_SENSITIVITY_PROFILE,
    calculate_funding_cashflow,
)

logger = logging.getLogger("EconomicGate010")

DATA_DIR = REPO_ROOT / "data" / "parquet_2024_2026"
INSTRUMENTS_INFO_FILE = REPO_ROOT / "data" / "instruments_info.json"

# All 18 perpetual symbols available in parquet_2024_2026
ALL_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT", "AVAXUSDT",
    "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT", "XRPUSDT", "BNBUSDT",
    "TIAUSDT", "INJUSDT", "ARBUSDT", "OPUSDT", "1000PEPEUSDT", "RENDERUSDT"
]

# Verified listing timestamps (UTC)
# RENDERUSDT was listed on 2024-07-26 08:00:00 UTC (1721980800000 ms)
LISTING_TIMESTAMPS: Dict[str, int] = {
    "RENDERUSDT": 1721980800000,
}

# Default contract rules approximation
DEFAULT_RULES: Dict[str, Dict[str, float]] = {
    "BTCUSDT": {"minQty": 0.001, "step": 0.001, "minNotional": 5.0, "tickSize": 0.10},
    "ETHUSDT": {"minQty": 0.01, "step": 0.01, "minNotional": 5.0, "tickSize": 0.01},
    "SOLUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0, "tickSize": 0.01},
    "SUIUSDT": {"minQty": 1.0, "step": 1.0, "minNotional": 5.0, "tickSize": 0.0001},
    "DOGEUSDT": {"minQty": 10.0, "step": 10.0, "minNotional": 5.0, "tickSize": 0.00001},
    "AVAXUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0, "tickSize": 0.001},
    "NEARUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0, "tickSize": 0.001},
    "LINKUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0, "tickSize": 0.001},
    "APTUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0, "tickSize": 0.001},
    "ADAUSDT": {"minQty": 1.0, "step": 1.0, "minNotional": 5.0, "tickSize": 0.0001},
    "XRPUSDT": {"minQty": 1.0, "step": 1.0, "minNotional": 5.0, "tickSize": 0.0001},
    "BNBUSDT": {"minQty": 0.01, "step": 0.01, "minNotional": 5.0, "tickSize": 0.01},
    "TIAUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0, "tickSize": 0.001},
    "INJUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0, "tickSize": 0.001},
    "ARBUSDT": {"minQty": 1.0, "step": 1.0, "minNotional": 5.0, "tickSize": 0.0001},
    "OPUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0, "tickSize": 0.001},
    "1000PEPEUSDT": {"minQty": 100.0, "step": 100.0, "minNotional": 5.0, "tickSize": 0.000001},
    "RENDERUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0, "tickSize": 0.001},
}


@dataclass
class ExecutionConfig:
    name: str  # "OPT", "BASE", "STRESS"
    fee_profile: FeeProfile
    slippage_bps: float
    half_spread_bps: float
    maker_penetration_atr: float  # Required price penetration in ATR for maker limit fill
    maker_failure_rate: float     # Probability of limit cancellation/unfilled timeout
    high_vol_slippage_mult: float = 1.0


CONFIG_OPT = ExecutionConfig(
    name="OPT",
    fee_profile=MNT_SENSITIVITY_PROFILE,  # 0.0324% maker, 0.0900% taker
    slippage_bps=1.0,
    half_spread_bps=0.5,
    maker_penetration_atr=0.0,   # Fill on touch
    maker_failure_rate=0.0,
    high_vol_slippage_mult=1.0,
)

CONFIG_BASE = ExecutionConfig(
    name="BASE",
    fee_profile=CONSERVATIVE_OPERATOR_PROFILE,  # 0.0360% maker, 0.1000% taker
    slippage_bps=2.0,
    half_spread_bps=1.5,
    maker_penetration_atr=0.05,  # Requires 0.05 ATR price penetration
    maker_failure_rate=0.05,     # 5% cancellation/timeout
    high_vol_slippage_mult=1.5,
)

CONFIG_STRESS = ExecutionConfig(
    name="STRESS",
    fee_profile=CONSERVATIVE_OPERATOR_PROFILE,  # 0.0360% maker, 0.1000% taker
    slippage_bps=5.0,
    half_spread_bps=3.0,
    maker_penetration_atr=0.20,  # Requires 0.20 ATR price penetration
    maker_failure_rate=0.30,     # 30% unfill rate
    high_vol_slippage_mult=2.5,  # Heavy slip in high vol
)


@dataclass
class GateTrade:
    trade_id: str
    strategy: str
    scenario: str
    symbol: str
    direction: int              # +1 Long, -1 Short
    entry_ts: int
    exit_ts: int
    entry_price: float
    exit_price: float
    initial_qty: float
    current_qty: float
    entry_type: str             # "MARKET_TAKER" or "LIMIT_MAKER"
    exit_type: str              # "MARKET_TAKER" or "LIMIT_MAKER"
    gross_pnl: float
    entry_fee: float
    exit_fee: float
    slippage_cost: float
    spread_cost: float
    funding_cashflow: float
    net_pnl: float
    return_pct: float
    pnl_r: float
    exit_reason: str
    was_pyramided: bool
    hold_hours: float
    capital_after: float
    initial_atr: float
    period: str                 # "IS", "OOS", "FULL"
    regime: str                 # "BULL", "BEAR", "CHOP"


@dataclass
class StrategyMetrics:
    strategy: str
    scenario: str
    period: str
    initial_capital: float
    final_equity: float
    net_pnl: float
    return_pct: float
    cagr_pct: float
    max_drawdown_pct: float
    max_drawdown_usd: float
    total_trades: int
    win_trades: int
    loss_trades: int
    win_rate_pct: float
    profit_factor: float
    payoff_ratio: float
    net_expectancy_r: float
    net_expectancy_bps: float
    sharpe_ratio: float
    sortino_ratio: float
    calmar_ratio: float
    gross_pnl: float
    total_fees: float
    maker_fees: float
    taker_fees: float
    total_slippage: float
    total_spread: float
    total_funding: float
    random_benchmark_net_pnl: float
    alpha_over_random_bps: float
    verdict: str


def compute_hourly_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculates all indicators strictly on closed 1h bars with causal shift(1)."""
    df = df.copy().sort_values("timestamp").reset_index(drop=True)
    c = df["close"]
    h = df["high"]
    l = df["low"]
    v = df["volume"]

    # True Range & ATR(14)
    tr1 = h - l
    tr2 = (h - c.shift(1)).abs()
    tr3 = (l - c.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["tr"] = tr
    df["atr_14"] = tr.ewm(alpha=1.0 / 14.0, adjust=False).mean()

    # Donchian 120 (5 days) strictly shifted by 1 bar
    df["donch_high_120"] = h.shift(1).rolling(120).max()
    df["donch_low_120"] = l.shift(1).rolling(120).min()

    # Donchian 14 strictly shifted by 1 bar (for v3.8)
    df["donch_high_14"] = h.shift(1).rolling(14).max()
    df["donch_low_14"] = l.shift(1).rolling(14).min()

    # EMAs
    df["ema_50"] = c.ewm(span=50, adjust=False).mean()
    df["ema_200"] = c.ewm(span=200, adjust=False).mean()

    # Volume SMA(20)
    df["vol_sma_20"] = v.rolling(20).mean()

    # Bollinger Bands 20, 2.0 std
    df["bb_mid"] = c.rolling(20).mean()
    df["bb_std"] = c.rolling(20).std(ddof=0)
    df["bb_upper"] = df["bb_mid"] + 2.0 * df["bb_std"]
    df["bb_lower"] = df["bb_mid"] - 2.0 * df["bb_std"]
    df["bb_bandwidth"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]

    # Kaufman Efficiency Ratio (KER_14)
    net_chg = (c.shift(1) - c.shift(15)).abs()
    gross_chg = (c.shift(1) - c.shift(2)).abs().rolling(14).sum()
    df["ker_14"] = (net_chg / gross_chg.replace(0, np.nan)).fillna(0.5).clip(0.0, 1.0)

    # Directional Movement & ADX 14
    up_move = h - h.shift(1)
    down_move = l.shift(1) - l
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr_smooth = tr.ewm(alpha=1.0 / 14.0, adjust=False).mean()
    plus_di = 100.0 * pd.Series(plus_dm).ewm(alpha=1.0 / 14.0, adjust=False).mean() / tr_smooth.replace(0, np.nan)
    minus_di = 100.0 * pd.Series(minus_dm).ewm(alpha=1.0 / 14.0, adjust=False).mean() / tr_smooth.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["adx_14"] = dx.ewm(alpha=1.0 / 14.0, adjust=False).mean().fillna(15.0)

    # Normalized ATR
    df["norm_atr"] = df["atr_14"] / c

    return df


def resample_4h_indicators(df_1h: pd.DataFrame) -> pd.DataFrame:
    """Resamples 1h data to 4h candle grid and calculates 4h indicators."""
    df = df_1h.copy().sort_values("timestamp").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["timestamp"], unit="ms")
    df_4h = (
        df.resample("4h", on="dt")
        .agg({
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
            "timestamp": "first",
        })
        .dropna()
        .reset_index(drop=True)
    )

    c = df_4h["close"]
    h = df_4h["high"]
    l = df_4h["low"]
    v = df_4h["volume"]

    # True Range & ATR(14)
    tr1 = h - l
    tr2 = (h - c.shift(1)).abs()
    tr3 = (l - c.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df_4h["tr"] = tr
    df_4h["atr_14"] = tr.ewm(alpha=1.0 / 14.0, adjust=False).mean()

    # Donchian 30 (30 * 4h = 120h)
    df_4h["donch_high_30"] = h.shift(1).rolling(30).max()
    df_4h["donch_low_30"] = l.shift(1).rolling(30).min()

    # EMAs
    df_4h["ema_50"] = c.ewm(span=50, adjust=False).mean()
    df_4h["ema_200"] = c.ewm(span=200, adjust=False).mean()

    # ADX 14 on 4h
    up_move = h - h.shift(1)
    down_move = l.shift(1) - l
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr_smooth = tr.ewm(alpha=1.0 / 14.0, adjust=False).mean()
    plus_di = 100.0 * pd.Series(plus_dm).ewm(alpha=1.0 / 14.0, adjust=False).mean() / tr_smooth.replace(0, np.nan)
    minus_di = 100.0 * pd.Series(minus_dm).ewm(alpha=1.0 / 14.0, adjust=False).mean() / tr_smooth.replace(0, np.nan)
    dx = 100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df_4h["adx_14"] = dx.ewm(alpha=1.0 / 14.0, adjust=False).mean().fillna(15.0)

    df_4h["vol_sma_20"] = v.rolling(20).mean()
    df_4h["norm_atr"] = df_4h["atr_14"] / c

    return df_4h


print("EconomicGate010 runner core classes and indicator math loaded.")


# ==============================================================================
# Simulation Engine Class
# ==============================================================================

class EconomicGate010Engine:
    """
    Unified multi-strategy quantitative execution simulator.
    Evaluates each strategy under strictly isolated capital ($300.00)
    and executes point-in-time order fills adhering to FeeContract009B.
    """

    def __init__(
        self,
        data_dir: Path = DATA_DIR,
        initial_capital: float = 300.0,
    ):
        self.data_dir = data_dir
        self.initial_capital = initial_capital
        self.dfs_1h: Dict[str, pd.DataFrame] = {}
        self.dfs_4h: Dict[str, pd.DataFrame] = {}
        self.sorted_ts_1h: List[int] = []
        self.sorted_ts_4h: List[int] = []
        self.funding_rates: Dict[str, pd.DataFrame] = {}
        self._load_and_prepare_data()

    def _load_and_prepare_data(self):
        """Loads and prepares 1h and 4h data for all 18 perpetual assets."""
        ts_set_1h = set()
        ts_set_4h = set()

        for sym in ALL_SYMBOLS:
            f = self.data_dir / f"{sym}_1h.parquet"
            if not f.exists():
                logger.warning(f"File missing: {f}")
                continue

            raw_df = pd.read_parquet(f)
            df_1h = compute_hourly_indicators(raw_df)
            self.dfs_1h[sym] = df_1h.set_index("timestamp")
            ts_set_1h.update(df_1h["timestamp"].tolist())

            df_4h = resample_4h_indicators(raw_df)
            self.dfs_4h[sym] = df_4h.set_index("timestamp")
            ts_set_4h.update(df_4h["timestamp"].tolist())

        self.sorted_ts_1h = sorted(list(ts_set_1h))
        self.sorted_ts_4h = sorted(list(ts_set_4h))

        # Load BTC/ETH funding if present
        for sym in ["BTCUSDT", "ETHUSDT"]:
            f_fund = REPO_ROOT / "data" / "parquet" / f"{sym}_funding.parquet"
            if f_fund.exists():
                df_f = pd.read_parquet(f_fund).sort_values("fundingRateTimestamp")
                self.funding_rates[sym] = df_f.set_index("fundingRateTimestamp")

        logger.info(
            f"Loaded {len(self.dfs_1h)} assets. 1h timeline: {len(self.sorted_ts_1h)} bars, "
            f"4h timeline: {len(self.sorted_ts_4h)} bars."
        )

    def is_symbol_active(self, symbol: str, timestamp: int) -> bool:
        """Enforces Point-in-Time universe: verifies asset was listed at timestamp."""
        listing_ts = LISTING_TIMESTAMPS.get(symbol)
        if listing_ts is not None and timestamp < listing_ts:
            return False
        df = self.dfs_1h.get(symbol)
        if df is None or timestamp not in df.index:
            return False
        return True

    def get_funding_rate(self, symbol: str, timestamp: int) -> float:
        """Returns empirical or baseline Bybit funding rate for 8h settlement."""
        if symbol in self.funding_rates:
            df_f = self.funding_rates[symbol]
            if timestamp in df_f.index:
                return float(df_f.loc[timestamp, "fundingRate"])
            idx = df_f.index.searchsorted(timestamp, side="right") - 1
            if 0 <= idx < len(df_f):
                return float(df_f.iloc[idx]["fundingRate"])
        return 0.000100

    def compute_drawdown(self, equity_curve: List[float]) -> Tuple[float, float]:
        """Calculates maximum drawdown in percent and USD."""
        if not equity_curve:
            return 0.0, 0.0
        peak = equity_curve[0]
        max_dd_pct = 0.0
        max_dd_usd = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd_usd = peak - eq
            dd_pct = (dd_usd / peak) * 100.0 if peak > 0 else 0.0
            if dd_pct > max_dd_pct:
                max_dd_pct = dd_pct
            if dd_usd > max_dd_usd:
                max_dd_usd = dd_usd
        return max_dd_pct, max_dd_usd

    def get_market_regime(self, timestamp: int) -> str:
        """Classifies broad crypto regime: BULL, BEAR, or CHOP using BTC."""
        btc_df = self.dfs_1h.get("BTCUSDT")
        if btc_df is None or timestamp not in btc_df.index:
            return "CHOP"
        row = btc_df.loc[timestamp]
        close = float(row["close"])
        ema_200 = float(row["ema_200"])
        adx = float(row["adx_14"])
        ker = float(row["ker_14"])

        if adx < 18.0 or ker <= 0.25:
            return "CHOP"
        elif close > ema_200:
            return "BULL"
        else:
            return "BEAR"

    # --------------------------------------------------------------------------
    # STRATEGY 1: v3.1.4 (4h Trend Breakout Champion)
    # --------------------------------------------------------------------------
    def run_v314(self, exec_cfg: ExecutionConfig) -> Tuple[List[GateTrade], List[Tuple[int, float]]]:
        equity = self.initial_capital
        peak = equity
        open_pos: Dict[str, Dict[str, Any]] = {}
        closed_trades: List[GateTrade] = []
        equity_records: List[Tuple[int, float]] = []
        cooldown: Dict[str, int] = {}
        trade_counter = 0

        for i in range(len(self.sorted_ts_4h) - 1):
            curr_ts = self.sorted_ts_4h[i]
            next_ts = self.sorted_ts_4h[i + 1]
            regime = self.get_market_regime(curr_ts)

            dt_curr = pd.to_datetime(curr_ts, unit="ms", utc=True)
            is_fund_bar = (dt_curr.hour % 8 == 0)

            # 1. Manage Active Positions on current bar
            for sym in list(open_pos.keys()):
                pos = open_pos[sym]
                df_4h = self.dfs_4h[sym]
                if curr_ts not in df_4h.index:
                    continue
                bar = df_4h.loc[curr_ts]
                b_high = float(bar["high"])
                b_low = float(bar["low"])
                b_open = float(bar["open"])
                b_close = float(bar["close"])
                b_atr = float(bar["atr_14"])

                if is_fund_bar:
                    f_rate = self.get_funding_rate(sym, curr_ts)
                    fund_cf = -pos["direction"] * (pos["current_qty"] * b_close) * f_rate
                    pos["funding_cashflow"] += fund_cf
                    equity += fund_cf

                if pos["direction"] == 1:
                    new_stop = b_high - 2.5 * b_atr
                    if new_stop > pos["current_stop"]:
                        pos["current_stop"] = new_stop
                    if not pos["is_pyramided"] and b_high >= pos["entry_price"] + 2.0 * pos["initial_atr"]:
                        add_qty = pos["initial_qty"] * 0.5
                        add_price = pos["entry_price"] + 2.0 * pos["initial_atr"]
                        add_slip = add_price * (exec_cfg.slippage_bps / 10000.0)
                        eff_add_price = add_price + add_slip
                        add_fee = add_qty * eff_add_price * float(exec_cfg.fee_profile.taker_rate)
                        pos["entry_fee"] += add_fee
                        pos["slippage_cost"] += (eff_add_price - add_price) * add_qty
                        total_qty = pos["current_qty"] + add_qty
                        pos["entry_price"] = (pos["current_qty"] * pos["entry_price"] + add_qty * eff_add_price) / total_qty
                        pos["current_qty"] = total_qty
                        pos["is_pyramided"] = True
                        pos["current_stop"] = pos["entry_price"]
                        equity -= add_fee

                    if b_low <= pos["current_stop"]:
                        raw_exit = min(b_open, pos["current_stop"]) if b_open <= pos["current_stop"] else pos["current_stop"]
                        slip = raw_exit * (exec_cfg.slippage_bps / 10000.0)
                        if b_atr / b_close > 0.03:
                            slip *= exec_cfg.high_vol_slippage_mult
                        exit_price = raw_exit - slip
                        exit_fee = pos["current_qty"] * exit_price * float(exec_cfg.fee_profile.taker_rate)
                        gross_pnl = pos["current_qty"] * (exit_price - pos["entry_price"])
                        net_pnl = gross_pnl - pos["entry_fee"] - exit_fee + pos["funding_cashflow"]
                        equity += (gross_pnl - exit_fee)

                        trade_counter += 1
                        hold_h = (curr_ts - pos["entry_ts"]) / 3600000.0
                        period_tag = "IS" if curr_ts < 1751241600000 else "OOS"
                        pnl_r = net_pnl / pos["risk_usd"] if pos["risk_usd"] > 0 else 0.0

                        closed_trades.append(GateTrade(
                            trade_id=f"v314_{trade_counter:04d}",
                            strategy="v3.1.4",
                            scenario=exec_cfg.name,
                            symbol=sym,
                            direction=pos["direction"],
                            entry_ts=pos["entry_ts"],
                            exit_ts=curr_ts,
                            entry_price=pos["entry_price"],
                            exit_price=exit_price,
                            initial_qty=pos["initial_qty"],
                            current_qty=pos["current_qty"],
                            entry_type="MARKET_TAKER",
                            exit_type="MARKET_TAKER",
                            gross_pnl=gross_pnl,
                            entry_fee=pos["entry_fee"],
                            exit_fee=exit_fee,
                            slippage_cost=pos["slippage_cost"] + slip * pos["current_qty"],
                            spread_cost=pos["spread_cost"] + pos["current_qty"] * exit_price * (exec_cfg.half_spread_bps / 10000.0),
                            funding_cashflow=pos["funding_cashflow"],
                            net_pnl=net_pnl,
                            return_pct=(net_pnl / self.initial_capital) * 100.0,
                            pnl_r=pnl_r,
                            exit_reason="STOP_LOSS",
                            was_pyramided=pos["is_pyramided"],
                            hold_hours=hold_h,
                            capital_after=equity,
                            initial_atr=pos["initial_atr"],
                            period=period_tag,
                            regime=regime,
                        ))
                        cooldown[sym] = i + 3
                        del open_pos[sym]

                else: # Short direction
                    new_stop = b_low + 2.5 * b_atr
                    if new_stop < pos["current_stop"]:
                        pos["current_stop"] = new_stop
                    if not pos["is_pyramided"] and b_low <= pos["entry_price"] - 2.0 * pos["initial_atr"]:
                        add_qty = pos["initial_qty"] * 0.5
                        add_price = pos["entry_price"] - 2.0 * pos["initial_atr"]
                        add_slip = add_price * (exec_cfg.slippage_bps / 10000.0)
                        eff_add_price = add_price - add_slip
                        add_fee = add_qty * eff_add_price * float(exec_cfg.fee_profile.taker_rate)
                        pos["entry_fee"] += add_fee
                        pos["slippage_cost"] += (add_price - eff_add_price) * add_qty
                        total_qty = pos["current_qty"] + add_qty
                        pos["entry_price"] = (pos["current_qty"] * pos["entry_price"] + add_qty * eff_add_price) / total_qty
                        pos["current_qty"] = total_qty
                        pos["is_pyramided"] = True
                        pos["current_stop"] = pos["entry_price"]
                        equity -= add_fee

                    if b_high >= pos["current_stop"]:
                        raw_exit = max(b_open, pos["current_stop"]) if b_open >= pos["current_stop"] else pos["current_stop"]
                        slip = raw_exit * (exec_cfg.slippage_bps / 10000.0)
                        if b_atr / b_close > 0.03:
                            slip *= exec_cfg.high_vol_slippage_mult
                        exit_price = raw_exit + slip
                        exit_fee = pos["current_qty"] * exit_price * float(exec_cfg.fee_profile.taker_rate)
                        gross_pnl = pos["current_qty"] * (pos["entry_price"] - exit_price)
                        net_pnl = gross_pnl - pos["entry_fee"] - exit_fee + pos["funding_cashflow"]
                        equity += (gross_pnl - exit_fee)

                        trade_counter += 1
                        hold_h = (curr_ts - pos["entry_ts"]) / 3600000.0
                        period_tag = "IS" if curr_ts < 1751241600000 else "OOS"
                        pnl_r = net_pnl / pos["risk_usd"] if pos["risk_usd"] > 0 else 0.0

                        closed_trades.append(GateTrade(
                            trade_id=f"v314_{trade_counter:04d}",
                            strategy="v3.1.4",
                            scenario=exec_cfg.name,
                            symbol=sym,
                            direction=pos["direction"],
                            entry_ts=pos["entry_ts"],
                            exit_ts=curr_ts,
                            entry_price=pos["entry_price"],
                            exit_price=exit_price,
                            initial_qty=pos["initial_qty"],
                            current_qty=pos["current_qty"],
                            entry_type="MARKET_TAKER",
                            exit_type="MARKET_TAKER",
                            gross_pnl=gross_pnl,
                            entry_fee=pos["entry_fee"],
                            exit_fee=exit_fee,
                            slippage_cost=pos["slippage_cost"] + slip * pos["current_qty"],
                            spread_cost=pos["spread_cost"] + pos["current_qty"] * exit_price * (exec_cfg.half_spread_bps / 10000.0),
                            funding_cashflow=pos["funding_cashflow"],
                            net_pnl=net_pnl,
                            return_pct=(net_pnl / self.initial_capital) * 100.0,
                            pnl_r=pnl_r,
                            exit_reason="STOP_LOSS",
                            was_pyramided=pos["is_pyramided"],
                            hold_hours=hold_h,
                            capital_after=equity,
                            initial_atr=pos["initial_atr"],
                            period=period_tag,
                            regime=regime,
                        ))
                        cooldown[sym] = i + 3
                        del open_pos[sym]

            # 2. Check Signals on current bar close
            btc_4h = self.dfs_4h.get("BTCUSDT")
            btc_bullish = False
            btc_bearish = False
            if btc_4h is not None and curr_ts in btc_4h.index:
                btc_c = float(btc_4h.loc[curr_ts, "close"])
                btc_ema = float(btc_4h.loc[curr_ts, "ema_50"])
                btc_bullish = (btc_c > btc_ema)
                btc_bearish = (btc_c < btc_ema)

            if len(open_pos) < 4:
                candidates = []
                for sym in ALL_SYMBOLS:
                    if sym in open_pos or cooldown.get(sym, 0) > i:
                        continue
                    if not self.is_symbol_active(sym, curr_ts) or not self.is_symbol_active(sym, next_ts):
                        continue
                    df_4h = self.dfs_4h[sym]
                    if curr_ts not in df_4h.index or next_ts not in df_4h.index:
                        continue
                    bar = df_4h.loc[curr_ts]
                    c = float(bar["close"])
                    upper = float(bar["donch_high_30"])
                    lower = float(bar["donch_low_30"])
                    adx = float(bar["adx_14"])
                    ema50 = float(bar["ema_50"])
                    ema200 = float(bar["ema_200"])
                    atr = float(bar["atr_14"])
                    norm_atr = float(bar["norm_atr"])

                    if adx < 18.0 or atr <= 0:
                        continue

                    if c > upper and ema50 > ema200 and (sym == "BTCUSDT" or btc_bullish):
                        score = norm_atr * adx
                        candidates.append((score, sym, 1, atr))
                    elif c < lower and ema50 < ema200 and (sym == "BTCUSDT" or btc_bearish):
                        score = norm_atr * adx
                        candidates.append((score, sym, -1, atr))

                candidates.sort(key=lambda x: x[0], reverse=True)
                for score, sym, direction, atr in candidates:
                    if len(open_pos) >= 4:
                        break
                    next_bar = self.dfs_4h[sym].loc[next_ts]
                    open_p = float(next_bar["open"])
                    rules = DEFAULT_RULES.get(sym, {"minQty": 0.01, "step": 0.01, "minNotional": 5.0})

                    risk_usd = 3.00
                    stop_dist = 2.5 * atr
                    if stop_dist <= 0:
                        continue
                    raw_qty = risk_usd / stop_dist
                    max_qty = (self.initial_capital * 3.5) / open_p
                    qty = min(raw_qty, max_qty)
                    step = rules["step"]
                    qty = math.floor(qty / step) * step
                    if qty < rules["minQty"] or (qty * open_p) < rules["minNotional"]:
                        continue

                    slip = open_p * (exec_cfg.slippage_bps / 10000.0)
                    eff_entry_p = open_p + slip if direction == 1 else open_p - slip
                    entry_fee = qty * eff_entry_p * float(exec_cfg.fee_profile.taker_rate)
                    spread_c = qty * eff_entry_p * (exec_cfg.half_spread_bps / 10000.0)
                    init_stop = eff_entry_p - stop_dist if direction == 1 else eff_entry_p + stop_dist

                    open_pos[sym] = {
                        "direction": direction,
                        "entry_ts": next_ts,
                        "entry_price": eff_entry_p,
                        "initial_qty": qty,
                        "current_qty": qty,
                        "initial_atr": atr,
                        "current_stop": init_stop,
                        "entry_fee": entry_fee,
                        "slippage_cost": abs(eff_entry_p - open_p) * qty,
                        "spread_cost": spread_c,
                        "funding_cashflow": 0.0,
                        "is_pyramided": False,
                        "risk_usd": risk_usd,
                    }
                    equity -= entry_fee

            equity_records.append((curr_ts, equity))

        return closed_trades, equity_records


    # --------------------------------------------------------------------------
    # STRATEGY 2: v3.8 (Adaptive Trend 1h - NewDragon v4 AdaptiveExitFSM)
    # --------------------------------------------------------------------------
    def run_v38(self, exec_cfg: ExecutionConfig) -> Tuple[List[GateTrade], List[Tuple[int, float]]]:
        equity = self.initial_capital
        open_pos: Dict[str, Dict[str, Any]] = {}
        closed_trades: List[GateTrade] = []
        equity_records: List[Tuple[int, float]] = []
        trade_counter = 0

        for i in range(len(self.sorted_ts_1h) - 1):
            curr_ts = self.sorted_ts_1h[i]
            next_ts = self.sorted_ts_1h[i + 1]
            regime = self.get_market_regime(curr_ts)

            dt_curr = pd.to_datetime(curr_ts, unit="ms", utc=True)
            is_fund_bar = (dt_curr.hour % 8 == 0)

            # 1. Manage Active Positions on current bar
            for sym in list(open_pos.keys()):
                pos = open_pos[sym]
                df_1h = self.dfs_1h[sym]
                if curr_ts not in df_1h.index:
                    continue
                bar = df_1h.loc[curr_ts]
                b_high = float(bar["high"])
                b_low = float(bar["low"])
                b_open = float(bar["open"])
                b_close = float(bar["close"])
                b_atr = float(bar["atr_14"])

                if is_fund_bar:
                    f_rate = self.get_funding_rate(sym, curr_ts)
                    fund_cf = -pos["direction"] * (pos["current_qty"] * b_close) * f_rate
                    pos["funding_cashflow"] += fund_cf
                    equity += fund_cf

                direction = pos["direction"]
                entry_p = pos["entry_price"]
                r_dist = pos["r_distance"]

                # Current MFE in R
                if direction == 1:
                    mfe_r = (b_high - entry_p) / r_dist if r_dist > 0 else 0.0
                else:
                    mfe_r = (entry_p - b_low) / r_dist if r_dist > 0 else 0.0

                # Stage 1: at +0.85R, move stop to -0.35R
                if pos["stage"] == 0 and mfe_r >= 0.85:
                    pos["stage"] = 1
                    pos["current_stop"] = entry_p - 0.35 * r_dist if direction == 1 else entry_p + 0.35 * r_dist

                # Stage 2: at +1.40R, try 60% partial close via Limit Maker at +1.40R
                if pos["stage"] <= 1 and mfe_r >= 1.40 and not pos["tp_maker_filled"]:
                    tp_limit_price = entry_p + 1.40 * r_dist if direction == 1 else entry_p - 1.40 * r_dist
                    # Check Maker limit fill condition
                    fill_maker = False
                    if direction == 1:
                        if exec_cfg.name == "OPT":
                            fill_maker = (b_high >= tp_limit_price)
                        else:
                            fill_maker = (b_high >= tp_limit_price + exec_cfg.maker_penetration_atr * b_atr)
                    else:
                        if exec_cfg.name == "OPT":
                            fill_maker = (b_low <= tp_limit_price)
                        else:
                            fill_maker = (b_low <= tp_limit_price - exec_cfg.maker_penetration_atr * b_atr)

                    if fill_maker and np.random.rand() >= exec_cfg.maker_failure_rate:
                        # Maker fill: 60% partial close
                        trim_qty = pos["initial_qty"] * 0.60
                        maker_fee = trim_qty * tp_limit_price * float(exec_cfg.fee_profile.maker_rate)
                        gross_trim_pnl = trim_qty * (tp_limit_price - entry_p) if direction == 1 else trim_qty * (entry_p - tp_limit_price)
                        pos["partial_pnl"] += gross_trim_pnl
                        pos["exit_fee"] += maker_fee
                        pos["current_qty"] -= trim_qty
                        pos["tp_maker_filled"] = True
                        pos["stage"] = 2
                        # Lock stop to +0.35R guaranteed profit
                        pos["current_stop"] = entry_p + 0.35 * r_dist if direction == 1 else entry_p - 0.35 * r_dist
                        equity += (gross_trim_pnl - maker_fee)

                # Stage 3: at +2.00R, Chandelier trailing stop
                if mfe_r >= 2.00:
                    pos["stage"] = 3
                    if direction == 1:
                        chand_stop = b_high - 2.5 * b_atr
                        if chand_stop > pos["current_stop"]:
                            pos["current_stop"] = chand_stop
                    else:
                        chand_stop = b_low + 2.5 * b_atr
                        if chand_stop < pos["current_stop"]:
                            pos["current_stop"] = chand_stop

                # Check Stop-Loss / Trailing Exit
                is_exit = False
                if direction == 1:
                    if b_low <= pos["current_stop"]:
                        is_exit = True
                        raw_exit = min(b_open, pos["current_stop"]) if b_open <= pos["current_stop"] else pos["current_stop"]
                        slip = raw_exit * (exec_cfg.slippage_bps / 10000.0)
                        if b_atr / b_close > 0.03:
                            slip *= exec_cfg.high_vol_slippage_mult
                        exit_price = raw_exit - slip
                else:
                    if b_high >= pos["current_stop"]:
                        is_exit = True
                        raw_exit = max(b_open, pos["current_stop"]) if b_open >= pos["current_stop"] else pos["current_stop"]
                        slip = raw_exit * (exec_cfg.slippage_bps / 10000.0)
                        if b_atr / b_close > 0.03:
                            slip *= exec_cfg.high_vol_slippage_mult
                        exit_price = raw_exit + slip

                if is_exit:
                    rem_qty = pos["current_qty"]
                    exit_fee = rem_qty * exit_price * float(exec_cfg.fee_profile.taker_rate)
                    gross_rem_pnl = rem_qty * (exit_price - entry_p) if direction == 1 else rem_qty * (entry_p - exit_price)
                    total_gross = pos["partial_pnl"] + gross_rem_pnl
                    total_exit_fee = pos["exit_fee"] + exit_fee
                    net_pnl = total_gross - pos["entry_fee"] - total_exit_fee + pos["funding_cashflow"]
                    equity += (gross_rem_pnl - exit_fee)

                    trade_counter += 1
                    hold_h = (curr_ts - pos["entry_ts"]) / 3600000.0
                    period_tag = "IS" if curr_ts < 1751241600000 else "OOS"
                    pnl_r = net_pnl / pos["risk_usd"] if pos["risk_usd"] > 0 else 0.0

                    closed_trades.append(GateTrade(
                        trade_id=f"v38_{trade_counter:04d}",
                        strategy="v3.8",
                        scenario=exec_cfg.name,
                        symbol=sym,
                        direction=pos["direction"],
                        entry_ts=pos["entry_ts"],
                        exit_ts=curr_ts,
                        entry_price=pos["entry_price"],
                        exit_price=exit_price,
                        initial_qty=pos["initial_qty"],
                        current_qty=pos["current_qty"],
                        entry_type="MARKET_TAKER",
                        exit_type="HYBRID_MAKER_TAKER" if pos["tp_maker_filled"] else "MARKET_TAKER",
                        gross_pnl=total_gross,
                        entry_fee=pos["entry_fee"],
                        exit_fee=total_exit_fee,
                        slippage_cost=pos["slippage_cost"] + slip * rem_qty,
                        spread_cost=pos["spread_cost"] + rem_qty * exit_price * (exec_cfg.half_spread_bps / 10000.0),
                        funding_cashflow=pos["funding_cashflow"],
                        net_pnl=net_pnl,
                        return_pct=(net_pnl / self.initial_capital) * 100.0,
                        pnl_r=pnl_r,
                        exit_reason="STOP_LOSS" if pos["stage"] < 2 else "TRAIL_OR_LOCKED",
                        was_pyramided=False,
                        hold_hours=hold_h,
                        capital_after=equity,
                        initial_atr=pos["initial_atr"],
                        period=period_tag,
                        regime=regime,
                    ))
                    del open_pos[sym]

            # 2. Check Signals on current 1h bar close
            if len(open_pos) < 3:
                candidates = []
                for sym in ALL_SYMBOLS:
                    if sym in open_pos:
                        continue
                    if not self.is_symbol_active(sym, curr_ts) or not self.is_symbol_active(sym, next_ts):
                        continue
                    df_1h = self.dfs_1h[sym]
                    if curr_ts not in df_1h.index or next_ts not in df_1h.index:
                        continue
                    bar = df_1h.loc[curr_ts]
                    c = float(bar["close"])
                    upper = float(bar["donch_high_14"])
                    lower = float(bar["donch_low_14"])
                    adx = float(bar["adx_14"])
                    ema50 = float(bar["ema_50"])
                    ema200 = float(bar["ema_200"])
                    atr = float(bar["atr_14"])
                    norm_atr = float(bar["norm_atr"])

                    if adx < 16.0 or atr <= 0:
                        continue

                    if c > upper and ema50 > ema200:
                        score = norm_atr * adx
                        candidates.append((score, sym, 1, atr))
                    elif c < lower and ema50 < ema200:
                        score = norm_atr * adx
                        candidates.append((score, sym, -1, atr))

                candidates.sort(key=lambda x: x[0], reverse=True)
                for score, sym, direction, atr in candidates:
                    if len(open_pos) >= 3:
                        break
                    next_bar = self.dfs_1h[sym].loc[next_ts]
                    open_p = float(next_bar["open"])
                    rules = DEFAULT_RULES.get(sym, {"minQty": 0.01, "step": 0.01, "minNotional": 5.0})

                    # True 1R = 0.8% of $300 = $2.40
                    risk_usd = 2.40
                    stop_dist = 2.5 * atr
                    if stop_dist <= 0:
                        continue
                    raw_qty = risk_usd / stop_dist
                    max_qty = (self.initial_capital * 3.5) / open_p
                    qty = min(raw_qty, max_qty)
                    step = rules["step"]
                    qty = math.floor(qty / step) * step
                    if qty < rules["minQty"] or (qty * open_p) < rules["minNotional"]:
                        continue

                    slip = open_p * (exec_cfg.slippage_bps / 10000.0)
                    eff_entry_p = open_p + slip if direction == 1 else open_p - slip
                    entry_fee = qty * eff_entry_p * float(exec_cfg.fee_profile.taker_rate)
                    spread_c = qty * eff_entry_p * (exec_cfg.half_spread_bps / 10000.0)
                    init_stop = eff_entry_p - stop_dist if direction == 1 else eff_entry_p + stop_dist

                    open_pos[sym] = {
                        "direction": direction,
                        "entry_ts": next_ts,
                        "entry_price": eff_entry_p,
                        "initial_qty": qty,
                        "current_qty": qty,
                        "initial_atr": atr,
                        "r_distance": stop_dist,
                        "current_stop": init_stop,
                        "entry_fee": entry_fee,
                        "exit_fee": 0.0,
                        "slippage_cost": abs(eff_entry_p - open_p) * qty,
                        "spread_cost": spread_c,
                        "funding_cashflow": 0.0,
                        "stage": 0,
                        "tp_maker_filled": False,
                        "partial_pnl": 0.0,
                        "risk_usd": risk_usd,
                    }
                    equity -= entry_fee

            equity_records.append((curr_ts, equity))

        return closed_trades, equity_records

    # --------------------------------------------------------------------------
    # STRATEGY 3: v3.2 (Dual-Regime IRAS: 1h Trend + IRAS Range Maker)
    # --------------------------------------------------------------------------
    def run_v32(self, exec_cfg: ExecutionConfig) -> Tuple[List[GateTrade], List[Tuple[int, float]]]:
        equity = self.initial_capital
        open_pos: Dict[str, Dict[str, Any]] = {}
        pending_limits: Dict[str, Dict[str, Any]] = {}
        closed_trades: List[GateTrade] = []
        equity_records: List[Tuple[int, float]] = []
        trade_counter = 0

        for i in range(len(self.sorted_ts_1h) - 1):
            curr_ts = self.sorted_ts_1h[i]
            next_ts = self.sorted_ts_1h[i + 1]
            regime = self.get_market_regime(curr_ts)

            dt_curr = pd.to_datetime(curr_ts, unit="ms", utc=True)
            is_fund_bar = (dt_curr.hour % 8 == 0)

            # 1. Manage Active Positions on current bar
            for sym in list(open_pos.keys()):
                pos = open_pos[sym]
                df_1h = self.dfs_1h[sym]
                if curr_ts not in df_1h.index:
                    continue
                bar = df_1h.loc[curr_ts]
                b_high = float(bar["high"])
                b_low = float(bar["low"])
                b_open = float(bar["open"])
                b_close = float(bar["close"])
                b_atr = float(bar["atr_14"])
                b_adx = float(bar["adx_14"])

                if is_fund_bar:
                    f_rate = self.get_funding_rate(sym, curr_ts)
                    fund_cf = -pos["direction"] * (pos["current_qty"] * b_close) * f_rate
                    pos["funding_cashflow"] += fund_cf
                    equity += fund_cf

                direction = pos["direction"]
                entry_p = pos["entry_price"]
                qty = pos["current_qty"]
                is_exit = False
                exit_price = 0.0
                exit_reason = ""
                exit_type = "MARKET_TAKER"

                if pos["module"] == "TREND":
                    # Trend trailing stop
                    if direction == 1:
                        new_stop = b_high - 2.5 * b_atr
                        if new_stop > pos["current_stop"]:
                            pos["current_stop"] = new_stop
                        if b_low <= pos["current_stop"]:
                            is_exit = True
                            raw_exit = min(b_open, pos["current_stop"]) if b_open <= pos["current_stop"] else pos["current_stop"]
                            slip = raw_exit * (exec_cfg.slippage_bps / 10000.0)
                            exit_price = raw_exit - slip
                            exit_reason = "STOP_LOSS"
                    else:
                        new_stop = b_low + 2.5 * b_atr
                        if new_stop < pos["current_stop"]:
                            pos["current_stop"] = new_stop
                        if b_high >= pos["current_stop"]:
                            is_exit = True
                            raw_exit = max(b_open, pos["current_stop"]) if b_open >= pos["current_stop"] else pos["current_stop"]
                            slip = raw_exit * (exec_cfg.slippage_bps / 10000.0)
                            exit_price = raw_exit + slip
                            exit_reason = "STOP_LOSS"

                else: # Range Mean-Reversion Module (IRAS)
                    # Regime handoff check: If breakout occurs, exit immediately
                    if b_adx >= 20.0:
                        is_exit = True
                        slip = b_open * (exec_cfg.slippage_bps / 10000.0)
                        exit_price = b_open - slip if direction == 1 else b_open + slip
                        exit_reason = "REGIME_HANDOFF"
                    else:
                        # Check SL (Taker) vs TP (Maker)
                        tp_p = pos["target_tp"]
                        sl_p = pos["current_stop"]

                        # Check collision (worst case: SL triggers first)
                        sl_touched = (b_low <= sl_p) if direction == 1 else (b_high >= sl_p)
                        tp_touched = (b_high >= tp_p) if direction == 1 else (b_low <= tp_p)

                        if sl_touched:
                            is_exit = True
                            raw_exit = min(b_open, sl_p) if direction == 1 and b_open <= sl_p else sl_p
                            slip = raw_exit * (exec_cfg.slippage_bps / 10000.0)
                            exit_price = raw_exit - slip if direction == 1 else raw_exit + slip
                            exit_reason = "STOP_LOSS"
                        elif tp_touched:
                            # Maker limit fill check
                            fill_maker = False
                            if direction == 1:
                                if exec_cfg.name == "OPT":
                                    fill_maker = (b_high >= tp_p)
                                else:
                                    fill_maker = (b_high >= tp_p + exec_cfg.maker_penetration_atr * b_atr)
                            else:
                                if exec_cfg.name == "OPT":
                                    fill_maker = (b_low <= tp_p)
                                else:
                                    fill_maker = (b_low <= tp_p - exec_cfg.maker_penetration_atr * b_atr)

                            if fill_maker and np.random.rand() >= exec_cfg.maker_failure_rate:
                                is_exit = True
                                exit_price = tp_p
                                exit_reason = "TAKE_PROFIT"
                                exit_type = "LIMIT_MAKER"

                if is_exit:
                    fee_rate = float(exec_cfg.fee_profile.maker_rate) if exit_type == "LIMIT_MAKER" else float(exec_cfg.fee_profile.taker_rate)
                    exit_fee = qty * exit_price * fee_rate
                    gross_pnl = qty * (exit_price - entry_p) if direction == 1 else qty * (entry_p - exit_price)
                    net_pnl = gross_pnl - pos["entry_fee"] - exit_fee + pos["funding_cashflow"]
                    equity += (gross_pnl - exit_fee)

                    trade_counter += 1
                    hold_h = (curr_ts - pos["entry_ts"]) / 3600000.0
                    period_tag = "IS" if curr_ts < 1751241600000 else "OOS"
                    pnl_r = net_pnl / pos["risk_usd"] if pos["risk_usd"] > 0 else 0.0

                    closed_trades.append(GateTrade(
                        trade_id=f"v32_{trade_counter:04d}",
                        strategy="v3.2",
                        scenario=exec_cfg.name,
                        symbol=sym,
                        direction=pos["direction"],
                        entry_ts=pos["entry_ts"],
                        exit_ts=curr_ts,
                        entry_price=pos["entry_price"],
                        exit_price=exit_price,
                        initial_qty=pos["initial_qty"],
                        current_qty=pos["current_qty"],
                        entry_type=pos["entry_type"],
                        exit_type=exit_type,
                        gross_pnl=gross_pnl,
                        entry_fee=pos["entry_fee"],
                        exit_fee=exit_fee,
                        slippage_cost=pos["slippage_cost"],
                        spread_cost=pos["spread_cost"],
                        funding_cashflow=pos["funding_cashflow"],
                        net_pnl=net_pnl,
                        return_pct=(net_pnl / self.initial_capital) * 100.0,
                        pnl_r=pnl_r,
                        exit_reason=exit_reason,
                        was_pyramided=False,
                        hold_hours=hold_h,
                        capital_after=equity,
                        initial_atr=pos["initial_atr"],
                        period=period_tag,
                        regime=regime,
                    ))
                    del open_pos[sym]

            # 2. Process Pending Limit Orders from prior bar on current bar Open
            for sym, pend in list(pending_limits.items()):
                if sym in open_pos or len(open_pos) >= 4:
                    continue
                df_1h = self.dfs_1h[sym]
                if curr_ts not in df_1h.index:
                    continue
                bar = df_1h.loc[curr_ts]
                b_low = float(bar["low"])
                b_high = float(bar["high"])
                b_open = float(bar["open"])
                b_atr = float(bar["atr_14"])

                p_limit = pend["price"]
                direction = pend["direction"]
                filled = False

                if direction == 1: # Buy Limit
                    if exec_cfg.name == "OPT":
                        filled = (b_low <= p_limit)
                    else:
                        filled = (b_open >= p_limit and b_low <= p_limit - exec_cfg.maker_penetration_atr * b_atr)
                else: # Sell Limit
                    if exec_cfg.name == "OPT":
                        filled = (b_high >= p_limit)
                    else:
                        filled = (b_open <= p_limit and b_high >= p_limit + exec_cfg.maker_penetration_atr * b_atr)

                if filled and np.random.rand() >= exec_cfg.maker_failure_rate:
                    # Limit Maker fill
                    qty = pend["qty"]
                    maker_fee = qty * p_limit * float(exec_cfg.fee_profile.maker_rate)
                    open_pos[sym] = {
                        "direction": direction,
                        "entry_ts": curr_ts,
                        "entry_price": p_limit,
                        "initial_qty": qty,
                        "current_qty": qty,
                        "initial_atr": b_atr,
                        "current_stop": pend["stop_loss"],
                        "target_tp": pend["target_tp"],
                        "entry_fee": maker_fee,
                        "slippage_cost": 0.0,
                        "spread_cost": 0.0,
                        "funding_cashflow": 0.0,
                        "entry_type": "LIMIT_MAKER",
                        "module": "RANGE",
                        "risk_usd": pend["risk_usd"],
                    }
                    equity -= maker_fee

            # Clear pending limits (orders expire if not filled in 1 bar)
            pending_limits.clear()

            # 3. Generate Signals on current bar close
            if len(open_pos) < 4:
                for sym in ALL_SYMBOLS:
                    if sym in open_pos or len(open_pos) >= 4:
                        continue
                    if not self.is_symbol_active(sym, curr_ts) or not self.is_symbol_active(sym, next_ts):
                        continue
                    df_1h = self.dfs_1h[sym]
                    if curr_ts not in df_1h.index or next_ts not in df_1h.index:
                        continue
                    bar = df_1h.loc[curr_ts]
                    c = float(bar["close"])
                    upper = float(bar["donch_high_120"])
                    lower = float(bar["donch_low_120"])
                    adx = float(bar["adx_14"])
                    ker = float(bar["ker_14"])
                    bw = float(bar["bb_bandwidth"])
                    bb_up = float(bar["bb_upper"])
                    bb_low = float(bar["bb_lower"])
                    bb_mid = float(bar["bb_mid"])
                    atr = float(bar["atr_14"])
                    vol = float(bar["volume"])
                    vol_sma = float(bar["vol_sma_20"])

                    # Regime A: TREND (ADX >= 20 and Vol > Vol_SMA)
                    if adx >= 20.0 and vol > vol_sma:
                        if c > upper: # Long breakout
                            next_bar = self.dfs_1h[sym].loc[next_ts]
                            open_p = float(next_bar["open"])
                            rules = DEFAULT_RULES.get(sym, {"minQty": 0.01, "step": 0.01, "minNotional": 5.0})
                            risk_usd = 3.00
                            stop_dist = 2.5 * atr
                            if stop_dist <= 0:
                                continue
                            qty = min(risk_usd / stop_dist, (self.initial_capital * 3.5) / open_p)
                            qty = math.floor(qty / rules["step"]) * rules["step"]
                            if qty >= rules["minQty"] and (qty * open_p) >= rules["minNotional"]:
                                slip = open_p * (exec_cfg.slippage_bps / 10000.0)
                                eff_entry_p = open_p + slip
                                entry_fee = qty * eff_entry_p * float(exec_cfg.fee_profile.taker_rate)
                                open_pos[sym] = {
                                    "direction": 1,
                                    "entry_ts": next_ts,
                                    "entry_price": eff_entry_p,
                                    "initial_qty": qty,
                                    "current_qty": qty,
                                    "initial_atr": atr,
                                    "current_stop": eff_entry_p - stop_dist,
                                    "entry_fee": entry_fee,
                                    "slippage_cost": abs(eff_entry_p - open_p) * qty,
                                    "spread_cost": qty * eff_entry_p * (exec_cfg.half_spread_bps / 10000.0),
                                    "funding_cashflow": 0.0,
                                    "entry_type": "MARKET_TAKER",
                                    "module": "TREND",
                                    "risk_usd": risk_usd,
                                }
                                equity -= entry_fee

                        elif c < lower: # Short breakout
                            next_bar = self.dfs_1h[sym].loc[next_ts]
                            open_p = float(next_bar["open"])
                            rules = DEFAULT_RULES.get(sym, {"minQty": 0.01, "step": 0.01, "minNotional": 5.0})
                            risk_usd = 3.00
                            stop_dist = 2.5 * atr
                            if stop_dist <= 0:
                                continue
                            qty = min(risk_usd / stop_dist, (self.initial_capital * 3.5) / open_p)
                            qty = math.floor(qty / rules["step"]) * rules["step"]
                            if qty >= rules["minQty"] and (qty * open_p) >= rules["minNotional"]:
                                slip = open_p * (exec_cfg.slippage_bps / 10000.0)
                                eff_entry_p = open_p - slip
                                entry_fee = qty * eff_entry_p * float(exec_cfg.fee_profile.taker_rate)
                                open_pos[sym] = {
                                    "direction": -1,
                                    "entry_ts": next_ts,
                                    "entry_price": eff_entry_p,
                                    "initial_qty": qty,
                                    "current_qty": qty,
                                    "initial_atr": atr,
                                    "current_stop": eff_entry_p + stop_dist,
                                    "entry_fee": entry_fee,
                                    "slippage_cost": abs(eff_entry_p - open_p) * qty,
                                    "spread_cost": qty * eff_entry_p * (exec_cfg.half_spread_bps / 10000.0),
                                    "funding_cashflow": 0.0,
                                    "entry_type": "MARKET_TAKER",
                                    "module": "TREND",
                                    "risk_usd": risk_usd,
                                }
                                equity -= entry_fee

                    # Regime B: CHOP / RANGE (IRAS Maker)
                    elif adx < 20.0 and ker <= 0.25 and bw >= 0.025:
                        rules = DEFAULT_RULES.get(sym, {"minQty": 0.01, "step": 0.01, "minNotional": 5.0})
                        risk_usd = 3.00
                        stop_dist = 1.0 * atr
                        if stop_dist <= 0:
                            continue

                        # Setup Limit Buy at BB Lower
                        qty_buy = min(risk_usd / stop_dist, (self.initial_capital * 3.5) / bb_low)
                        qty_buy = math.floor(qty_buy / rules["step"]) * rules["step"]
                        if qty_buy >= rules["minQty"] and (qty_buy * bb_low) >= rules["minNotional"]:
                            pending_limits[sym] = {
                                "direction": 1,
                                "price": bb_low,
                                "qty": qty_buy,
                                "stop_loss": bb_low - stop_dist,
                                "target_tp": bb_mid,
                                "risk_usd": risk_usd,
                            }

            equity_records.append((curr_ts, equity))

        return closed_trades, equity_records


    # --------------------------------------------------------------------------
    # STRATEGY 4: v3.0 (Dynamic Momentum 1h Screener)
    # --------------------------------------------------------------------------
    def run_v30(self, exec_cfg: ExecutionConfig) -> Tuple[List[GateTrade], List[Tuple[int, float]]]:
        equity = self.initial_capital
        open_pos: Dict[str, Dict[str, Any]] = {}
        closed_trades: List[GateTrade] = []
        equity_records: List[Tuple[int, float]] = []
        trade_counter = 0

        for i in range(len(self.sorted_ts_1h) - 1):
            curr_ts = self.sorted_ts_1h[i]
            next_ts = self.sorted_ts_1h[i + 1]
            regime = self.get_market_regime(curr_ts)

            dt_curr = pd.to_datetime(curr_ts, unit="ms", utc=True)
            is_fund_bar = (dt_curr.hour % 8 == 0)

            # 1. Manage Active Positions on current bar
            for sym in list(open_pos.keys()):
                pos = open_pos[sym]
                df_1h = self.dfs_1h[sym]
                if curr_ts not in df_1h.index:
                    continue
                bar = df_1h.loc[curr_ts]
                b_high = float(bar["high"])
                b_low = float(bar["low"])
                b_open = float(bar["open"])
                b_close = float(bar["close"])
                b_atr = float(bar["atr_14"])

                if is_fund_bar:
                    f_rate = self.get_funding_rate(sym, curr_ts)
                    fund_cf = -pos["direction"] * (pos["current_qty"] * b_close) * f_rate
                    pos["funding_cashflow"] += fund_cf
                    equity += fund_cf

                direction = pos["direction"]

                if direction == 1:
                    new_stop = b_high - 2.5 * b_atr
                    if new_stop > pos["current_stop"]:
                        pos["current_stop"] = new_stop
                    if not pos["is_pyramided"] and b_high >= pos["entry_price"] + 2.0 * pos["initial_atr"]:
                        add_qty = pos["initial_qty"] * 0.5
                        add_price = pos["entry_price"] + 2.0 * pos["initial_atr"]
                        add_slip = add_price * (exec_cfg.slippage_bps / 10000.0)
                        eff_add_price = add_price + add_slip
                        add_fee = add_qty * eff_add_price * float(exec_cfg.fee_profile.taker_rate)
                        pos["entry_fee"] += add_fee
                        pos["slippage_cost"] += (eff_add_price - add_price) * add_qty
                        total_qty = pos["current_qty"] + add_qty
                        pos["entry_price"] = (pos["current_qty"] * pos["entry_price"] + add_qty * eff_add_price) / total_qty
                        pos["current_qty"] = total_qty
                        pos["is_pyramided"] = True
                        pos["current_stop"] = pos["entry_price"]
                        equity -= add_fee

                    if b_low <= pos["current_stop"]:
                        raw_exit = min(b_open, pos["current_stop"]) if b_open <= pos["current_stop"] else pos["current_stop"]
                        slip = raw_exit * (exec_cfg.slippage_bps / 10000.0)
                        if b_atr / b_close > 0.03:
                            slip *= exec_cfg.high_vol_slippage_mult
                        exit_price = raw_exit - slip
                        exit_fee = pos["current_qty"] * exit_price * float(exec_cfg.fee_profile.taker_rate)
                        gross_pnl = pos["current_qty"] * (exit_price - pos["entry_price"])
                        net_pnl = gross_pnl - pos["entry_fee"] - exit_fee + pos["funding_cashflow"]
                        equity += (gross_pnl - exit_fee)

                        trade_counter += 1
                        hold_h = (curr_ts - pos["entry_ts"]) / 3600000.0
                        period_tag = "IS" if curr_ts < 1751241600000 else "OOS"
                        pnl_r = net_pnl / pos["risk_usd"] if pos["risk_usd"] > 0 else 0.0

                        closed_trades.append(GateTrade(
                            trade_id=f"v30_{trade_counter:04d}",
                            strategy="v3.0",
                            scenario=exec_cfg.name,
                            symbol=sym,
                            direction=pos["direction"],
                            entry_ts=pos["entry_ts"],
                            exit_ts=curr_ts,
                            entry_price=pos["entry_price"],
                            exit_price=exit_price,
                            initial_qty=pos["initial_qty"],
                            current_qty=pos["current_qty"],
                            entry_type="MARKET_TAKER",
                            exit_type="MARKET_TAKER",
                            gross_pnl=gross_pnl,
                            entry_fee=pos["entry_fee"],
                            exit_fee=exit_fee,
                            slippage_cost=pos["slippage_cost"] + slip * pos["current_qty"],
                            spread_cost=pos["spread_cost"] + pos["current_qty"] * exit_price * (exec_cfg.half_spread_bps / 10000.0),
                            funding_cashflow=pos["funding_cashflow"],
                            net_pnl=net_pnl,
                            return_pct=(net_pnl / self.initial_capital) * 100.0,
                            pnl_r=pnl_r,
                            exit_reason="STOP_LOSS",
                            was_pyramided=pos["is_pyramided"],
                            hold_hours=hold_h,
                            capital_after=equity,
                            initial_atr=pos["initial_atr"],
                            period=period_tag,
                            regime=regime,
                        ))
                        del open_pos[sym]

                else: # Short direction
                    new_stop = b_low + 2.5 * b_atr
                    if new_stop < pos["current_stop"]:
                        pos["current_stop"] = new_stop
                    if not pos["is_pyramided"] and b_low <= pos["entry_price"] - 2.0 * pos["initial_atr"]:
                        add_qty = pos["initial_qty"] * 0.5
                        add_price = pos["entry_price"] - 2.0 * pos["initial_atr"]
                        add_slip = add_price * (exec_cfg.slippage_bps / 10000.0)
                        eff_add_price = add_price - add_slip
                        add_fee = add_qty * eff_add_price * float(exec_cfg.fee_profile.taker_rate)
                        pos["entry_fee"] += add_fee
                        pos["slippage_cost"] += (add_price - eff_add_price) * add_qty
                        total_qty = pos["current_qty"] + add_qty
                        pos["entry_price"] = (pos["current_qty"] * pos["entry_price"] + add_qty * eff_add_price) / total_qty
                        pos["current_qty"] = total_qty
                        pos["is_pyramided"] = True
                        pos["current_stop"] = pos["entry_price"]
                        equity -= add_fee

                    if b_high >= pos["current_stop"]:
                        raw_exit = max(b_open, pos["current_stop"]) if b_open >= pos["current_stop"] else pos["current_stop"]
                        slip = raw_exit * (exec_cfg.slippage_bps / 10000.0)
                        if b_atr / b_close > 0.03:
                            slip *= exec_cfg.high_vol_slippage_mult
                        exit_price = raw_exit + slip
                        exit_fee = pos["current_qty"] * exit_price * float(exec_cfg.fee_profile.taker_rate)
                        gross_pnl = pos["current_qty"] * (pos["entry_price"] - exit_price)
                        net_pnl = gross_pnl - pos["entry_fee"] - exit_fee + pos["funding_cashflow"]
                        equity += (gross_pnl - exit_fee)

                        trade_counter += 1
                        hold_h = (curr_ts - pos["entry_ts"]) / 3600000.0
                        period_tag = "IS" if curr_ts < 1751241600000 else "OOS"
                        pnl_r = net_pnl / pos["risk_usd"] if pos["risk_usd"] > 0 else 0.0

                        closed_trades.append(GateTrade(
                            trade_id=f"v30_{trade_counter:04d}",
                            strategy="v3.0",
                            scenario=exec_cfg.name,
                            symbol=sym,
                            direction=pos["direction"],
                            entry_ts=pos["entry_ts"],
                            exit_ts=curr_ts,
                            entry_price=pos["entry_price"],
                            exit_price=exit_price,
                            initial_qty=pos["initial_qty"],
                            current_qty=pos["current_qty"],
                            entry_type="MARKET_TAKER",
                            exit_type="MARKET_TAKER",
                            gross_pnl=gross_pnl,
                            entry_fee=pos["entry_fee"],
                            exit_fee=exit_fee,
                            slippage_cost=pos["slippage_cost"] + slip * pos["current_qty"],
                            spread_cost=pos["spread_cost"] + pos["current_qty"] * exit_price * (exec_cfg.half_spread_bps / 10000.0),
                            funding_cashflow=pos["funding_cashflow"],
                            net_pnl=net_pnl,
                            return_pct=(net_pnl / self.initial_capital) * 100.0,
                            pnl_r=pnl_r,
                            exit_reason="STOP_LOSS",
                            was_pyramided=pos["is_pyramided"],
                            hold_hours=hold_h,
                            capital_after=equity,
                            initial_atr=pos["initial_atr"],
                            period=period_tag,
                            regime=regime,
                        ))
                        del open_pos[sym]

            # 2. Check Signals on current 1h bar close (Dynamic Screener)
            if len(open_pos) < 4:
                candidates = []
                for sym in ALL_SYMBOLS:
                    if sym in open_pos:
                        continue
                    if not self.is_symbol_active(sym, curr_ts) or not self.is_symbol_active(sym, next_ts):
                        continue
                    df_1h = self.dfs_1h[sym]
                    if curr_ts not in df_1h.index or next_ts not in df_1h.index:
                        continue
                    bar = df_1h.loc[curr_ts]
                    c = float(bar["close"])
                    upper = float(bar["donch_high_120"])
                    lower = float(bar["donch_low_120"])
                    adx = float(bar["adx_14"])
                    ema50 = float(bar["ema_50"])
                    ema200 = float(bar["ema_200"])
                    atr = float(bar["atr_14"])
                    norm_atr = float(bar["norm_atr"])
                    vol = float(bar["volume"])
                    vol_sma = float(bar["vol_sma_20"])
                    vol_ratio = vol / vol_sma if vol_sma > 0 else 1.0

                    if adx < 18.0 or atr <= 0:
                        continue

                    # Dynamic score: Norm_ATR * ADX * Volume_Ratio
                    score = norm_atr * adx * min(vol_ratio, 3.0)

                    if c > upper and ema50 > ema200:
                        candidates.append((score, sym, 1, atr))
                    elif c < lower and ema50 < ema200:
                        candidates.append((score, sym, -1, atr))

                candidates.sort(key=lambda x: x[0], reverse=True)
                for score, sym, direction, atr in candidates:
                    if len(open_pos) >= 4:
                        break
                    next_bar = self.dfs_1h[sym].loc[next_ts]
                    open_p = float(next_bar["open"])
                    rules = DEFAULT_RULES.get(sym, {"minQty": 0.01, "step": 0.01, "minNotional": 5.0})

                    risk_usd = 3.00
                    stop_dist = 2.5 * atr
                    if stop_dist <= 0:
                        continue
                    raw_qty = risk_usd / stop_dist
                    max_qty = (self.initial_capital * 3.5) / open_p
                    qty = min(raw_qty, max_qty)
                    step = rules["step"]
                    qty = math.floor(qty / step) * step
                    if qty < rules["minQty"] or (qty * open_p) < rules["minNotional"]:
                        continue

                    slip = open_p * (exec_cfg.slippage_bps / 10000.0)
                    eff_entry_p = open_p + slip if direction == 1 else open_p - slip
                    entry_fee = qty * eff_entry_p * float(exec_cfg.fee_profile.taker_rate)
                    spread_c = qty * eff_entry_p * (exec_cfg.half_spread_bps / 10000.0)
                    init_stop = eff_entry_p - stop_dist if direction == 1 else eff_entry_p + stop_dist

                    open_pos[sym] = {
                        "direction": direction,
                        "entry_ts": next_ts,
                        "entry_price": eff_entry_p,
                        "initial_qty": qty,
                        "current_qty": qty,
                        "initial_atr": atr,
                        "current_stop": init_stop,
                        "entry_fee": entry_fee,
                        "slippage_cost": abs(eff_entry_p - open_p) * qty,
                        "spread_cost": spread_c,
                        "funding_cashflow": 0.0,
                        "is_pyramided": False,
                        "risk_usd": risk_usd,
                    }
                    equity -= entry_fee

            equity_records.append((curr_ts, equity))

        return closed_trades, equity_records

    # --------------------------------------------------------------------------
    # STRATEGY 5: v3.6 (Omega ZF-8 Quant: Transmission + IRAS)
    # --------------------------------------------------------------------------
    def run_v36(self, exec_cfg: ExecutionConfig) -> Tuple[List[GateTrade], List[Tuple[int, float]]]:
        equity = self.initial_capital
        open_pos: Dict[str, Dict[str, Any]] = {}
        pending_limits: Dict[str, Dict[str, Any]] = {}
        closed_trades: List[GateTrade] = []
        equity_records: List[Tuple[int, float]] = []
        trade_counter = 0

        # Gear state
        current_gear = 2 # Start in Gear 2 (1.3%)
        gear_risks = {
            1: 0.010, 2: 0.013, 3: 0.016, 4: 0.020,
            5: 0.024, 6: 0.028, 7: 0.032, 8: 0.036
        }
        peak_equity = equity

        for i in range(len(self.sorted_ts_1h) - 1):
            curr_ts = self.sorted_ts_1h[i]
            next_ts = self.sorted_ts_1h[i + 1]
            regime = self.get_market_regime(curr_ts)

            if equity > peak_equity:
                peak_equity = equity
            dd_pct = ((peak_equity - equity) / peak_equity) * 100.0 if peak_equity > 0 else 0.0

            # Circuit breaker: If DD >= 8.0%, lock in Gear 1 (Limp Mode)
            if dd_pct >= 8.0:
                current_gear = 1

            dt_curr = pd.to_datetime(curr_ts, unit="ms", utc=True)
            is_fund_bar = (dt_curr.hour % 8 == 0)

            # 1. Manage Active Positions on current bar
            for sym in list(open_pos.keys()):
                pos = open_pos[sym]
                df_1h = self.dfs_1h[sym]
                if curr_ts not in df_1h.index:
                    continue
                bar = df_1h.loc[curr_ts]
                b_high = float(bar["high"])
                b_low = float(bar["low"])
                b_open = float(bar["open"])
                b_close = float(bar["close"])
                b_atr = float(bar["atr_14"])
                b_adx = float(bar["adx_14"])

                if is_fund_bar:
                    f_rate = self.get_funding_rate(sym, curr_ts)
                    fund_cf = -pos["direction"] * (pos["current_qty"] * b_close) * f_rate
                    pos["funding_cashflow"] += fund_cf
                    equity += fund_cf

                direction = pos["direction"]
                entry_p = pos["entry_price"]
                qty = pos["current_qty"]
                is_exit = False
                exit_price = 0.0
                exit_reason = ""
                exit_type = "MARKET_TAKER"

                if pos["module"] == "TREND":
                    # Trend trailing stop
                    if direction == 1:
                        new_stop = b_high - 2.5 * b_atr
                        if new_stop > pos["current_stop"]:
                            pos["current_stop"] = new_stop
                        if b_low <= pos["current_stop"]:
                            is_exit = True
                            raw_exit = min(b_open, pos["current_stop"]) if b_open <= pos["current_stop"] else pos["current_stop"]
                            slip = raw_exit * (exec_cfg.slippage_bps / 10000.0)
                            exit_price = raw_exit - slip
                            exit_reason = "STOP_LOSS"
                    else:
                        new_stop = b_low + 2.5 * b_atr
                        if new_stop < pos["current_stop"]:
                            pos["current_stop"] = new_stop
                        if b_high >= pos["current_stop"]:
                            is_exit = True
                            raw_exit = max(b_open, pos["current_stop"]) if b_open >= pos["current_stop"] else pos["current_stop"]
                            slip = raw_exit * (exec_cfg.slippage_bps / 10000.0)
                            exit_price = raw_exit + slip
                            exit_reason = "STOP_LOSS"

                else: # Range Mean-Reversion Module (IRAS)
                    if b_adx >= 20.0:
                        is_exit = True
                        slip = b_open * (exec_cfg.slippage_bps / 10000.0)
                        exit_price = b_open - slip if direction == 1 else b_open + slip
                        exit_reason = "REGIME_HANDOFF"
                    else:
                        tp_p = pos["target_tp"]
                        sl_p = pos["current_stop"]
                        sl_touched = (b_low <= sl_p) if direction == 1 else (b_high >= sl_p)
                        tp_touched = (b_high >= tp_p) if direction == 1 else (b_low <= tp_p)

                        if sl_touched:
                            is_exit = True
                            raw_exit = min(b_open, sl_p) if direction == 1 and b_open <= sl_p else sl_p
                            slip = raw_exit * (exec_cfg.slippage_bps / 10000.0)
                            exit_price = raw_exit - slip if direction == 1 else raw_exit + slip
                            exit_reason = "STOP_LOSS"
                        elif tp_touched:
                            fill_maker = False
                            if direction == 1:
                                fill_maker = (b_high >= tp_p) if exec_cfg.name == "OPT" else (b_high >= tp_p + exec_cfg.maker_penetration_atr * b_atr)
                            else:
                                fill_maker = (b_low <= tp_p) if exec_cfg.name == "OPT" else (b_low <= tp_p - exec_cfg.maker_penetration_atr * b_atr)

                            if fill_maker and np.random.rand() >= exec_cfg.maker_failure_rate:
                                is_exit = True
                                exit_price = tp_p
                                exit_reason = "TAKE_PROFIT"
                                exit_type = "LIMIT_MAKER"

                if is_exit:
                    fee_rate = float(exec_cfg.fee_profile.maker_rate) if exit_type == "LIMIT_MAKER" else float(exec_cfg.fee_profile.taker_rate)
                    exit_fee = qty * exit_price * fee_rate
                    gross_pnl = qty * (exit_price - entry_p) if direction == 1 else qty * (entry_p - exit_price)
                    net_pnl = gross_pnl - pos["entry_fee"] - exit_fee + pos["funding_cashflow"]
                    equity += (gross_pnl - exit_fee)

                    trade_counter += 1
                    hold_h = (curr_ts - pos["entry_ts"]) / 3600000.0
                    period_tag = "IS" if curr_ts < 1751241600000 else "OOS"
                    pnl_r = net_pnl / pos["risk_usd"] if pos["risk_usd"] > 0 else 0.0

                    # Transmission gear shift
                    if pnl_r >= 5.0: # Mega runner
                        current_gear = min(8, current_gear + 3)
                    elif pnl_r >= 3.2: # Super runner
                        current_gear = min(8, current_gear + 2)
                    elif pnl_r >= 0.3: # Standard win
                        current_gear = min(8, current_gear + 1)
                    elif pnl_r < -0.7: # Hard stop loss (ABS downshift)
                        current_gear = max(1, current_gear - 2)
                    elif pnl_r < -0.3: # Soft cut
                        current_gear = max(1, current_gear - 1)

                    closed_trades.append(GateTrade(
                        trade_id=f"v36_{trade_counter:04d}",
                        strategy="v3.6",
                        scenario=exec_cfg.name,
                        symbol=sym,
                        direction=pos["direction"],
                        entry_ts=pos["entry_ts"],
                        exit_ts=curr_ts,
                        entry_price=pos["entry_price"],
                        exit_price=exit_price,
                        initial_qty=pos["initial_qty"],
                        current_qty=pos["current_qty"],
                        entry_type=pos["entry_type"],
                        exit_type=exit_type,
                        gross_pnl=gross_pnl,
                        entry_fee=pos["entry_fee"],
                        exit_fee=exit_fee,
                        slippage_cost=pos["slippage_cost"],
                        spread_cost=pos["spread_cost"],
                        funding_cashflow=pos["funding_cashflow"],
                        net_pnl=net_pnl,
                        return_pct=(net_pnl / self.initial_capital) * 100.0,
                        pnl_r=pnl_r,
                        exit_reason=exit_reason,
                        was_pyramided=False,
                        hold_hours=hold_h,
                        capital_after=equity,
                        initial_atr=pos["initial_atr"],
                        period=period_tag,
                        regime=regime,
                    ))
                    del open_pos[sym]

            # 2. Process Pending Limit Orders
            for sym, pend in list(pending_limits.items()):
                if sym in open_pos or len(open_pos) >= 4:
                    continue
                df_1h = self.dfs_1h[sym]
                if curr_ts not in df_1h.index:
                    continue
                bar = df_1h.loc[curr_ts]
                b_low = float(bar["low"])
                b_high = float(bar["high"])
                b_open = float(bar["open"])
                b_atr = float(bar["atr_14"])
                p_limit = pend["price"]
                direction = pend["direction"]
                filled = False
                if direction == 1:
                    filled = (b_low <= p_limit) if exec_cfg.name == "OPT" else (b_open >= p_limit and b_low <= p_limit - exec_cfg.maker_penetration_atr * b_atr)
                else:
                    filled = (b_high >= p_limit) if exec_cfg.name == "OPT" else (b_open <= p_limit and b_high >= p_limit + exec_cfg.maker_penetration_atr * b_atr)

                if filled and np.random.rand() >= exec_cfg.maker_failure_rate:
                    qty = pend["qty"]
                    maker_fee = qty * p_limit * float(exec_cfg.fee_profile.maker_rate)
                    open_pos[sym] = {
                        "direction": direction,
                        "entry_ts": curr_ts,
                        "entry_price": p_limit,
                        "initial_qty": qty,
                        "current_qty": qty,
                        "initial_atr": b_atr,
                        "current_stop": pend["stop_loss"],
                        "target_tp": pend["target_tp"],
                        "entry_fee": maker_fee,
                        "slippage_cost": 0.0,
                        "spread_cost": 0.0,
                        "funding_cashflow": 0.0,
                        "entry_type": "LIMIT_MAKER",
                        "module": "RANGE",
                        "risk_usd": pend["risk_usd"],
                    }
                    equity -= maker_fee

            pending_limits.clear()

            # 3. Check Signals on current 1h bar close
            if len(open_pos) < 4:
                curr_risk_pct = gear_risks[current_gear]
                risk_usd = self.initial_capital * curr_risk_pct

                for sym in ALL_SYMBOLS:
                    if sym in open_pos or len(open_pos) >= 4:
                        continue
                    if not self.is_symbol_active(sym, curr_ts) or not self.is_symbol_active(sym, next_ts):
                        continue
                    df_1h = self.dfs_1h[sym]
                    if curr_ts not in df_1h.index or next_ts not in df_1h.index:
                        continue
                    bar = df_1h.loc[curr_ts]
                    c = float(bar["close"])
                    upper = float(bar["donch_high_120"])
                    lower = float(bar["donch_low_120"])
                    adx = float(bar["adx_14"])
                    ker = float(bar["ker_14"])
                    bw = float(bar["bb_bandwidth"])
                    bb_up = float(bar["bb_upper"])
                    bb_low = float(bar["bb_lower"])
                    bb_mid = float(bar["bb_mid"])
                    atr = float(bar["atr_14"])
                    vol = float(bar["volume"])
                    vol_sma = float(bar["vol_sma_20"])

                    if adx >= 20.0 and vol > vol_sma: # TREND
                        if c > upper:
                            next_bar = self.dfs_1h[sym].loc[next_ts]
                            open_p = float(next_bar["open"])
                            rules = DEFAULT_RULES.get(sym, {"minQty": 0.01, "step": 0.01, "minNotional": 5.0})
                            stop_dist = 2.5 * atr
                            if stop_dist <= 0:
                                continue
                            qty = min(risk_usd / stop_dist, (self.initial_capital * 3.5) / open_p)
                            qty = math.floor(qty / rules["step"]) * rules["step"]
                            if qty >= rules["minQty"] and (qty * open_p) >= rules["minNotional"]:
                                slip = open_p * (exec_cfg.slippage_bps / 10000.0)
                                eff_entry_p = open_p + slip
                                entry_fee = qty * eff_entry_p * float(exec_cfg.fee_profile.taker_rate)
                                open_pos[sym] = {
                                    "direction": 1,
                                    "entry_ts": next_ts,
                                    "entry_price": eff_entry_p,
                                    "initial_qty": qty,
                                    "current_qty": qty,
                                    "initial_atr": atr,
                                    "current_stop": eff_entry_p - stop_dist,
                                    "entry_fee": entry_fee,
                                    "slippage_cost": abs(eff_entry_p - open_p) * qty,
                                    "spread_cost": qty * eff_entry_p * (exec_cfg.half_spread_bps / 10000.0),
                                    "funding_cashflow": 0.0,
                                    "entry_type": "MARKET_TAKER",
                                    "module": "TREND",
                                    "risk_usd": risk_usd,
                                }
                                equity -= entry_fee

                        elif c < lower:
                            next_bar = self.dfs_1h[sym].loc[next_ts]
                            open_p = float(next_bar["open"])
                            rules = DEFAULT_RULES.get(sym, {"minQty": 0.01, "step": 0.01, "minNotional": 5.0})
                            stop_dist = 2.5 * atr
                            if stop_dist <= 0:
                                continue
                            qty = min(risk_usd / stop_dist, (self.initial_capital * 3.5) / open_p)
                            qty = math.floor(qty / rules["step"]) * rules["step"]
                            if qty >= rules["minQty"] and (qty * open_p) >= rules["minNotional"]:
                                slip = open_p * (exec_cfg.slippage_bps / 10000.0)
                                eff_entry_p = open_p - slip
                                entry_fee = qty * eff_entry_p * float(exec_cfg.fee_profile.taker_rate)
                                open_pos[sym] = {
                                    "direction": -1,
                                    "entry_ts": next_ts,
                                    "entry_price": eff_entry_p,
                                    "initial_qty": qty,
                                    "current_qty": qty,
                                    "initial_atr": atr,
                                    "current_stop": eff_entry_p + stop_dist,
                                    "entry_fee": entry_fee,
                                    "slippage_cost": abs(eff_entry_p - open_p) * qty,
                                    "spread_cost": qty * eff_entry_p * (exec_cfg.half_spread_bps / 10000.0),
                                    "funding_cashflow": 0.0,
                                    "entry_type": "MARKET_TAKER",
                                    "module": "TREND",
                                    "risk_usd": risk_usd,
                                }
                                equity -= entry_fee

                    elif adx < 20.0 and ker <= 0.25 and bw >= 0.025: # CHOP / RANGE
                        rules = DEFAULT_RULES.get(sym, {"minQty": 0.01, "step": 0.01, "minNotional": 5.0})
                        stop_dist = 1.0 * atr
                        if stop_dist <= 0:
                            continue
                        qty_buy = min(risk_usd / stop_dist, (self.initial_capital * 3.5) / bb_low)
                        qty_buy = math.floor(qty_buy / rules["step"]) * rules["step"]
                        if qty_buy >= rules["minQty"] and (qty_buy * bb_low) >= rules["minNotional"]:
                            pending_limits[sym] = {
                                "direction": 1,
                                "price": bb_low,
                                "qty": qty_buy,
                                "stop_loss": bb_low - stop_dist,
                                "target_tp": bb_mid,
                                "risk_usd": risk_usd,
                            }

            equity_records.append((curr_ts, equity))

        return closed_trades, equity_records

    # --------------------------------------------------------------------------
    # STRATEGY 6: v3.4 (ZF-8 Quant Control: Transmission without IRAS)
    # --------------------------------------------------------------------------
    def run_v34(self, exec_cfg: ExecutionConfig) -> Tuple[List[GateTrade], List[Tuple[int, float]]]:
        equity = self.initial_capital
        open_pos: Dict[str, Dict[str, Any]] = {}
        closed_trades: List[GateTrade] = []
        equity_records: List[Tuple[int, float]] = []
        trade_counter = 0

        current_gear = 2
        gear_risks = {
            1: 0.010, 2: 0.013, 3: 0.016, 4: 0.020,
            5: 0.024, 6: 0.028, 7: 0.032, 8: 0.036
        }
        peak_equity = equity

        for i in range(len(self.sorted_ts_1h) - 1):
            curr_ts = self.sorted_ts_1h[i]
            next_ts = self.sorted_ts_1h[i + 1]
            regime = self.get_market_regime(curr_ts)

            if equity > peak_equity:
                peak_equity = equity
            dd_pct = ((peak_equity - equity) / peak_equity) * 100.0 if peak_equity > 0 else 0.0

            if dd_pct >= 8.0:
                current_gear = 1

            dt_curr = pd.to_datetime(curr_ts, unit="ms", utc=True)
            is_fund_bar = (dt_curr.hour % 8 == 0)

            # 1. Manage Active Positions on current bar
            for sym in list(open_pos.keys()):
                pos = open_pos[sym]
                df_1h = self.dfs_1h[sym]
                if curr_ts not in df_1h.index:
                    continue
                bar = df_1h.loc[curr_ts]
                b_high = float(bar["high"])
                b_low = float(bar["low"])
                b_open = float(bar["open"])
                b_close = float(bar["close"])
                b_atr = float(bar["atr_14"])

                if is_fund_bar:
                    f_rate = self.get_funding_rate(sym, curr_ts)
                    fund_cf = -pos["direction"] * (pos["current_qty"] * b_close) * f_rate
                    pos["funding_cashflow"] += fund_cf
                    equity += fund_cf

                direction = pos["direction"]
                entry_p = pos["entry_price"]
                qty = pos["current_qty"]
                is_exit = False
                exit_price = 0.0

                if direction == 1:
                    new_stop = b_high - 2.5 * b_atr
                    if new_stop > pos["current_stop"]:
                        pos["current_stop"] = new_stop
                    if b_low <= pos["current_stop"]:
                        is_exit = True
                        raw_exit = min(b_open, pos["current_stop"]) if b_open <= pos["current_stop"] else pos["current_stop"]
                        slip = raw_exit * (exec_cfg.slippage_bps / 10000.0)
                        exit_price = raw_exit - slip
                else:
                    new_stop = b_low + 2.5 * b_atr
                    if new_stop < pos["current_stop"]:
                        pos["current_stop"] = new_stop
                    if b_high >= pos["current_stop"]:
                        is_exit = True
                        raw_exit = max(b_open, pos["current_stop"]) if b_open >= pos["current_stop"] else pos["current_stop"]
                        slip = raw_exit * (exec_cfg.slippage_bps / 10000.0)
                        exit_price = raw_exit + slip

                if is_exit:
                    exit_fee = qty * exit_price * float(exec_cfg.fee_profile.taker_rate)
                    gross_pnl = qty * (exit_price - entry_p) if direction == 1 else qty * (entry_p - exit_price)
                    net_pnl = gross_pnl - pos["entry_fee"] - exit_fee + pos["funding_cashflow"]
                    equity += (gross_pnl - exit_fee)

                    trade_counter += 1
                    hold_h = (curr_ts - pos["entry_ts"]) / 3600000.0
                    period_tag = "IS" if curr_ts < 1751241600000 else "OOS"
                    pnl_r = net_pnl / pos["risk_usd"] if pos["risk_usd"] > 0 else 0.0

                    if pnl_r >= 5.0:
                        current_gear = min(8, current_gear + 3)
                    elif pnl_r >= 3.2:
                        current_gear = min(8, current_gear + 2)
                    elif pnl_r >= 0.3:
                        current_gear = min(8, current_gear + 1)
                    elif pnl_r < -0.7:
                        current_gear = max(1, current_gear - 2)
                    elif pnl_r < -0.3:
                        current_gear = max(1, current_gear - 1)

                    closed_trades.append(GateTrade(
                        trade_id=f"v34_{trade_counter:04d}",
                        strategy="v3.4",
                        scenario=exec_cfg.name,
                        symbol=sym,
                        direction=pos["direction"],
                        entry_ts=pos["entry_ts"],
                        exit_ts=curr_ts,
                        entry_price=pos["entry_price"],
                        exit_price=exit_price,
                        initial_qty=pos["initial_qty"],
                        current_qty=pos["current_qty"],
                        entry_type="MARKET_TAKER",
                        exit_type="MARKET_TAKER",
                        gross_pnl=gross_pnl,
                        entry_fee=pos["entry_fee"],
                        exit_fee=exit_fee,
                        slippage_cost=pos["slippage_cost"],
                        spread_cost=pos["spread_cost"],
                        funding_cashflow=pos["funding_cashflow"],
                        net_pnl=net_pnl,
                        return_pct=(net_pnl / self.initial_capital) * 100.0,
                        pnl_r=pnl_r,
                        exit_reason="STOP_LOSS",
                        was_pyramided=False,
                        hold_hours=hold_h,
                        capital_after=equity,
                        initial_atr=pos["initial_atr"],
                        period=period_tag,
                        regime=regime,
                    ))
                    del open_pos[sym]

            # 2. Check Signals on current 1h bar close
            if len(open_pos) < 4:
                curr_risk_pct = gear_risks[current_gear]
                risk_usd = self.initial_capital * curr_risk_pct

                for sym in ALL_SYMBOLS:
                    if sym in open_pos or len(open_pos) >= 4:
                        continue
                    if not self.is_symbol_active(sym, curr_ts) or not self.is_symbol_active(sym, next_ts):
                        continue
                    df_1h = self.dfs_1h[sym]
                    if curr_ts not in df_1h.index or next_ts not in df_1h.index:
                        continue
                    bar = df_1h.loc[curr_ts]
                    c = float(bar["close"])
                    upper = float(bar["donch_high_120"])
                    lower = float(bar["donch_low_120"])
                    adx = float(bar["adx_14"])
                    atr = float(bar["atr_14"])
                    vol = float(bar["volume"])
                    vol_sma = float(bar["vol_sma_20"])

                    if adx >= 20.0 and vol > vol_sma:
                        if c > upper:
                            next_bar = self.dfs_1h[sym].loc[next_ts]
                            open_p = float(next_bar["open"])
                            rules = DEFAULT_RULES.get(sym, {"minQty": 0.01, "step": 0.01, "minNotional": 5.0})
                            stop_dist = 2.5 * atr
                            if stop_dist <= 0:
                                continue
                            qty = min(risk_usd / stop_dist, (self.initial_capital * 3.5) / open_p)
                            qty = math.floor(qty / rules["step"]) * rules["step"]
                            if qty >= rules["minQty"] and (qty * open_p) >= rules["minNotional"]:
                                slip = open_p * (exec_cfg.slippage_bps / 10000.0)
                                eff_entry_p = open_p + slip
                                entry_fee = qty * eff_entry_p * float(exec_cfg.fee_profile.taker_rate)
                                open_pos[sym] = {
                                    "direction": 1,
                                    "entry_ts": next_ts,
                                    "entry_price": eff_entry_p,
                                    "initial_qty": qty,
                                    "current_qty": qty,
                                    "initial_atr": atr,
                                    "current_stop": eff_entry_p - stop_dist,
                                    "entry_fee": entry_fee,
                                    "slippage_cost": abs(eff_entry_p - open_p) * qty,
                                    "spread_cost": qty * eff_entry_p * (exec_cfg.half_spread_bps / 10000.0),
                                    "funding_cashflow": 0.0,
                                    "entry_type": "MARKET_TAKER",
                                    "risk_usd": risk_usd,
                                }
                                equity -= entry_fee

                        elif c < lower:
                            next_bar = self.dfs_1h[sym].loc[next_ts]
                            open_p = float(next_bar["open"])
                            rules = DEFAULT_RULES.get(sym, {"minQty": 0.01, "step": 0.01, "minNotional": 5.0})
                            stop_dist = 2.5 * atr
                            if stop_dist <= 0:
                                continue
                            qty = min(risk_usd / stop_dist, (self.initial_capital * 3.5) / open_p)
                            qty = math.floor(qty / rules["step"]) * rules["step"]
                            if qty >= rules["minQty"] and (qty * open_p) >= rules["minNotional"]:
                                slip = open_p * (exec_cfg.slippage_bps / 10000.0)
                                eff_entry_p = open_p - slip
                                entry_fee = qty * eff_entry_p * float(exec_cfg.fee_profile.taker_rate)
                                open_pos[sym] = {
                                    "direction": -1,
                                    "entry_ts": next_ts,
                                    "entry_price": eff_entry_p,
                                    "initial_qty": qty,
                                    "current_qty": qty,
                                    "initial_atr": atr,
                                    "current_stop": eff_entry_p + stop_dist,
                                    "entry_fee": entry_fee,
                                    "slippage_cost": abs(eff_entry_p - open_p) * qty,
                                    "spread_cost": qty * eff_entry_p * (exec_cfg.half_spread_bps / 10000.0),
                                    "funding_cashflow": 0.0,
                                    "entry_type": "MARKET_TAKER",
                                    "risk_usd": risk_usd,
                                }
                                equity -= entry_fee

            equity_records.append((curr_ts, equity))

        return closed_trades, equity_records

    # --------------------------------------------------------------------------
    # STRATEGY 7: v2.1 (Trend Baseline 4h on Static 18 Coins)
    # --------------------------------------------------------------------------
    def run_v21(self, exec_cfg: ExecutionConfig) -> Tuple[List[GateTrade], List[Tuple[int, float]]]:
        equity = self.initial_capital
        open_pos: Dict[str, Dict[str, Any]] = {}
        closed_trades: List[GateTrade] = []
        equity_records: List[Tuple[int, float]] = []
        trade_counter = 0

        for i in range(len(self.sorted_ts_4h) - 1):
            curr_ts = self.sorted_ts_4h[i]
            next_ts = self.sorted_ts_4h[i + 1]
            regime = self.get_market_regime(curr_ts)

            dt_curr = pd.to_datetime(curr_ts, unit="ms", utc=True)
            is_fund_bar = (dt_curr.hour % 8 == 0)

            for sym in list(open_pos.keys()):
                pos = open_pos[sym]
                df_4h = self.dfs_4h[sym]
                if curr_ts not in df_4h.index:
                    continue
                bar = df_4h.loc[curr_ts]
                b_high = float(bar["high"])
                b_low = float(bar["low"])
                b_open = float(bar["open"])
                b_close = float(bar["close"])
                b_atr = float(bar["atr_14"])

                if is_fund_bar:
                    f_rate = self.get_funding_rate(sym, curr_ts)
                    fund_cf = -pos["direction"] * (pos["current_qty"] * b_close) * f_rate
                    pos["funding_cashflow"] += fund_cf
                    equity += fund_cf

                direction = pos["direction"]

                if direction == 1:
                    new_stop = b_high - 2.5 * b_atr
                    if new_stop > pos["current_stop"]:
                        pos["current_stop"] = new_stop
                    if not pos["is_pyramided"] and b_high >= pos["entry_price"] + 2.0 * pos["initial_atr"]:
                        add_qty = pos["initial_qty"] * 0.5
                        add_price = pos["entry_price"] + 2.0 * pos["initial_atr"]
                        add_slip = add_price * (exec_cfg.slippage_bps / 10000.0)
                        eff_add_price = add_price + add_slip
                        add_fee = add_qty * eff_add_price * float(exec_cfg.fee_profile.taker_rate)
                        pos["entry_fee"] += add_fee
                        pos["slippage_cost"] += (eff_add_price - add_price) * add_qty
                        total_qty = pos["current_qty"] + add_qty
                        pos["entry_price"] = (pos["current_qty"] * pos["entry_price"] + add_qty * eff_add_price) / total_qty
                        pos["current_qty"] = total_qty
                        pos["is_pyramided"] = True
                        pos["current_stop"] = pos["entry_price"]
                        equity -= add_fee

                    if b_low <= pos["current_stop"]:
                        raw_exit = min(b_open, pos["current_stop"]) if b_open <= pos["current_stop"] else pos["current_stop"]
                        slip = raw_exit * (exec_cfg.slippage_bps / 10000.0)
                        exit_price = raw_exit - slip
                        exit_fee = pos["current_qty"] * exit_price * float(exec_cfg.fee_profile.taker_rate)
                        gross_pnl = pos["current_qty"] * (exit_price - pos["entry_price"])
                        net_pnl = gross_pnl - pos["entry_fee"] - exit_fee + pos["funding_cashflow"]
                        equity += (gross_pnl - exit_fee)

                        trade_counter += 1
                        hold_h = (curr_ts - pos["entry_ts"]) / 3600000.0
                        period_tag = "IS" if curr_ts < 1751241600000 else "OOS"
                        pnl_r = net_pnl / pos["risk_usd"] if pos["risk_usd"] > 0 else 0.0

                        closed_trades.append(GateTrade(
                            trade_id=f"v21_{trade_counter:04d}",
                            strategy="v2.1",
                            scenario=exec_cfg.name,
                            symbol=sym,
                            direction=pos["direction"],
                            entry_ts=pos["entry_ts"],
                            exit_ts=curr_ts,
                            entry_price=pos["entry_price"],
                            exit_price=exit_price,
                            initial_qty=pos["initial_qty"],
                            current_qty=pos["current_qty"],
                            entry_type="MARKET_TAKER",
                            exit_type="MARKET_TAKER",
                            gross_pnl=gross_pnl,
                            entry_fee=pos["entry_fee"],
                            exit_fee=exit_fee,
                            slippage_cost=pos["slippage_cost"] + slip * pos["current_qty"],
                            spread_cost=pos["spread_cost"] + pos["current_qty"] * exit_price * (exec_cfg.half_spread_bps / 10000.0),
                            funding_cashflow=pos["funding_cashflow"],
                            net_pnl=net_pnl,
                            return_pct=(net_pnl / self.initial_capital) * 100.0,
                            pnl_r=pnl_r,
                            exit_reason="STOP_LOSS",
                            was_pyramided=pos["is_pyramided"],
                            hold_hours=hold_h,
                            capital_after=equity,
                            initial_atr=pos["initial_atr"],
                            period=period_tag,
                            regime=regime,
                        ))
                        del open_pos[sym]

                else:
                    new_stop = b_low + 2.5 * b_atr
                    if new_stop < pos["current_stop"]:
                        pos["current_stop"] = new_stop
                    if not pos["is_pyramided"] and b_low <= pos["entry_price"] - 2.0 * pos["initial_atr"]:
                        add_qty = pos["initial_qty"] * 0.5
                        add_price = pos["entry_price"] - 2.0 * pos["initial_atr"]
                        add_slip = add_price * (exec_cfg.slippage_bps / 10000.0)
                        eff_add_price = add_price - add_slip
                        add_fee = add_qty * eff_add_price * float(exec_cfg.fee_profile.taker_rate)
                        pos["entry_fee"] += add_fee
                        pos["slippage_cost"] += (add_price - eff_add_price) * add_qty
                        total_qty = pos["current_qty"] + add_qty
                        pos["entry_price"] = (pos["current_qty"] * pos["entry_price"] + add_qty * eff_add_price) / total_qty
                        pos["current_qty"] = total_qty
                        pos["is_pyramided"] = True
                        pos["current_stop"] = pos["entry_price"]
                        equity -= add_fee

                    if b_high >= pos["current_stop"]:
                        raw_exit = max(b_open, pos["current_stop"]) if b_open >= pos["current_stop"] else pos["current_stop"]
                        slip = raw_exit * (exec_cfg.slippage_bps / 10000.0)
                        exit_price = raw_exit + slip
                        exit_fee = pos["current_qty"] * exit_price * float(exec_cfg.fee_profile.taker_rate)
                        gross_pnl = pos["current_qty"] * (pos["entry_price"] - exit_price)
                        net_pnl = gross_pnl - pos["entry_fee"] - exit_fee + pos["funding_cashflow"]
                        equity += (gross_pnl - exit_fee)

                        trade_counter += 1
                        hold_h = (curr_ts - pos["entry_ts"]) / 3600000.0
                        period_tag = "IS" if curr_ts < 1751241600000 else "OOS"
                        pnl_r = net_pnl / pos["risk_usd"] if pos["risk_usd"] > 0 else 0.0

                        closed_trades.append(GateTrade(
                            trade_id=f"v21_{trade_counter:04d}",
                            strategy="v2.1",
                            scenario=exec_cfg.name,
                            symbol=sym,
                            direction=pos["direction"],
                            entry_ts=pos["entry_ts"],
                            exit_ts=curr_ts,
                            entry_price=pos["entry_price"],
                            exit_price=exit_price,
                            initial_qty=pos["initial_qty"],
                            current_qty=pos["current_qty"],
                            entry_type="MARKET_TAKER",
                            exit_type="MARKET_TAKER",
                            gross_pnl=gross_pnl,
                            entry_fee=pos["entry_fee"],
                            exit_fee=exit_fee,
                            slippage_cost=pos["slippage_cost"] + slip * pos["current_qty"],
                            spread_cost=pos["spread_cost"] + pos["current_qty"] * exit_price * (exec_cfg.half_spread_bps / 10000.0),
                            funding_cashflow=pos["funding_cashflow"],
                            net_pnl=net_pnl,
                            return_pct=(net_pnl / self.initial_capital) * 100.0,
                            pnl_r=pnl_r,
                            exit_reason="STOP_LOSS",
                            was_pyramided=pos["is_pyramided"],
                            hold_hours=hold_h,
                            capital_after=equity,
                            initial_atr=pos["initial_atr"],
                            period=period_tag,
                            regime=regime,
                        ))
                        del open_pos[sym]

            # 2. Signals on 4h close
            if len(open_pos) < 4:
                candidates = []
                for sym in ALL_SYMBOLS:
                    if sym in open_pos:
                        continue
                    if not self.is_symbol_active(sym, curr_ts) or not self.is_symbol_active(sym, next_ts):
                        continue
                    df_4h = self.dfs_4h[sym]
                    if curr_ts not in df_4h.index or next_ts not in df_4h.index:
                        continue
                    bar = df_4h.loc[curr_ts]
                    c = float(bar["close"])
                    upper = float(bar["donch_high_30"])
                    lower = float(bar["donch_low_30"])
                    atr = float(bar["atr_14"])
                    norm_atr = float(bar["norm_atr"])

                    if atr <= 0:
                        continue

                    if c > upper:
                        candidates.append((norm_atr, sym, 1, atr))
                    elif c < lower:
                        candidates.append((norm_atr, sym, -1, atr))

                candidates.sort(key=lambda x: x[0], reverse=True)
                for score, sym, direction, atr in candidates:
                    if len(open_pos) >= 4:
                        break
                    next_bar = self.dfs_4h[sym].loc[next_ts]
                    open_p = float(next_bar["open"])
                    rules = DEFAULT_RULES.get(sym, {"minQty": 0.01, "step": 0.01, "minNotional": 5.0})

                    risk_usd = 3.00
                    stop_dist = 2.5 * atr
                    if stop_dist <= 0:
                        continue
                    qty = min(risk_usd / stop_dist, (self.initial_capital * 3.5) / open_p)
                    qty = math.floor(qty / rules["step"]) * rules["step"]
                    if qty < rules["minQty"] or (qty * open_p) < rules["minNotional"]:
                        continue

                    slip = open_p * (exec_cfg.slippage_bps / 10000.0)
                    eff_entry_p = open_p + slip if direction == 1 else open_p - slip
                    entry_fee = qty * eff_entry_p * float(exec_cfg.fee_profile.taker_rate)
                    open_pos[sym] = {
                        "direction": direction,
                        "entry_ts": next_ts,
                        "entry_price": eff_entry_p,
                        "initial_qty": qty,
                        "current_qty": qty,
                        "initial_atr": atr,
                        "current_stop": eff_entry_p - stop_dist if direction == 1 else eff_entry_p + stop_dist,
                        "entry_fee": entry_fee,
                        "slippage_cost": abs(eff_entry_p - open_p) * qty,
                        "spread_cost": qty * eff_entry_p * (exec_cfg.half_spread_bps / 10000.0),
                        "funding_cashflow": 0.0,
                        "entry_type": "MARKET_TAKER",
                        "is_pyramided": False,
                        "risk_usd": risk_usd,
                    }
                    equity -= entry_fee

            equity_records.append((curr_ts, equity))

        return closed_trades, equity_records

    # --------------------------------------------------------------------------
    # RANDOM CONTROL BENCHMARK
    # --------------------------------------------------------------------------
    def run_random_control(
        self,
        avg_holding_bars: int = 15,
        target_trades_count: int = 250,
        seed: int = 42,
    ) -> Tuple[float, float]:
        """
        Executes a randomized entry/exit benchmark under identical BASE fees
        to evaluate genuine statistical alpha over noise.
        """
        np.random.seed(seed)
        equity = self.initial_capital
        net_pnls = []

        # Sample random entry points across 1h timeline
        max_idx = len(self.sorted_ts_1h) - avg_holding_bars - 1
        if max_idx <= 0:
            return 0.0, 0.0

        sample_indices = np.random.choice(range(100, max_idx), size=target_trades_count, replace=False)
        sample_indices.sort()

        exec_cfg = CONFIG_BASE
        fee_rate = float(exec_cfg.fee_profile.taker_rate)

        for idx in sample_indices:
            curr_ts = self.sorted_ts_1h[idx]
            exit_ts = self.sorted_ts_1h[idx + avg_holding_bars]
            sym = np.random.choice(ALL_SYMBOLS[:10]) # Top 10 liquid coins

            if not self.is_symbol_active(sym, curr_ts) or not self.is_symbol_active(sym, exit_ts):
                continue

            df = self.dfs_1h[sym]
            if curr_ts not in df.index or exit_ts not in df.index:
                continue

            direction = 1 if np.random.rand() > 0.5 else -1
            open_p = float(df.loc[curr_ts, "open"])
            exit_p = float(df.loc[exit_ts, "close"])
            atr = float(df.loc[curr_ts, "atr_14"])
            if atr <= 0 or open_p <= 0:
                continue

            risk_usd = 3.00
            stop_dist = 2.5 * atr
            qty = min(risk_usd / stop_dist, (self.initial_capital * 3.5) / open_p)

            slip_in = open_p * (exec_cfg.slippage_bps / 10000.0)
            slip_out = exit_p * (exec_cfg.slippage_bps / 10000.0)
            eff_entry = open_p + slip_in if direction == 1 else open_p - slip_in
            eff_exit = exit_p - slip_out if direction == 1 else exit_p + slip_out

            entry_fee = qty * eff_entry * fee_rate
            exit_fee = qty * eff_exit * fee_rate

            gross = qty * (eff_exit - eff_entry) if direction == 1 else qty * (eff_entry - eff_exit)
            # 8h funding approximation
            n_fund = avg_holding_bars // 8
            fund = -direction * (qty * eff_exit) * (0.000100 * n_fund)

            net = gross - entry_fee - exit_fee + fund
            net_pnls.append(net)

        total_net = sum(net_pnls)
        # Average bps per trade
        avg_bps = (np.mean(net_pnls) / (self.initial_capital * 1.5)) * 10000.0 if net_pnls else 0.0
        return total_net, avg_bps


    # --------------------------------------------------------------------------
    # METRICS CALCULATION
    # --------------------------------------------------------------------------
    def calculate_metrics(
        self,
        strategy: str,
        scenario: str,
        period: str,
        trades: List[GateTrade],
        equity_records: List[Tuple[int, float]],
        random_net_pnl: float,
        random_bps: float,
    ) -> StrategyMetrics:
        """Computes comprehensive performance, cost attribution, and statistical metrics."""
        # Filter trades by period
        if period == "IS":
            p_trades = [t for t in trades if t.period == "IS"]
            p_eq = [e[1] for e in equity_records if e[0] < 1751241600000]
        elif period == "OOS":
            p_trades = [t for t in trades if t.period == "OOS"]
            p_eq = [e[1] for e in equity_records if e[0] >= 1751241600000]
        else: # FULL
            p_trades = trades
            p_eq = [e[1] for e in equity_records]

        total_trades = len(p_trades)
        if total_trades == 0:
            return StrategyMetrics(
                strategy=strategy,
                scenario=scenario,
                period=period,
                initial_capital=self.initial_capital,
                final_equity=self.initial_capital,
                net_pnl=0.0,
                return_pct=0.0,
                cagr_pct=0.0,
                max_drawdown_pct=0.0,
                max_drawdown_usd=0.0,
                total_trades=0,
                win_trades=0,
                loss_trades=0,
                win_rate_pct=0.0,
                profit_factor=0.0,
                payoff_ratio=0.0,
                net_expectancy_r=0.0,
                net_expectancy_bps=0.0,
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                calmar_ratio=0.0,
                gross_pnl=0.0,
                total_fees=0.0,
                maker_fees=0.0,
                taker_fees=0.0,
                total_slippage=0.0,
                total_spread=0.0,
                total_funding=0.0,
                random_benchmark_net_pnl=0.0,
                alpha_over_random_bps=0.0,
                verdict="INCONCLUSIVE",
            )

        win_trades = [t for t in p_trades if t.net_pnl > 0]
        loss_trades = [t for t in p_trades if t.net_pnl <= 0]
        n_wins = len(win_trades)
        n_loss = len(loss_trades)
        win_rate = (n_wins / total_trades) * 100.0

        gross_wins = sum(t.net_pnl for t in win_trades)
        gross_losses = abs(sum(t.net_pnl for t in loss_trades))
        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else 999.0

        avg_win = (gross_wins / n_wins) if n_wins > 0 else 0.0
        avg_loss = (gross_losses / n_loss) if n_loss > 0 else 1.0
        payoff_ratio = avg_win / avg_loss if avg_loss > 0 else 0.0

        net_pnl = sum(t.net_pnl for t in p_trades)
        gross_pnl = sum(t.gross_pnl for t in p_trades)
        total_entry_fees = sum(t.entry_fee for t in p_trades)
        total_exit_fees = sum(t.exit_fee for t in p_trades)
        total_fees = total_entry_fees + total_exit_fees
        maker_fees = sum(t.exit_fee for t in p_trades if t.exit_type == "LIMIT_MAKER") + sum(t.entry_fee for t in p_trades if t.entry_type == "LIMIT_MAKER")
        taker_fees = total_fees - maker_fees

        total_slippage = sum(t.slippage_cost for t in p_trades)
        total_spread = sum(t.spread_cost for t in p_trades)
        total_funding = sum(t.funding_cashflow for t in p_trades)

        final_equity = self.initial_capital + net_pnl
        return_pct = (net_pnl / self.initial_capital) * 100.0

        # Duration in years
        if period == "IS":
            n_years = 1.5
        elif period == "OOS":
            n_years = 1.33
        else:
            n_years = 2.83
        cagr = (((final_equity / self.initial_capital) ** (1.0 / n_years)) - 1.0) * 100.0 if final_equity > 0 else -100.0

        max_dd_pct, max_dd_usd = self.compute_drawdown(p_eq)

        # Net expectancy
        r_list = [t.pnl_r for t in p_trades]
        net_exp_r = float(np.mean(r_list)) if r_list else 0.0
        avg_notional = 15.0 # Average trade notional
        net_exp_bps = (float(np.mean([t.net_pnl for t in p_trades])) / avg_notional) * 10000.0 if p_trades else 0.0

        # Sharpe and Sortino (trade-level annualized)
        returns = [t.return_pct / 100.0 for t in p_trades]
        trades_per_year = total_trades / n_years
        ann_factor = math.sqrt(max(1.0, trades_per_year))
        mean_ret = float(np.mean(returns))
        std_ret = float(np.std(returns)) if len(returns) > 1 else 1.0
        sharpe = (mean_ret / std_ret) * ann_factor if std_ret > 0 else 0.0

        downside = [r for r in returns if r < 0]
        std_down = float(np.std(downside)) if len(downside) > 1 else 1.0
        sortino = (mean_ret / std_down) * ann_factor if std_down > 0 else 0.0

        calmar = (return_pct / max_dd_pct) if max_dd_pct > 0 else 0.0
        alpha_over_random = net_exp_bps - random_bps

        # Verdict
        if net_pnl > 0 and net_exp_r > 0.05 and max_dd_pct < 25.0 and total_trades >= 30:
            verdict = "PROFITABILITY_PASS"
        elif net_pnl < 0:
            verdict = "NEGATIVE_EDGE"
        else:
            verdict = "INCONCLUSIVE"

        return StrategyMetrics(
            strategy=strategy,
            scenario=scenario,
            period=period,
            initial_capital=self.initial_capital,
            final_equity=final_equity,
            net_pnl=net_pnl,
            return_pct=return_pct,
            cagr_pct=cagr,
            max_drawdown_pct=max_dd_pct,
            max_drawdown_usd=max_dd_usd,
            total_trades=total_trades,
            win_trades=n_wins,
            loss_trades=n_loss,
            win_rate_pct=win_rate,
            profit_factor=profit_factor,
            payoff_ratio=payoff_ratio,
            net_expectancy_r=net_exp_r,
            net_expectancy_bps=net_exp_bps,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            calmar_ratio=calmar,
            gross_pnl=gross_pnl,
            total_fees=total_fees,
            maker_fees=maker_fees,
            taker_fees=taker_fees,
            total_slippage=total_slippage,
            total_spread=total_spread,
            total_funding=total_funding,
            random_benchmark_net_pnl=random_net_pnl,
            alpha_over_random_bps=alpha_over_random,
            verdict=verdict,
        )


print("EconomicGate010 runner engine complete.")
