"""
Multi-Alpha Growth Engine v2.0 - Zero Lookahead Historical Backtester (2024-2026).

Strict Zero Lookahead Protocol:
1. Signal calculated at Bar t Close.
2. Orders filled at Bar t+1 Open with Taker Fee (0.06%) and Slippage (0.02%).
3. Half-Kelly (2.5%) Position Sizing with hard 3.5x leverage cap.
4. Regime Kill-Switch: 3 consecutive losses -> 0.75% risk; 2 wins -> recover to 2.5%.
5. Anti-Martingale Pyramiding (+50% add at +2.0 ATR, stop moved to breakeven).
6. Ratchet Trailing Stop (2.5 ATR trailing).
7. Portfolio correlation limits: max 2 longs, max 2 shorts concurrently across 18 symbols.
8. Funding fees deducted every 8 hours.
"""

from dataclasses import dataclass, field
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
import time
from typing import Dict, List, Optional, Tuple, Any
import numpy as np
import pandas as pd

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data" / "parquet_2024_2026"

SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "SUIUSDT", "DOGEUSDT", "AVAXUSDT",
    "NEARUSDT", "LINKUSDT", "APTUSDT", "ADAUSDT", "XRPUSDT", "BNBUSDT",
    "TIAUSDT", "INJUSDT", "ARBUSDT", "OPUSDT", "1000PEPEUSDT", "RENDERUSDT"
]

# Bybit contract specs approximation
INSTRUMENT_RULES = {
    "BTCUSDT": {"minQty": 0.001, "step": 0.001, "minNotional": 5.0},
    "ETHUSDT": {"minQty": 0.01, "step": 0.01, "minNotional": 5.0},
    "SOLUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0},
    "SUIUSDT": {"minQty": 1.0, "step": 1.0, "minNotional": 5.0},
    "DOGEUSDT": {"minQty": 10.0, "step": 10.0, "minNotional": 5.0},
    "AVAXUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0},
    "NEARUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0},
    "LINKUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0},
    "APTUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0},
    "ADAUSDT": {"minQty": 1.0, "step": 1.0, "minNotional": 5.0},
    "XRPUSDT": {"minQty": 1.0, "step": 1.0, "minNotional": 5.0},
    "BNBUSDT": {"minQty": 0.01, "step": 0.01, "minNotional": 5.0},
    "TIAUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0},
    "INJUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0},
    "ARBUSDT": {"minQty": 1.0, "step": 1.0, "minNotional": 5.0},
    "OPUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0},
    "1000PEPEUSDT": {"minQty": 100.0, "step": 100.0, "minNotional": 5.0},
    "RENDERUSDT": {"minQty": 0.1, "step": 0.1, "minNotional": 5.0},
}

TAKER_FEE = 0.0006      # 0.06% Bybit V5
SLIPPAGE = 0.0002       # 0.02% realistic execution slippage
FUNDING_8H_RATE = 0.0001 # 0.01% typical per 8h funding fee

@dataclass
class Position:
    symbol: str
    direction: int # +1 Long, -1 Short
    entry_bar_idx: int
    entry_ts: int
    entry_price: float
    initial_qty: float
    current_qty: float
    current_stop: float
    initial_atr: float
    highest_price: float
    lowest_price: float
    is_pyramided: bool = False
    is_breakeven_set: bool = False

@dataclass
class TradeRecord:
    symbol: str
    direction: int
    entry_ts: int
    exit_ts: int
    entry_price: float
    exit_price: float
    initial_qty: float
    final_qty: float
    net_pnl: float
    return_pct: float
    exit_reason: str
    was_pyramided: bool
    hold_hours: float
    capital_after: float

def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Computes technical indicators with strictly closed bar references."""
    df = df.copy()
    
    # Donchian 120-hour (5 days)
    # Important: shift(1) ensures candle t only looks at [t-120 : t-1]
    df["upper_120"] = df["high"].rolling(120).max().shift(1)
    df["lower_120"] = df["low"].rolling(120).min().shift(1)
    
    # EMAs
    df["ema_50"] = df["close"].ewm(span=50, adjust=False).mean()
    df["ema_200"] = df["close"].ewm(span=200, adjust=False).mean()
    
    # ATR(14)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - df["close"].shift(1)).abs()
    tr3 = (df["low"] - df["close"].shift(1)).abs()
    df["tr"] = np.maximum(tr1, np.maximum(tr2, tr3))
    df["atr_14"] = df["tr"].rolling(14).mean()
    
    # Volume SMA(20)
    df["vol_sma_20"] = df["volume"].rolling(20).mean()
    df["vol_ratio"] = df["volume"] / df["vol_sma_20"]
    
    return df

def run_backtest_2024_2026(initial_capital: float = 300.0) -> Dict[str, Any]:
    print("Loading datasets for 18 symbols...")
    data_by_sym = {}
    timestamps = set()
    
    for sym in SYMBOLS:
        p_path = DATA_DIR / f"{sym}_1h.parquet"
        if not p_path.exists():
            print(f"Warning: {p_path} missing!")
            continue
        df = pd.read_parquet(p_path)
        if len(df) < 300:
            continue
        df = compute_indicators(df)
        df = df.dropna(subset=["upper_120", "lower_120", "ema_200", "atr_14", "vol_sma_20"]).reset_index(drop=True)
        data_by_sym[sym] = df.set_index("timestamp")
        timestamps.update(df["timestamp"].tolist())
        
    sorted_ts = sorted(list(timestamps))
    print(f"Total timeline: {len(sorted_ts)} hourly bars from {pd.to_datetime(sorted_ts[0], unit='ms')} to {pd.to_datetime(sorted_ts[-1], unit='ms')}")
    
    # Backtest state
    cash_equity = initial_capital
    peak_equity = initial_capital
    max_drawdown = 0.0
    
    consecutive_losses = 0
    consecutive_wins = 0
    is_kill_switch = False
    
    open_positions: Dict[str, Position] = {}
    closed_trades: List[TradeRecord] = []
    equity_curve: List[Tuple[int, float]] = []
    
    # Iterate timeline bar by bar (Simulation loop)
    for i in range(len(sorted_ts) - 1):
        curr_ts = sorted_ts[i]
        next_ts = sorted_ts[i + 1]
        
        # 1. Evaluate Active Positions on current bar (intra-bar evaluation)
        for sym, pos in list(open_positions.items()):
            df_sym = data_by_sym.get(sym)
            if df_sym is None or curr_ts not in df_sym.index:
                continue
                
            bar = df_sym.loc[curr_ts]
            b_high = float(bar["high"])
            b_low = float(bar["low"])
            b_open = float(bar["open"])
            b_close = float(bar["close"])
            b_atr = float(bar["atr_14"])
            
            # Update extremes
            if b_high > pos.highest_price:
                pos.highest_price = b_high
            if b_low < pos.lowest_price:
                pos.lowest_price = b_low
                
            # Check Stop-Loss
            is_exit = False
            exit_price = 0.0
            exit_reason = ""
            
            if pos.direction == 1:
                if b_low <= pos.current_stop:
                    is_exit = True
                    # Fill with slippage
                    fill = min(b_open, pos.current_stop) if b_open <= pos.current_stop else pos.current_stop
                    exit_price = fill * (1.0 - SLIPPAGE)
                    exit_reason = "STOP_LOSS"
            else:
                if b_high >= pos.current_stop:
                    is_exit = True
                    fill = max(b_open, pos.current_stop) if b_open >= pos.current_stop else pos.current_stop
                    exit_price = fill * (1.0 + SLIPPAGE)
                    exit_reason = "STOP_LOSS"
                    
            if is_exit:
                gross_pnl = (pos.current_qty * (exit_price - pos.entry_price)) if pos.direction == 1 else (pos.current_qty * (pos.entry_price - exit_price))
                exit_fee = pos.current_qty * exit_price * TAKER_FEE
                net_pnl = gross_pnl - exit_fee
                cash_equity += net_pnl
                
                # Streak tracking
                is_win = net_pnl > 0
                if is_win:
                    consecutive_wins += 1
                    consecutive_losses = 0
                    if is_kill_switch and consecutive_wins >= 2:
                        is_kill_switch = False
                else:
                    consecutive_losses += 1
                    consecutive_wins = 0
                    if consecutive_losses >= 3:
                        is_kill_switch = True
                        
                hold_hours = (curr_ts - pos.entry_ts) / 3600000.0
                closed_trades.append(
                    TradeRecord(
                        symbol=sym,
                        direction=pos.direction,
                        entry_ts=pos.entry_ts,
                        exit_ts=curr_ts,
                        entry_price=pos.entry_price,
                        exit_price=exit_price,
                        initial_qty=pos.initial_qty,
                        final_qty=pos.current_qty,
                        net_pnl=net_pnl,
                        return_pct=(net_pnl / cash_equity) * 100.0,
                        exit_reason=exit_reason,
                        was_pyramided=pos.is_pyramided,
                        hold_hours=hold_hours,
                        capital_after=cash_equity
                    )
                )
                del open_positions[sym]
                continue
                
            # If not stopped out, check Pyramiding trigger (+2.0 ATR)
            if not pos.is_pyramided:
                if pos.direction == 1:
                    fav_dist = pos.highest_price - pos.entry_price
                    if fav_dist >= 2.0 * pos.initial_atr:
                        # Breakeven stop with fee buffer
                        be_stop = pos.entry_price * (1.0 + 2.0 * TAKER_FEE + 2.0 * SLIPPAGE)
                        if be_stop > pos.current_stop:
                            pos.current_stop = be_stop
                        pos.is_breakeven_set = True
                        
                        # Add +50% volume
                        add_qty = pos.initial_qty * 0.5
                        rules = INSTRUMENT_RULES.get(sym, {"step": 0.01, "minQty": 0.01})
                        add_qty = np.floor(add_qty / rules["step"]) * rules["step"]
                        if add_qty >= rules["minQty"]:
                            pos.current_qty += add_qty
                            pos.is_pyramided = True
                            fee = add_qty * b_close * TAKER_FEE
                            cash_equity -= fee
                else:
                    fav_dist = pos.entry_price - pos.lowest_price
                    if fav_dist >= 2.0 * pos.initial_atr:
                        be_stop = pos.entry_price * (1.0 - 2.0 * TAKER_FEE - 2.0 * SLIPPAGE)
                        if be_stop < pos.current_stop:
                            pos.current_stop = be_stop
                        pos.is_breakeven_set = True
                        
                        add_qty = pos.initial_qty * 0.5
                        rules = INSTRUMENT_RULES.get(sym, {"step": 0.01, "minQty": 0.01})
                        add_qty = np.floor(add_qty / rules["step"]) * rules["step"]
                        if add_qty >= rules["minQty"]:
                            pos.current_qty += add_qty
                            pos.is_pyramided = True
                            fee = add_qty * b_close * TAKER_FEE
                            cash_equity -= fee
                            
            # Ratchet Trailing Stop (2.5 ATR trailing)
            if pos.direction == 1:
                trail_stop = pos.highest_price - 2.5 * b_atr
                if trail_stop > pos.current_stop:
                    pos.current_stop = trail_stop
            else:
                trail_stop = pos.lowest_price + 2.5 * b_atr
                if trail_stop < pos.current_stop:
                    pos.current_stop = trail_stop
                    
            # 8h Funding fee deduction
            if curr_ts % (8 * 3600 * 1000) == 0:
                notional = pos.current_qty * b_close
                funding_fee = notional * FUNDING_8H_RATE
                cash_equity -= funding_fee

        # 2. Scanner: Identify Candidates at Bar t Close
        candidates = []
        long_count = sum(1 for p in open_positions.values() if p.direction == 1)
        short_count = sum(1 for p in open_positions.values() if p.direction == -1)
        
        for sym in SYMBOLS:
            if sym in open_positions:
                continue
            df_sym = data_by_sym.get(sym)
            if df_sym is None or curr_ts not in df_sym.index:
                continue
                
            bar = df_sym.loc[curr_ts]
            b_close = float(bar["close"])
            b_up = float(bar["upper_120"])
            b_low = float(bar["lower_120"])
            b_ema50 = float(bar["ema_50"])
            b_ema200 = float(bar["ema_200"])
            b_atr = float(bar["atr_14"])
            b_vol_ratio = float(bar["vol_ratio"])
            
            # Long Breakout Check
            if b_close > b_up and b_ema50 > b_ema200 and b_vol_ratio >= 1.2:
                if long_count < 2:
                    tms = abs(b_close - b_ema50) / (b_atr + 1e-6) * b_vol_ratio
                    candidates.append((sym, 1, tms, b_atr))
                    
            # Short Breakout Check
            elif b_close < b_low and b_ema50 < b_ema200 and b_vol_ratio >= 1.2:
                if short_count < 2:
                    tms = abs(b_close - b_ema50) / (b_atr + 1e-6) * b_vol_ratio
                    candidates.append((sym, -1, tms, b_atr))
                    
        # Rank by TMS Score
        candidates.sort(key=lambda x: x[2], reverse=True)
        
        # 3. Execute Signals strictly at Bar t+1 Open (Zero Lookahead)
        for cand in candidates:
            sym, direction, tms, atr = cand
            if direction == 1 and long_count >= 2:
                continue
            if direction == -1 and short_count >= 2:
                continue
                
            df_sym = data_by_sym.get(sym)
            if next_ts not in df_sym.index:
                continue
                
            next_bar = df_sym.loc[next_ts]
            open_p = float(next_bar["open"])
            
            # Sizing calculation
            risk_pct = 0.0075 if is_kill_switch else 0.025
            risk_budget = cash_equity * risk_pct
            stop_dist = 2.5 * atr
            if stop_dist <= 0:
                continue
                
            raw_qty = risk_budget / stop_dist
            
            # Leverage cap: notional <= 3.5 * equity
            max_qty = (cash_equity * 3.5) / open_p
            raw_qty = min(raw_qty, max_qty)
            
            rules = INSTRUMENT_RULES.get(sym, {"step": 0.01, "minQty": 0.01, "minNotional": 5.0})
            qty = np.floor(raw_qty / rules["step"]) * rules["step"]
            
            if qty < rules["minQty"] or (qty * open_p) < rules["minNotional"]:
                continue
                
            # Fill with Slippage at t+1 Open
            fill_price = open_p * (1.0 + SLIPPAGE) if direction == 1 else open_p * (1.0 - SLIPPAGE)
            stop_price = fill_price - stop_dist if direction == 1 else fill_price + stop_dist
            
            entry_fee = qty * fill_price * TAKER_FEE
            cash_equity -= entry_fee
            
            open_positions[sym] = Position(
                symbol=sym,
                direction=direction,
                entry_bar_idx=i + 1,
                entry_ts=next_ts,
                entry_price=fill_price,
                initial_qty=qty,
                current_qty=qty,
                current_stop=stop_price,
                initial_atr=atr,
                highest_price=fill_price,
                lowest_price=fill_price,
                is_pyramided=False,
                is_breakeven_set=False,
            )
            if direction == 1:
                long_count += 1
            else:
                short_count += 1
                
        # 4. Record Total Portfolio Equity at bar close
        unrealized = 0.0
        for sym, pos in open_positions.items():
            df_sym = data_by_sym.get(sym)
            if curr_ts in df_sym.index:
                c_p = float(df_sym.loc[curr_ts, "close"])
                unrealized += (pos.current_qty * (c_p - pos.entry_price)) if pos.direction == 1 else (pos.current_qty * (pos.entry_price - c_p))
                
        total_equity = cash_equity + unrealized
        if total_equity > peak_equity:
            peak_equity = total_equity
        dd = (peak_equity - total_equity) / peak_equity * 100.0 if peak_equity > 0 else 0.0
        if dd > max_drawdown:
            max_drawdown = dd
            
        equity_curve.append((curr_ts, total_equity))

    # Compile Final Metrics
    final_equity = cash_equity
    total_net_pnl = final_equity - initial_capital
    return_pct = (total_net_pnl / initial_capital) * 100.0
    
    wins = [t for t in closed_trades if t.net_pnl > 0]
    losses = [t for t in closed_trades if t.net_pnl <= 0]
    win_rate = (len(wins) / len(closed_trades) * 100.0) if closed_trades else 0.0
    
    gross_profit = sum(t.net_pnl for t in wins)
    gross_loss = abs(sum(t.net_pnl for t in losses))
    profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 999.0
    
    avg_win = (gross_profit / len(wins)) if wins else 0.0
    avg_loss = (gross_loss / len(losses)) if losses else 0.0
    payoff_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0
    
    pyramided_trades = [t for t in closed_trades if t.was_pyramided]
    
    return {
        "initial_capital": initial_capital,
        "final_equity": final_equity,
        "peak_equity": peak_equity,
        "total_net_pnl": total_net_pnl,
        "return_pct": return_pct,
        "max_drawdown_pct": max_drawdown,
        "total_trades": len(closed_trades),
        "wins_count": len(wins),
        "losses_count": len(losses),
        "win_rate_pct": win_rate,
        "profit_factor": profit_factor,
        "payoff_ratio": payoff_ratio,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "pyramided_trades_count": len(pyramided_trades),
        "closed_trades": closed_trades,
        "equity_curve": equity_curve,
    }

if __name__ == "__main__":
    t0 = time.time()
    results = run_backtest_2024_2026(initial_capital=300.0)
    elapsed = time.time() - t0
    
    print("\n" + "="*60)
    print("  BYBIT MULTI-ALPHA V2.0: FULL AUDIT BACKTEST (2024 - 2026)  ")
    print("="*60)
    print(f"Initial Capital:        ${results['initial_capital']:.2f}")
    print(f"Final Capital:          ${results['final_equity']:.2f}")
    print(f"Peak Capital:           ${results['peak_equity']:.2f}")
    print(f"Total Net PnL:          ${results['total_net_pnl']:+.2f} ({results['return_pct']:+.2f}%)")
    print(f"Max Drawdown:           {results['max_drawdown_pct']:.2f}%")
    print(f"Profit Factor:          {results['profit_factor']:.2f}")
    print(f"Payoff Ratio:           {results['payoff_ratio']:.2f} : 1")
    print(f"Win Rate:               {results['win_rate_pct']:.2f}% ({results['wins_count']} wins / {results['losses_count']} losses)")
    print(f"Total Closed Trades:    {results['total_trades']}")
    print(f"Pyramided Add Trades:   {results['pyramided_trades_count']}")
    print(f"Simulation Time:        {elapsed:.2f}s")
    print("="*60)
