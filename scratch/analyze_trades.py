"""Deep trade analysis for v0.1 strategy failure diagnosis."""
import csv
import sys

trades = []
with open("reports/trades_continuous_300.csv", "r") as f:
    reader = csv.DictReader(f)
    for row in reader:
        trades.append({
            "id": int(row["trade_id"]),
            "symbol": row["symbol"],
            "direction": row["direction"],
            "entry_time": int(row["entry_time"]),
            "exit_time": int(row["exit_time"]),
            "entry_price": float(row["entry_price"]),
            "exit_price": float(row["exit_price"]),
            "qty": float(row["qty"]),
            "entry_fee": float(row["entry_fee"]),
            "exit_fee": float(row["exit_fee"]),
            "funding_paid": float(row["funding_paid"]),
            "net_pnl": float(row["net_pnl"]),
            "return_pct": float(row["return_pct"]),
            "exit_reason": row["exit_reason"],
            "hold_hours": float(row["hold_hours"]),
        })

print("=" * 70)
print("DEEP ANALYSIS OF v0.1 STRATEGY FAILURE")
print("=" * 70)

# 1. Cost analysis
total_entry_fees = sum(t["entry_fee"] for t in trades)
total_exit_fees = sum(t["exit_fee"] for t in trades)
total_funding = sum(t["funding_paid"] for t in trades)
total_costs = total_entry_fees + total_exit_fees + total_funding
gross_pnl = sum(t["net_pnl"] for t in trades) + total_costs

print(f"\n1. COST BREAKDOWN ({len(trades)} trades)")
print(f"   Gross PnL (before costs): ${gross_pnl:.2f}")
print(f"   Entry fees total:         ${total_entry_fees:.2f}")
print(f"   Exit fees total:          ${total_exit_fees:.2f}")
print(f"   Funding total:            ${total_funding:.2f}")
print(f"   Total costs:              ${total_costs:.2f}")
print(f"   Net PnL:                  ${sum(t['net_pnl'] for t in trades):.2f}")
print(f"   Cost per trade:           ${total_costs / len(trades):.4f}")
print(f"   Costs eat {abs(total_costs / gross_pnl * 100):.1f}% of gross PnL" if gross_pnl != 0 else "")

# 2. Hold time distribution
short_trades = [t for t in trades if t["hold_hours"] < 3]
med_trades = [t for t in trades if 3 <= t["hold_hours"] < 12]
long_trades = [t for t in trades if t["hold_hours"] >= 12]

print(f"\n2. HOLD TIME DISTRIBUTION")
for label, group in [("<3h", short_trades), ("3-12h", med_trades), (">12h", long_trades)]:
    if group:
        avg = sum(t["net_pnl"] for t in group) / len(group)
        wr = sum(1 for t in group if t["net_pnl"] > 0) / len(group) * 100
        print(f"   {label}: {len(group)} trades, avg PnL=${avg:.3f}, WR={wr:.1f}%")

# 3. Return distribution percentiles
returns = sorted([t["return_pct"] for t in trades])
n = len(returns)
print(f"\n3. RETURN DISTRIBUTION (% per trade)")
for pct in [5, 10, 25, 50, 75, 90, 95]:
    idx = int(n * pct / 100)
    print(f"   P{pct}: {returns[min(idx, n-1)]:.3f}%")
print(f"   Mean: {sum(returns)/len(returns):.3f}%")

# 4. Win streaks and loss streaks
max_win_streak = 0
max_loss_streak = 0
curr_win = 0
curr_loss = 0
for t in trades:
    if t["net_pnl"] > 0:
        curr_win += 1
        curr_loss = 0
    else:
        curr_loss += 1
        curr_win = 0
    max_win_streak = max(max_win_streak, curr_win)
    max_loss_streak = max(max_loss_streak, curr_loss)

print(f"\n4. STREAKS")
print(f"   Max win streak:  {max_win_streak}")
print(f"   Max loss streak: {max_loss_streak}")

# 5. False breakouts analysis  
# If a trade exits at stop_loss within 3 hours, likely a false breakout
false_breakouts = [t for t in trades if t["hold_hours"] < 3 and t["exit_reason"] == "STOP_LOSS" and t["net_pnl"] < 0]
print(f"\n5. FALSE BREAKOUT ANALYSIS (stop in <3h, loss)")
print(f"   False breakout trades: {len(false_breakouts)} ({len(false_breakouts)/len(trades)*100:.1f}% of all)")
print(f"   Total PnL from false breakouts: ${sum(t['net_pnl'] for t in false_breakouts):.2f}")

# 6. Best vs worst trades
trades_sorted = sorted(trades, key=lambda t: t["net_pnl"])
print(f"\n6. TOP 10 WORST TRADES")
for t in trades_sorted[:10]:
    print(f"   #{t['id']} {t['symbol']} {t['direction']}: PnL=${t['net_pnl']:.2f} ({t['return_pct']:.2f}%), hold={t['hold_hours']:.1f}h")
print(f"\n   TOP 10 BEST TRADES")
for t in trades_sorted[-10:]:
    print(f"   #{t['id']} {t['symbol']} {t['direction']}: PnL=${t['net_pnl']:.2f} ({t['return_pct']:.2f}%), hold={t['hold_hours']:.1f}h")

# 7. Monthly breakdown
from datetime import datetime, timezone
monthly = {}
for t in trades:
    dt = datetime.fromtimestamp(t["entry_time"] / 1000, tz=timezone.utc)
    month_key = dt.strftime("%Y-%m")
    if month_key not in monthly:
        monthly[month_key] = {"count": 0, "pnl": 0, "wins": 0}
    monthly[month_key]["count"] += 1
    monthly[month_key]["pnl"] += t["net_pnl"]
    if t["net_pnl"] > 0:
        monthly[month_key]["wins"] += 1

print(f"\n7. MONTHLY BREAKDOWN")
for month in sorted(monthly.keys()):
    m = monthly[month]
    wr = m["wins"] / m["count"] * 100 if m["count"] > 0 else 0
    print(f"   {month}: {m['count']} trades, PnL=${m['pnl']:.2f}, WR={wr:.1f}%")

# 8. Signal quality - what is the avg distance from breakout in winning vs losing?
print(f"\n8. KEY DIAGNOSIS SUMMARY")
print(f"   Win rate: {sum(1 for t in trades if t['net_pnl']>0)/len(trades)*100:.1f}%")
print(f"   Avg winner: ${sum(t['net_pnl'] for t in trades if t['net_pnl']>0)/max(1,sum(1 for t in trades if t['net_pnl']>0)):.3f}")
print(f"   Avg loser:  ${sum(t['net_pnl'] for t in trades if t['net_pnl']<=0)/max(1,sum(1 for t in trades if t['net_pnl']<=0)):.3f}")
avg_w = sum(t['net_pnl'] for t in trades if t['net_pnl']>0)/max(1,sum(1 for t in trades if t['net_pnl']>0))
avg_l = abs(sum(t['net_pnl'] for t in trades if t['net_pnl']<=0)/max(1,sum(1 for t in trades if t['net_pnl']<=0)))
print(f"   Reward/Risk ratio: {avg_w/avg_l:.3f}")
print(f"   Required WR for breakeven at this R:R: {1/(1+avg_w/avg_l)*100:.1f}%")
print(f"   Actual WR: {sum(1 for t in trades if t['net_pnl']>0)/len(trades)*100:.1f}%")
print(f"   WR GAP: {sum(1 for t in trades if t['net_pnl']>0)/len(trades)*100 - 1/(1+avg_w/avg_l)*100:.1f}% ({'PROFITABLE' if sum(1 for t in trades if t['net_pnl']>0)/len(trades) > 1/(1+avg_w/avg_l) else 'UNPROFITABLE'})")
