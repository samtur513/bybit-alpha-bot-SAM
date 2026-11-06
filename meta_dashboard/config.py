"""
Configuration for Bybit Multi-Alpha Meta-Dashboard (Port 8085).
Monitors and aggregates all 13 active production engines (v3.0 Production Fleet):
1. Port 8080: v3.13 Sector Arb (Beta-Neutral)
2. Port 8083: v3.2 Dual-Regime (Donchian Trend + Calibrated IRAS Maker)
3. Port 8084: v3.1.4 Champion 4h Breakout
4. Port 8086: v3.3 Turbo Risk (Adaptive Streak Gearing 1.5%-3.2%)
5. Port 8087: v3.4 ZF-8 Quant (8-Speed Transmission G1-G8)
6. Port 8088: v3.5 Alpha Pairs (Market-Neutral CAR-CSMOM Top 2 vs Bottom 2)
7. Port 8089: v3.6 Omega ZF-8 Dual-Regime
8. Port 8090: v3.7 Master Sweeps (Liquidity Sweep + Anti-Compression + OBI Guard)
9. Port 8091: v3.8 NewDragon Trend (True 1R Sizer + 4-Stage Exit)
10. Port 8092: v3.9 NewDragon Scalp (Pure Lead-Lag Beta-Catchup to BTC)
11. Port 8094: v3.14 Basket Dispersion (BTC+ETH Benchmark Dispersion)
12. Port 8096: v3.12 Cross-Basis (Spot-Perp Basis Dislocation)
13. Port 8097: v3.11 Funding Squeeze (|FR| >= 0.04% Squeeze)
"""

import os

META_PORT = int(os.environ.get("META_PORT", 8085))
POLL_INTERVAL_SECONDS = float(os.environ.get("META_POLL_INTERVAL", 2.0))

ENGINES = [
    {
        "id": "v313",
        "name": "v3.13 Sector Arb",
        "tag": "Intra-Sector Spike Reversion (3-8m)",
        "version": "3.13",
        "timeframe": "3-8m",
        "port": 8080,
        "strategy": "Beta-Neutral Sector Pairs + 2-Stage Exit + News v1.2",
        "color": "#06b6d4",  # cyan
    },
    {
        "id": "v32",
        "name": "v3.2 Dual-Regime",
        "tag": "Dual Trend + IRAS Range Maker + News v1.2",
        "version": "3.2",
        "timeframe": "1h",
        "port": 8083,
        "strategy": "Dual-Regime Hybrid (Donchian Trend + Calibrated IRAS Maker + News Shield)",
        "color": "#f59e0b",  # amber
    },
    {
        "id": "v314",
        "name": "v3.1.4 Champion 4h",
        "tag": "4h Breakout Champion + News v1.2",
        "version": "3.1.4",
        "timeframe": "4h",
        "port": 8084,
        "strategy": "Dynamic Universe Screener + 4h Donchian + News Core v1.2",
        "color": "#00f2fe",  # cyan
    },
    {
        "id": "v33",
        "name": "v3.3 Turbo Risk",
        "tag": "1h Dynamic + Streak Adaptive R:R (1:3-1:5)",
        "version": "3.3",
        "timeframe": "1h",
        "port": 8086,
        "strategy": "Adaptive Streak Gearing (1.5%-3.2%) + Dynamic R:R (1:3-1:5)",
        "color": "#ec4899",  # pink / magenta
    },
    {
        "id": "v34",
        "name": "v3.4 ZF-8 Quant",
        "tag": "1h Dynamic + 8-Speed Intelligent Transmission",
        "version": "3.4",
        "timeframe": "1h",
        "port": 8087,
        "strategy": "8-Speed ZF Transmission (1.0%-3.6% Smart Kickdown)",
        "color": "#8b5cf6",  # violet
    },
    {
        "id": "v35",
        "name": "v3.5 Alpha Pairs",
        "tag": "1h Market-Neutral Long/Short Pairs (Beta ~ 0)",
        "version": "3.5",
        "timeframe": "1h",
        "port": 8088,
        "strategy": "Market-Neutral CAR-CSMOM (168h vs BTC) + Hysteresis Buffer",
        "color": "#10b981",  # emerald green
    },
    {
        "id": "v36",
        "name": "v3.6 Omega ZF-8",
        "tag": "1h Dual-Regime + 8-Speed ZF + News Core v1.2",
        "version": "3.6",
        "timeframe": "1h",
        "port": 8089,
        "strategy": "Dual-Regime Hybrid (ZF-8 Transmission + IRAS Maker + News Guard)",
        "color": "#f59e0b",  # gold / amber
    },
    {
        "id": "v37",
        "name": "v3.7 Master Sweeps",
        "tag": "15m/M1 Sweeps + Anti-Compression + OBI Guard",
        "version": "3.7.1",
        "timeframe": "15m/M1",
        "port": 8090,
        "strategy": "Liquidity Sweep Reversion (Anti-Compression Gate) + OBI Gate (KER >= 0.30)",
        "color": "#ec4899",  # hot pink
    },
    {
        "id": "v38",
        "name": "v3.8 NewDragon Trend",
        "tag": "1h Trend + 400+ Dynamic Universe + NewDragon v4",
        "version": "3.8",
        "timeframe": "1h",
        "port": 8091,
        "strategy": "True 1R Sizer + 4-Stage Exit FSM (Maker TP 60% + Fast Scratch) + News v1.2",
        "color": "#6366f1",  # indigo
    },
    {
        "id": "v39",
        "name": "v3.9 NewDragon Scalp",
        "tag": "15m/M1 L2 Microstructure + OFI Scratch + Micro-Router",
        "version": "3.9",
        "timeframe": "15m/M1",
        "port": 8092,
        "strategy": "Micro-Account Router (minNotional) + Order Flow Imbalance Z_OFI Scratch + NewDragon v4",
        "color": "#8b5cf6",  # violet
    },
    {
        "id": "v314_dispersion",
        "name": "v3.14 Basket Dispersion",
        "tag": "Synthetic Basket + Funding Carry (5-30m)",
        "version": "3.14",
        "timeframe": "5-30m",
        "port": 8094,
        "strategy": "BTC+ETH Benchmark Dispersion + Funding Carry + 2-Stage Exit",
        "color": "#10b981",  # emerald green
    },
    {
        "id": "v312",
        "name": "v3.12 Cross-Basis",
        "tag": "Spot-Perp OFI Lead-Lag Scalper",
        "version": "3.12",
        "timeframe": "M1",
        "port": 8096,
        "strategy": "Spot-Perp Basis Dislocation (>= 3.5 bps) + Spot OFI Lead-Lag",
        "color": "#a855f7",  # purple
    },
    {
        "id": "v311_funding",
        "name": "v3.11 Funding Squeeze",
        "tag": "Pre-Funding Squeeze + T-1s Eject",
        "version": "3.11",
        "timeframe": "15m",
        "port": 8097,
        "strategy": "Extreme Funding Rate Squeeze (|FR| >= 0.04%) + Clearing Dump Defense",
        "color": "#f59e0b",  # amber
    },
    {
        "id": "v3_v4_exp",
        "name": "v3-v4 NewDragon Trend",
        "tag": "1h/4h Trend + NewDragon v4 True 1R + FSM Exit",
        "version": "4.0.0-exp1",
        "timeframe": "1h/4h",
        "port": 8082,
        "strategy": "True 1R Sizer + 4-Stage Adaptive Exit FSM (Maker TP 40-70% + MFE Breakeven) + News Shield",
        "color": "#a855f7",  # purple
    },
    {
        "id": "v37_v4_exp",
        "name": "v3.7-v4 NewDragon Scalp",
        "tag": "M1/15m Scalp OMS + minNotional Guard + Z_OFI Scratch",
        "version": "4.0.0-exp2",
        "timeframe": "M1/15m",
        "port": 8093,
        "strategy": "UniversalRisk minNotional Guard + 2-Stage Z_OFI Fast Scratch + Dynamic Breakeven Ratchet",
        "color": "#ec4899",  # pink / magenta
    },
]
