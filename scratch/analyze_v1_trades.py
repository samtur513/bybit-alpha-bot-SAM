"""Deep analysis of v1.0 trades on Dev period."""
import sys
sys.path.insert(0, ".")
from decimal import Decimal
import pandas as pd

from src.config import (
    DEV_START_TS,
    DEV_END_TS,
    PARQUET_DATA_DIR,
    PERIOD_ENTRY_CUTOFF_MS,
    SYMBOLS,
    StrategyParams,
)
from src.data_downloader import DataDownloader
from src.strategy import StrategyV10
from src.simulator import MarketSimulator

df_1m_dict = {}
funding_dict = {}
for sym in SYMBOLS:
    df_1m_dict[sym] = pd.read_parquet(PARQUET_DATA_DIR / f"{sym}_1m.parquet")
    f_path = PARQUET_DATA_DIR / f"{sym}_funding.parquet"
    funding_dict[sym] = pd.read_parquet(f_path) if f_path.exists() else pd.DataFrame()

downloader = DataDownloader()
df_1h_dict = {}
df_4h_dict = {}
for sym in SYMBOLS:
    df_1h_dict[sym] = downloader.aggregate_candles(df_1m_dict[sym], 1)
    df_4h_dict[sym] = downloader.aggregate_candles(df_1m_dict[sym], 4)

param = StrategyParams(N=24, K=2.5, RSI_LONG_MIN=55, RSI_SHORT_MAX=45)
strat = StrategyV10(params=param)
prepared = {}
for sym in SYMBOLS:
    prepared[sym] = strat.prepare_data(df_1m_dict[sym], df_1h_dict[sym], df_4h_dict[sym])

sim = MarketSimulator(strategy_params=param, initial_capital=Decimal("300.0"))
res = sim.run(
    df_1m_dict,
    prepared,
    funding_dict,
    start_ts=DEV_START_TS,
    end_ts=DEV_END_TS,
    entry_cutoff_ms=PERIOD_ENTRY_CUTOFF_MS,
)

trades = res.trades
print(f"Total trades: {len(trades)}")
wins = [float(t.net_pnl) for t in trades if t.net_pnl > 0]
losses = [float(t.net_pnl) for t in trades if t.net_pnl <= 0]

print(f"Win rate: {len(wins)/len(trades)*100:.1f}%")
print(f"Avg win:  ${sum(wins)/len(wins):.3f}" if wins else "No wins")
print(f"Avg loss: ${sum(losses)/len(losses):.3f}" if losses else "No losses")
r_r = (sum(wins)/len(wins)) / abs(sum(losses)/len(losses)) if (wins and losses) else 0
print(f"Reward/Risk ratio: {r_r:.3f}")
req_wr = (1 / (1 + r_r)) * 100 if r_r > 0 else 0
print(f"Required WR for breakeven: {req_wr:.1f}% vs actual {len(wins)/len(trades)*100:.1f}%")

total_entry_fees = sum(float(t.entry_fee) for t in trades)
total_exit_fees = sum(float(t.exit_fee) for t in trades)
total_funding = sum(float(t.funding_paid) for t in trades)
total_costs = total_entry_fees + total_exit_fees + total_funding
gross_pnl = float(res.net_profit) + total_costs
print(f"Gross PnL: ${gross_pnl:.2f}")
print(f"Total costs: ${total_costs:.2f} (Entry: ${total_entry_fees:.2f}, Exit: ${total_exit_fees:.2f}, Funding: ${total_funding:.2f})")
print(f"Net PnL: ${float(res.net_profit):.2f}")

# Exit reasons
from collections import Counter
reasons = Counter(t.exit_reason for t in trades)
print("\nExit reasons:")
for r, count in reasons.most_common():
    sub_trades = [t for t in trades if t.exit_reason == r]
    sub_pnl = sum(float(t.net_pnl) for t in sub_trades)
    sub_wr = sum(1 for t in sub_trades if t.net_pnl > 0) / count * 100
    print(f"  {r}: {count} trades ({count/len(trades)*100:.1f}%), PnL=${sub_pnl:.2f}, avg=${sub_pnl/count:.3f}, WR={sub_wr:.1f}%")

# Direction x Symbol
dir_sym = Counter((t.direction, t.symbol) for t in trades)
print("\nDirection x Symbol:")
for (d, s), count in dir_sym.most_common():
    sub_trades = [t for t in trades if t.direction == d and t.symbol == s]
    sub_pnl = sum(float(t.net_pnl) for t in sub_trades)
    sub_wr = sum(1 for t in sub_trades if t.net_pnl > 0) / count * 100
    print(f"  {d} {s}: {count} trades, PnL=${sub_pnl:.2f}, WR={sub_wr:.1f}%")
