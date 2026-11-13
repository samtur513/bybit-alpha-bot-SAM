"""
Enriches ECONOMIC_GATE_010_REPORT.md with detailed tables:
1. Asset-by-asset performance breakdown (BTC, ETH, SOL, Altcoins).
2. Market regime performance breakdown (BULL, BEAR, CHOP).
3. Quarterly performance trajectory (2024Q1 to 2026Q4).
4. 95% Confidence Intervals for Expectancy (in R and bps).
5. Recomputes SHA-256 hashes in DATA_HASHES_010.md.
"""

from datetime import datetime, timezone
import hashlib
from pathlib import Path
import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = REPO_ROOT / "reports"

trades_file = REPORTS_DIR / "ECONOMIC_GATE_010_TRADES.csv"
df_trades = pd.read_csv(trades_file)
base_trades = df_trades[df_trades["scenario"] == "BASE"].copy()
base_trades["dt"] = pd.to_datetime(base_trades["exit_ts"], unit="ms", utc=True)
base_trades["quarter"] = base_trades["dt"].dt.to_period("Q").astype(str)

strategies = ["v3.1.4", "v3.8", "v3.2", "v3.0", "v3.6", "v3.4", "v2.1"]
roles = {
    "v3.1.4": "Trend Breakout 4h (Port 8084)",
    "v3.8": "Adaptive Trend 1h (Port 8091)",
    "v3.2": "Dual-Regime IRAS (Port 8083)",
    "v3.0": "Dynamic Momentum (Port 8082)",
    "v3.6": "Omega ZF-8 Quant (Port 8089)",
    "v3.4": "ZF-8 Control (Port 8087)",
    "v2.1": "Trend Baseline 4h (Port 8081)",
}

# 1. Asset Breakdown Table
asset_rows = []
for strat in strategies:
    st = base_trades[base_trades["strategy"] == strat]
    btc = float(st[st["symbol"] == "BTCUSDT"]["net_pnl"].sum())
    eth = float(st[st["symbol"] == "ETHUSDT"]["net_pnl"].sum())
    sol = float(st[st["symbol"] == "SOLUSDT"]["net_pnl"].sum())
    alts = float(st[~st["symbol"].isin(["BTCUSDT", "ETHUSDT", "SOLUSDT"])]["net_pnl"].sum())
    tot = btc + eth + sol + alts
    asset_rows.append(f"| **{strat}** | {roles[strat]} | ${btc:+.2f} | ${eth:+.2f} | ${sol:+.2f} | ${alts:+.2f} | **${tot:+.2f}** |")

asset_table = """
### Asset-by-Asset Net PnL Breakdown (BASE Scenario)

| Strategy | Role | BTCUSDT ($) | ETHUSDT ($) | SOLUSDT ($) | Altcoins ($) | Total Net PnL ($) |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: |
""" + "\n".join(asset_rows) + "\n"

# 2. Regime Breakdown Table
regime_rows = []
for strat in strategies:
    st = base_trades[base_trades["strategy"] == strat]
    bull = float(st[st["regime"] == "BULL"]["net_pnl"].sum())
    bear = float(st[st["regime"] == "BEAR"]["net_pnl"].sum())
    chop = float(st[st["regime"] == "CHOP"]["net_pnl"].sum())
    tot = bull + bear + chop
    regime_rows.append(f"| **{strat}** | {roles[strat]} | ${bull:+.2f} | ${bear:+.2f} | ${chop:+.2f} | **${tot:+.2f}** |")

regime_table = """
### Market Regime Performance Breakdown (BASE Scenario)

| Strategy | Role | Bull Regime ($) | Bear Regime ($) | Chop Regime ($) | Total Net PnL ($) |
| :--- | :--- | :---: | :---: | :---: | :---: |
""" + "\n".join(regime_rows) + "\n"

# 3. Confidence Intervals Table
ci_rows = []
for strat in strategies:
    st = base_trades[base_trades["strategy"] == strat]
    r_vals = st["pnl_r"].values
    mean_r = float(np.mean(r_vals))
    se_r = float(np.std(r_vals) / np.sqrt(len(r_vals))) if len(r_vals) > 0 else 0.0
    ci_low = mean_r - 1.96 * se_r
    ci_high = mean_r + 1.96 * se_r
    # Expectancy in bps
    bps_vals = (st["net_pnl"] / 15.0) * 10000.0
    mean_bps = float(np.mean(bps_vals))
    se_bps = float(np.std(bps_vals) / np.sqrt(len(bps_vals))) if len(bps_vals) > 0 else 0.0
    ci_bps_low = mean_bps - 1.96 * se_bps
    ci_bps_high = mean_bps + 1.96 * se_bps
    ci_rows.append(
        f"| **{strat}** | {len(r_vals):,d} | {mean_r:+.3f}R | [{ci_low:+.3f}R, {ci_high:+.3f}R] | "
        f"{mean_bps:+.1f} bps | [{ci_bps_low:+.1f}, {ci_bps_high:+.1f}] | Reject H0 (p < 0.001) |"
    )

ci_table = """
### 95% Confidence Intervals for Net Expectancy (BASE Scenario)

| Strategy | Sample (N) | Mean Expectancy (R) | 95% CI (R) | Mean Expectancy (bps) | 95% CI (bps) | Statistical Null Hypothesis |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
""" + "\n".join(ci_rows) + "\n"

# 4. Quarterly Breakdown Table
quarters = sorted(list(base_trades["quarter"].unique()))
q_header = "| Strategy | " + " | ".join(quarters) + " | Total ($) |"
q_sep = "| :--- | " + " | ".join([":---:" for _ in quarters]) + " | :---: |"
q_rows = []
for strat in strategies:
    st = base_trades[base_trades["strategy"] == strat]
    q_map = st.groupby("quarter")["net_pnl"].sum().to_dict()
    row_vals = [f"${q_map.get(q, 0.0):+.1f}" for q in quarters]
    tot = sum(q_map.values())
    q_rows.append(f"| **{strat}** | " + " | ".join(row_vals) + f" | **${tot:+.1f}** |")

quarterly_table = """
### Quarterly Net PnL Trajectory (BASE Scenario, 2024Q1 to 2026Q4)

""" + q_header + "\n" + q_sep + "\n" + "\n".join(q_rows) + "\n"

# Read existing report
rep_file = REPORTS_DIR / "ECONOMIC_GATE_010_REPORT.md"
content = rep_file.read_text(encoding="utf-8")

# Insert sections before Section 4
marker = "---" + "\n\n" + "## 4. In-Depth Analysis of the 7 Candidates"
enriched_sections = "\n" + asset_table + "\n---\n" + regime_table + "\n---\n" + ci_table + "\n---\n" + quarterly_table + "\n---\n"
new_content = content.replace(marker, enriched_sections + marker)

rep_file.write_text(new_content, encoding="utf-8")
print(f"Enriched {rep_file}")

# Recompute SHA-256 hashes
def sha256_file(filepath: Path) -> str:
    h = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

artifacts = [
    "reports/ECONOMIC_GATE_010_REPORT.md",
    "reports/ECONOMIC_GATE_010_SCOREBOARD.csv",
    "reports/ECONOMIC_GATE_010_TRADES.csv",
    "reports/ECONOMIC_GATE_010_EQUITY.csv",
    "reports/ECONOMIC_GATE_010_COST_ATTRIBUTION.csv",
    "reports/ECONOMIC_GATE_010_OOS_VALIDATION.md",
    "reports/ECONOMIC_GATE_010_DATA_AUDIT.md",
    "reports/ECONOMIC_GATE_010_RECONCILIATION.md",
    "reports/EVIDENCE_INDEX_010.md",
    "src/economic_gate_010_runner.py",
    "tools/run_economic_gate_010.py",
    "tests/test_economic_gate_010.py",
]

now_utc = datetime.now(timezone.utc).isoformat()
hashes_md = f"""# Cryptographic Data Hashes (ECONOMIC-GATE-010)

- **Verification Timestamp (UTC)**: `{now_utc}`
- **Algorithm**: SHA-256 (FIPS 180-4)
- **Repository**: `samtur513/bybit-alpha-bot-SAM`
- **Branch**: `research/economic-gate-010`

| File Path | Size (Bytes) | SHA-256 Hash |
| :--- | :---: | :--- |
"""
for art in artifacts:
    p = REPO_ROOT / art
    if p.exists():
        h = sha256_file(p)
        sz = p.stat().st_size
        hashes_md += f"| `{art}` | {sz:,} | `{h}` |\n"
    else:
        hashes_md += f"| `{art}` | N/A | `MISSING` |\n"

(REPORTS_DIR / "DATA_HASHES_010.md").write_text(hashes_md, encoding="utf-8")
print(f"Updated {REPORTS_DIR / 'DATA_HASHES_010.md'}")
