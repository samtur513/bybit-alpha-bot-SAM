"""Explore exit parameter combinations: breakeven trigger, TP distance, trail distance."""
import sys
sys.path.insert(0, ".")
from decimal import Decimal
import pandas as pd

from src.config import (
    DEV_START_TS,
    DEV_END_TS,
    VAL_START_TS,
    VAL_END_TS,
    FINAL_START_TS,
    FINAL_END_TS,
    PARQUET_DATA_DIR,
    PERIOD_ENTRY_CUTOFF_MS,
    SYMBOLS,
    StrategyParams,
)
from src.data_downloader import DataDownloader
from src.strategy import StrategyV10
from src.simulator import MarketSimulator
from src.research_protocol import compute_metrics

df_1m_dict = {sym: pd.read_parquet(PARQUET_DATA_DIR / f"{sym}_1m.parquet") for sym in SYMBOLS}
funding_dict = {sym: pd.read_parquet(PARQUET_DATA_DIR / f"{sym}_funding.parquet") for sym in SYMBOLS}

downloader = DataDownloader()
df_1h_dict = {sym: downloader.aggregate_candles(df_1m_dict[sym], 1) for sym in SYMBOLS}
df_4h_dict = {sym: downloader.aggregate_candles(df_1m_dict[sym], 4) for sym in SYMBOLS}

param = StrategyParams(N=24, K=2.0, RSI_LONG_MIN=55, RSI_SHORT_MAX=45)
strat = StrategyV10(params=param)
prepared = {sym: strat.prepare_data(df_1m_dict[sym], df_1h_dict[sym], df_4h_dict[sym]) for sym in SYMBOLS}

# Let's inspect the distribution of MFE (Maximum Favorable Excursion) of trades
# MFE = max profit in ATR units during the trade
sim = MarketSimulator(strategy_params=param, initial_capital=Decimal("300.0"))
res = sim.run(df_1m_dict, prepared, funding_dict, DEV_START_TS, DEV_END_TS, PERIOD_ENTRY_CUTOFF_MS)

print("Baseline N=24, K=2.0 on Dev:")
m = compute_metrics(res)
print(f"NetPnL: ${m['net_profit']:.2f}, PF: {m['profit_factor']:.2f}, Trades: {m['total_trades']}, WR: {m['win_rate']:.1f}%")

# Now let's see how many trades reached +1 ATR, +2 ATR, +3 ATR, +4 ATR
# Let's inspect trades
winners = [t for t in res.trades if t.net_pnl > 0]
breakevens = [t for t in res.trades if 0 < t.net_pnl < 0.5]
big_winners = [t for t in res.trades if t.net_pnl >= 2.0]
print(f"\nWinners distribution:")
print(f"  Total winners: {len(winners)}")
print(f"  Tiny winners ($0 - $0.50): {len(breakevens)} ({len(breakevens)/len(winners)*100:.1f}% of winners)")
print(f"  Medium winners ($0.50 - $2.00): {len(winners) - len(breakevens) - len(big_winners)}")
print(f"  Big winners (>= $2.00): {len(big_winners)} ({len(big_winners)/len(winners)*100:.1f}% of winners)")

losers = [t for t in res.trades if t.net_pnl <= 0]
print(f"\nLosers distribution:")
print(f"  Total losers: {len(losers)}")
print(f"  Avg loser: ${sum(float(t.net_pnl) for t in losers)/len(losers):.2f}")
print(f"  Full stops (< -$1.50): {sum(1 for t in losers if t.net_pnl < -1.5)}")
