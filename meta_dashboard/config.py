"""
Configuration for Bybit Multi-Alpha Meta-Dashboard (Port 8085).
Monitors and aggregates all 5 active engines:
- Port 8080: v2.0 (1h Baseline Breakout, 18 coins)
- Port 8081: v2.1 (4h Breakout + News Core v1.1, 18 coins)
- Port 8082: v3.1.1 (1h Breakout + Dynamic Universe + News Core v1.2)
- Port 8083: v3.2 (Dual-Regime Hybrid: Trend + Calibrated IRAS Range Maker)
- Port 8084: v3.1.4 (4h Breakout Champion + Dynamic Universe + News Core v1.2)
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
        "id": "v21",
        "name": "v2.1 Static 4h",
        "tag": "4h Breakout + News v1.2",
        "version": "2.1",
        "timeframe": "4h",
        "port": 8081,
        "strategy": "Donchian 30 bars (Static 18 Coins) + News Core v1.2",
        "color": "#38bdf8",  # sky blue
    },
    {
        "id": "v311",
        "name": "v3.1.1 Trend 1h",
        "tag": "1h Breakout + Dynamic Screener + News v1.2",
        "version": "3.1.1",
        "timeframe": "1h",
        "port": 8082,
        "strategy": "Dynamic Universe Screener + 1h Donchian + News Core v1.2",
        "color": "#a855f7",  # purple
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
        "name": "v3.7 HFT Scalper",
        "tag": "15m/M1 L2 Microstructure + Sweep + Lead-Lag",
        "version": "3.7",
        "timeframe": "15m/M1",
        "port": 8090,
        "strategy": "Orderbook L2 (50 levels) + Sweep Absorption + Lead-Lag Beta + News Guard",
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
        "id": "v310",
        "name": "v3.10 Sweep Pod",
        "tag": "15m Sweep Reversion + 4-Part TCA",
        "version": "3.10",
        "timeframe": "15m",
        "port": 8098,
        "strategy": "Liquidity Sweep Reversion + Anti-Compression + Post-Only Maker Reclaim",
        "color": "#06b6d4",  # cyan
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
        "id": "v314_dispersion",
        "name": "v3.14 Basket Dispersion",
        "tag": "Synthetic Basket + Funding Carry (5-30m)",
        "version": "3.14",
        "timeframe": "5-30m",
        "port": 8094,
        "strategy": "BTC+ETH Benchmark Dispersion + Funding Carry + 2-Stage Exit",
        "color": "#10b981",  # emerald green
    },
]
