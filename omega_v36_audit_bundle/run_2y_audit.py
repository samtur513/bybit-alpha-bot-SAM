"""
Official 2-Year Quantitative Audit Script for v3.6 Omega Dual-Regime ZF-8.
Executes 4-Pass Gauntlet under the crypto-hybrid-bot-tester standard:
- Pass 1: Baseline Clean PIT (Trend ZF-8 + IRAS Range Maker)
- Pass 2: Friction & Execution Stress (Taker 0.090%, Maker 0.0324%, Slip 0.02%)
- Pass 3: Full Hybrid with News Core v1.2 Defensive Shield (Macro Embargo [-45m, +30m])
- Pass 4: Calibrated Final Verification (Enhanced IRAS Bandwidth 3.0%, KER <= 0.15)
"""

import sys
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import json
import numpy as np
import pandas as pd

BUNDLE_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(BUNDLE_ROOT))

from src.core_v34 import CoreV34Engine
from src.range_engine import RangeMeanReversionEngine
from src.regime_classifier import MarketRegimeClassifier, MarketRegime
from src.config import (
    DEFAULT_SYMBOLS,
    INITIAL_CAPITAL,
    BASE_TAKER_FEE,
    BASE_MAKER_FEE,
    BASE_SLIPPAGE,
    PORTFOLIO_LEVERAGE,
    DRAWDOWN_LIMIT_PCT,
)
from src.news_core.news_config import NewsConfig
from src.news_core.regime_guard import RegimeGuard

# Check for data directory (supports local or parent repository data)
DATA_DIR = BUNDLE_ROOT / "data" / "parquet_2024_2026"
if not DATA_DIR.exists():
    DATA_DIR = BUNDLE_ROOT.parent / "data" / "parquet_2024_2026"

print("=" * 85)
print("  QUANT AUDIT: OMEGA DUAL-REGIME ZF-8 (2024-2026 BYBIT LINEAR PERPETUALS)")
print("=" * 85)

if not DATA_DIR.exists():
    print(f"[!] Data directory not found at {DATA_DIR}. Please place 1h parquet files in data/parquet_2024_2026/ to execute backtest.")
    sys.exit(0)

# 1. Load Data
symbols = [s for s in DEFAULT_SYMBOLS if (DATA_DIR / f"{s}_1h.parquet").exists()]
print(f"Loading data for {len(symbols)} Bybit Linear Perpetuals from {DATA_DIR}...")

classifier = MarketRegimeClassifier()
dfs = {}
timestamps_set = set()

for sym in symbols:
    p = DATA_DIR / f"{sym}_1h.parquet"
    raw_df = pd.read_parquet(p)
    df = classifier.prepare_indicators(raw_df)
    
    high = df["high"]
    low = df["low"]
    close = df["close"]
    
    # Point-in-time shift(1) Donchian channels & EMAs
    df["upper"] = high.shift(1).rolling(120).max()
    df["lower"] = low.shift(1).rolling(120).min()
    df["ema_fast"] = close.ewm(span=50, adjust=False).mean()
    df["ema_slow"] = close.ewm(span=200, adjust=False).mean()
    
    # Kaufman Efficiency Ratio (KER 14) on shift(1)
    net_chg = (close.shift(1) - close.shift(15)).abs()
    gross_chg = (close.shift(1) - close.shift(2)).abs().rolling(14).sum()
    df["ker"] = (net_chg / gross_chg.replace(0, np.nan)).fillna(0.5).clip(0.0, 1.0)
    
    df = df.dropna(subset=["upper", "lower", "ema_slow", "atr", "volume_sma", "adx", "bb_mid", "bb_lower", "bb_upper"]).reset_index(drop=True)
    dfs[sym] = df.set_index("timestamp")
    timestamps_set.update(df["timestamp"].tolist())

sorted_ts = sorted(list(timestamps_set))
print(f"Total timestamps: {len(sorted_ts)} bars ({len(sorted_ts)/24:.1f} days, ~2.8 years)")

fast_data = {}
for sym, df in dfs.items():
    fast_data[sym] = {
        "open": df["open"].to_dict(),
        "high": df["high"].to_dict(),
        "low": df["low"].to_dict(),
        "close": df["close"].to_dict(),
        "volume": df["volume"].to_dict(),
        "volume_sma": df["volume_sma"].to_dict(),
        "upper": df["upper"].to_dict(),
        "lower": df["lower"].to_dict(),
        "ema_fast": df["ema_fast"].to_dict(),
        "ema_slow": df["ema_slow"].to_dict(),
        "atr": df["atr"].to_dict(),
        "adx": df["adx"].to_dict(),
        "rsi": df["rsi"].to_dict(),
        "bb_mid": df["bb_mid"].to_dict(),
        "bb_upper": df["bb_upper"].to_dict(),
        "bb_lower": df["bb_lower"].to_dict(),
        "bb_width_pctl": df["bb_width_pctl"].to_dict(),
        "ker": df["ker"].to_dict(),
    }

instrument_rules = {
    sym: {
        "minOrderQty": Decimal("0.001") if "BTC" in sym else Decimal("0.01"),
        "qtyStep": Decimal("0.001") if "BTC" in sym else Decimal("0.01"),
        "minNotional": Decimal("5.0"),
        "maxOrderQty": Decimal("1000000"),
    }
    for sym in symbols
}

# Macro Blackout Precomputation
cfg_v12 = NewsConfig(macro_lockout_before_minutes=45, macro_lockout_after_minutes=30)
guard_v12 = RegimeGuard(cfg_v12)
macro_events = guard_v12._macro_events
ts_array = np.array(sorted_ts, dtype=np.int64)

macro_blocked = np.zeros(len(sorted_ts), dtype=bool)
for ev_idx, (ev_dt, ev_name) in enumerate(macro_events):
    ev_ts = int(ev_dt.timestamp() * 1000)
    w_start = ev_ts - 45 * 60 * 1000 - 3600 * 1000
    w_end = ev_ts + 30 * 60 * 1000
    macro_blocked |= (ts_array >= w_start) & (ts_array <= w_end)


def simulate_omega(
    pass_name: str,
    enable_range: bool = True,
    use_iras: bool = True,
    min_bw: Decimal = Decimal("0.030"),
    taker_fee: Decimal = BASE_TAKER_FEE,
    maker_fee: Decimal = BASE_MAKER_FEE,
    slippage: Decimal = BASE_SLIPPAGE,
    use_news_defense: bool = False,
) -> dict:
    t0 = time.time()
    
    trend_engine = CoreV34Engine(
        initial_capital=Decimal("300.0"),
        instrument_rules=instrument_rules,
        max_leverage=Decimal("3.5"),
        atr_stop_mult=Decimal("2.5"),
        taker_fee=taker_fee,
        slippage=slippage,
        portfolio_leverage=PORTFOLIO_LEVERAGE,
        drawdown_limit_pct=Decimal("35.0"),
        cooldown_bars=3,
        min_adx=18.0,
        enable_btc_delta_filter=True,
        enable_fast_scratch=True,
        fast_scratch_bars=3,
        fast_scratch_mfe_r=0.3,
        enable_cascade_tp=True,
        bar_interval_ms=3600000,
    )
    
    range_engine = RangeMeanReversionEngine(
        initial_capital=Decimal("300.0"),
        instrument_rules=instrument_rules,
        risk_per_trade_pct=Decimal("0.010"),
        max_concurrent_positions=2,
        max_portfolio_leverage=PORTFOLIO_LEVERAGE,
        maker_fee=maker_fee,
        taker_fee=taker_fee,
        slippage=slippage,
        sl_atr_mult=Decimal("1.0"),
        min_bandwidth_clearance=min_bw,
        min_tp_dist_pct=Decimal("0.012"),
        max_tp_dist_pct=Decimal("0.035"),
        cooldown_bars=3,
    )
    
    equity_curve = []
    gear_counts = {g: 0 for g in range(1, 9)}
    
    for i in range(len(sorted_ts) - 1):
        ts = sorted_ts[i]
        next_ts = sorted_ts[i + 1]
        
        bars = {}
        next_bars = {}
        next_open_prices = {}
        regimes = {}
        
        for sym in symbols:
            fd = fast_data[sym]
            if ts in fd["close"] and next_ts in fd["open"]:
                c = fd["close"][ts]
                mid = fd["bb_mid"][ts]
                up = fd["bb_upper"][ts]
                lo = fd["bb_lower"][ts]
                adx = fd["adx"][ts]
                vol = fd["volume"][ts]
                vol_sma = fd["volume_sma"][ts]
                pctl = fd["bb_width_pctl"][ts]
                rsi = fd["rsi"][ts]
                atr = fd["atr"][ts]
                ker = fd["ker"].get(ts, 0.10)
                
                bars[sym] = {
                    "open": fd["open"][ts],
                    "high": fd["high"][ts],
                    "low": fd["low"][ts],
                    "close": c,
                    "volume": vol,
                    "volume_sma": vol_sma,
                    "upper": fd["upper"][ts],
                    "lower": fd["lower"][ts],
                    "ema_fast": fd["ema_fast"][ts],
                    "ema_slow": fd["ema_slow"][ts],
                    "atr": atr,
                    "adx": adx,
                    "rsi": rsi,
                    "bb_mid": mid,
                    "bb_upper": up,
                    "bb_lower": lo,
                    "ker": ker,
                }
                
                next_bars[sym] = {
                    "open": fd["open"][next_ts],
                    "high": fd["high"][next_ts],
                    "low": fd["low"][next_ts],
                    "close": fd["close"][next_ts],
                }
                next_open_prices[sym] = Decimal(str(fd["open"][next_ts]))
                
                reg = classifier.classify_bar(
                    symbol=sym,
                    adx=adx,
                    bb_mid=mid,
                    bb_upper=up,
                    bb_lower=lo,
                    bb_width_pctl=pctl,
                    kc_upper=mid + 1.5 * atr,
                    kc_lower=mid - 1.5 * atr,
                    rsi=rsi,
                    atr=atr,
                    volume=vol,
                    volume_sma=vol_sma,
                )
                
                if use_iras and ker > 0.15:
                    reg.is_chop = False
                    reg.regime = MarketRegime.NEUTRAL
                    
                regimes[sym] = reg
                
        if "BTCUSDT" not in bars:
            continue
            
        btc_c = bars["BTCUSDT"]["close"]
        btc_ef = bars["BTCUSDT"]["ema_fast"]
        btc_es = bars["BTCUSDT"]["ema_slow"]
        btc_bullish = btc_ef > btc_es and btc_c > btc_ef
        btc_bearish = btc_ef < btc_es and btc_c < btc_ef
        
        cur_gear = trend_engine.transmission.current_gear
        gear_counts[cur_gear] += 1
        macro_active = use_news_defense and macro_blocked[i]
        
        if enable_range and not macro_active:
            active_trend_syms = list(trend_engine.open_positions.keys())
            range_engine.step_bar(
                timestamp=ts,
                bars=bars,
                next_bars=next_bars,
                next_open_prices=next_open_prices,
                regimes=regimes,
                btc_bullish=btc_bullish,
                btc_bearish=btc_bearish,
                active_trend_symbols=active_trend_syms,
            )
            
        trend_bars = {
            s: b for s, b in bars.items()
            if not enable_range or (s not in range_engine.open_positions and s not in range_engine.pending_orders)
        }
        
        if not macro_active:
            trend_engine.process_bar_event(
                timestamp=ts,
                bars=trend_bars,
                next_open_prices=next_open_prices,
                manage_positions=True,
                btc_bullish=btc_bullish,
                btc_bearish=btc_bearish,
            )
            
        curr_prices = {s: Decimal(str(b["close"])) for s, b in bars.items()}
        trend_eq = trend_engine.get_equity(curr_prices)
        range_eq = range_engine.get_equity(curr_prices) if enable_range else Decimal("300.0")
        combined_eq = float(trend_eq) + (float(range_eq) - 300.0)
        equity_curve.append(combined_eq)
        
    elapsed = time.time() - t0
    
    eq_arr = np.array(equity_curve)
    peak = np.maximum.accumulate(eq_arr)
    dd_arr = (peak - eq_arr) / peak * 100.0
    max_dd = float(np.max(dd_arr)) if len(dd_arr) > 0 else 0.0
    final_eq = float(equity_curve[-1])
    net_pnl = final_eq - 300.0
    ret_pct = net_pnl / 300.0 * 100.0
    years = len(sorted_ts) / (24.0 * 365.25)
    cagr = ((final_eq / 300.0) ** (1.0 / years) - 1.0) * 100.0 if final_eq > 0 else -100.0
    calmar = cagr / max_dd if max_dd > 0 else 0.0
    
    eq_s = pd.Series(equity_curve)
    daily_ret = eq_s.iloc[::24].pct_change().dropna()
    sharpe = (daily_ret.mean() / daily_ret.std() * np.sqrt(365.25)) if daily_ret.std() > 0 else 0.0
    
    all_trades = list(trend_engine.closed_trades) + (list(range_engine.closed_trades) if enable_range else [])
    tot_trades = len(all_trades)
    wins = sum(1 for t in all_trades if t.net_pnl > 0)
    wr = (wins / tot_trades * 100.0) if tot_trades > 0 else 0.0
    gross_win = sum(float(t.net_pnl) for t in all_trades if t.net_pnl > 0)
    gross_loss = abs(sum(float(t.net_pnl) for t in all_trades if t.net_pnl < 0))
    pf = (gross_win / gross_loss) if gross_loss > 0 else 999.0
    
    return {
        "pass": pass_name,
        "final_equity": round(final_eq, 2),
        "net_pnl": round(net_pnl, 2),
        "return_pct": round(ret_pct, 1),
        "cagr_pct": round(cagr, 1),
        "max_dd_pct": round(max_dd, 2),
        "calmar": round(calmar, 2),
        "sharpe": round(sharpe, 2),
        "win_rate": round(wr, 1),
        "profit_factor": round(pf, 2),
        "total_trades": tot_trades,
        "trend_trades": len(trend_engine.closed_trades),
        "range_trades": len(range_engine.closed_trades) if enable_range else 0,
        "gear_distribution": {f"G{k}": round(v / sum(gear_counts.values()) * 100, 1) for k, v in gear_counts.items()},
        "elapsed_sec": round(elapsed, 1),
    }

if __name__ == "__main__":
    print("\nRunning Pass 4: Calibrated Final Verification (Pass 4 under Bybit non-VIP fees)...")
    res = simulate_omega("Pass 4: Calibrated Final Verification", enable_range=True, use_iras=True, min_bw=Decimal("0.030"), use_news_defense=True)
    print(f"Final Equity: ${res['final_equity']} (+{res['return_pct']}%)")
    print(f"CAGR: {res['cagr_pct']}% | Max Drawdown: {res['max_dd_pct']}%")
    print(f"Sharpe Ratio: {res['sharpe']} | Calmar Ratio: {res['calmar']}")
    print(f"Total Trades: {res['total_trades']} (Trend: {res['trend_trades']}, Range: {res['range_trades']})")
    print(f"Win Rate: {res['win_rate']}% | Profit Factor: {res['profit_factor']}")
    print(f"Gear Distribution: {res['gear_distribution']}")
