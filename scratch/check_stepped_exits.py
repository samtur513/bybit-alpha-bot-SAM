"""Check why winners are cut short."""
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

df_1m_dict = {sym: pd.read_parquet(PARQUET_DATA_DIR / f"{sym}_1m.parquet") for sym in SYMBOLS}
funding_dict = {sym: pd.read_parquet(PARQUET_DATA_DIR / f"{sym}_funding.parquet") for sym in SYMBOLS}

downloader = DataDownloader()
df_1h_dict = {sym: downloader.aggregate_candles(df_1m_dict[sym], 1) for sym in SYMBOLS}
df_4h_dict = {sym: downloader.aggregate_candles(df_1m_dict[sym], 4) for sym in SYMBOLS}

param = StrategyParams(N=24, K=2.5, RSI_LONG_MIN=55, RSI_SHORT_MAX=45)
strat = StrategyV10(params=param)
prepared = {sym: strat.prepare_data(df_1m_dict[sym], df_1h_dict[sym], df_4h_dict[sym]) for sym in SYMBOLS}

sim = MarketSimulator(strategy_params=param, initial_capital=Decimal("300.0"))
res = sim.run(df_1m_dict, prepared, funding_dict, DEV_START_TS, DEV_END_TS, PERIOD_ENTRY_CUTOFF_MS)

print("Trades with partial TP vs without:")
with_tp = [t for t in res.trades if t.partial_tp_pnl > 0]
without_tp = [t for t in res.trades if t.partial_tp_pnl == 0]
print(f"Trades with partial TP: {len(with_tp)}, total PnL=${sum(float(t.net_pnl) for t in with_tp):.2f}, avg=${sum(float(t.net_pnl) for t in with_tp)/len(with_tp) if with_tp else 0:.3f}")
print(f"Trades without partial TP: {len(without_tp)}, total PnL=${sum(float(t.net_pnl) for t in without_tp):.2f}, avg=${sum(float(t.net_pnl) for t in without_tp)/len(without_tp) if without_tp else 0:.3f}")

# Look at holding time and return of top winners
sorted_trades = sorted(res.trades, key=lambda t: float(t.net_pnl), reverse=True)
print("\nTop 10 winners:")
for t in sorted_trades[:10]:
    print(f"  #{t.trade_id} {t.direction} {t.symbol}: PnL=${float(t.net_pnl):.2f}, hold={t.hold_hours:.1f}h, partial_tp=${float(t.partial_tp_pnl):.2f}, exit={t.exit_reason}")

print("\nTop 10 losers:")
for t in sorted_trades[-10:]:
    print(f"  #{t.trade_id} {t.direction} {t.symbol}: PnL=${float(t.net_pnl):.2f}, hold={t.hold_hours:.1f}h, exit={t.exit_reason}")
