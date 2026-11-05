"""
Master Meta-Dashboard HTTP Server & Web Application (Port 8085).
Provides institutional-grade monitoring and visual comparison of all 5 running trading engines.
"""

from datetime import datetime, timezone
import http.server
import json
import logging
from pathlib import Path
import socketserver
import sys
import threading
import time

BASE_DIR = Path(__file__).resolve().parent
PARENT_DIR = BASE_DIR.parent
for p in [str(PARENT_DIR), str(BASE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from meta_dashboard.config import ENGINES, META_PORT
from meta_dashboard.aggregator import MetaAggregator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("MetaDashboard")

AGGREGATOR: MetaAggregator = None

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha | Master Meta-Dashboard (13 Engines)</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-body: #070a13;
            --bg-card: #0d1322;
            --bg-card-hover: #131c31;
            --border: #1e293b;
            --border-light: #2d3b55;
            --accent-cyan: #00f2fe;
            --accent-blue: #38bdf8;
            --accent-purple: #a855f7;
            --accent-green: #10b981;
            --accent-red: #f43f5e;
            --accent-amber: #f59e0b;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --text-dim: #64748b;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background-color: var(--bg-body);
            color: var(--text-main);
            font-family: 'Inter', sans-serif;
            padding: 20px 24px;
            min-height: 100vh;
        }
        .container { max-width: 1720px; margin: 0 auto; }

        /* Top Header */
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 24px;
            flex-wrap: wrap;
            gap: 16px;
        }
        .logo-group {
            display: flex;
            align-items: center;
            gap: 16px;
        }
        .logo-badge {
            background: linear-gradient(135deg, #00f2fe 0%, #38bdf8 50%, #a855f7 100%);
            color: #070a13;
            font-weight: 800;
            font-size: 13px;
            padding: 7px 14px;
            border-radius: 8px;
            letter-spacing: 1px;
            font-family: 'JetBrains Mono', monospace;
            box-shadow: 0 0 20px rgba(0, 242, 254, 0.35);
        }
        .title { font-size: 22px; font-weight: 800; letter-spacing: -0.5px; }
        .subtitle { font-size: 13px; color: var(--text-muted); margin-top: 3px; }
        
        .header-stats {
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }
        .stat-pill {
            background: var(--bg-card);
            border: 1px solid var(--border);
            padding: 8px 16px;
            border-radius: 8px;
            display: flex;
            flex-direction: column;
            gap: 2px;
        }
        .stat-pill-label { font-size: 10px; color: var(--text-muted); text-transform: uppercase; font-weight: 600; letter-spacing: 0.5px; }
        .stat-pill-val { font-size: 15px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }

        /* Section Titles */
        .section-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 14px;
            margin-top: 24px;
        }
        .section-title {
            font-size: 14px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            color: var(--text-muted);
            display: flex;
            align-items: center;
            gap: 8px;
        }
        .section-title::before {
            content: "";
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: var(--accent-cyan);
            box-shadow: 0 0 10px var(--accent-cyan);
        }

        /* 5-Engine Faceoff Grid */
        .engines-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(310px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .engine-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 18px;
            display: flex;
            flex-direction: column;
            gap: 14px;
            transition: all 0.2s ease;
            position: relative;
            overflow: hidden;
        }
        .engine-card:hover {
            border-color: var(--border-light);
            background: var(--bg-card-hover);
            transform: translateY(-2px);
        }
        .engine-card-bar {
            position: absolute;
            top: 0; left: 0; right: 0;
            height: 4px;
        }
        .engine-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
        }
        .engine-name { font-size: 16px; font-weight: 700; }
        .engine-tag { font-size: 11px; color: var(--text-muted); margin-top: 2px; }
        .engine-port-btn {
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid var(--border);
            color: var(--accent-cyan);
            font-size: 11px;
            font-weight: 600;
            font-family: 'JetBrains Mono', monospace;
            padding: 4px 8px;
            border-radius: 6px;
            text-decoration: none;
            display: inline-flex;
            align-items: center;
            gap: 4px;
            transition: all 0.15s ease;
        }
        .engine-port-btn:hover {
            background: var(--accent-cyan);
            color: #070a13;
            border-color: var(--accent-cyan);
        }

        .engine-stats-row {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 10px;
            background: rgba(0, 0, 0, 0.25);
            padding: 12px;
            border-radius: 8px;
            border: 1px solid rgba(255, 255, 255, 0.03);
        }
        .engine-stat-item { display: flex; flex-direction: column; gap: 2px; }
        .engine-stat-label { font-size: 11px; color: var(--text-muted); }
        .engine-stat-value { font-size: 15px; font-weight: 700; font-family: 'JetBrains Mono', monospace; }

        /* Health Tags Matrix */
        .health-tags {
            display: flex;
            flex-wrap: wrap;
            gap: 6px;
        }
        .health-tag {
            font-size: 11px;
            padding: 4px 8px;
            border-radius: 6px;
            font-family: 'JetBrains Mono', monospace;
            display: inline-flex;
            align-items: center;
            gap: 5px;
        }
        .health-tag-good {
            background: rgba(16, 185, 129, 0.12);
            color: var(--accent-green);
            border: 1px solid rgba(16, 185, 129, 0.3);
        }
        .health-tag-warn {
            background: rgba(245, 158, 11, 0.12);
            color: var(--accent-amber);
            border: 1px solid rgba(245, 158, 11, 0.3);
        }
        .health-tag-off {
            background: rgba(244, 63, 94, 0.12);
            color: var(--accent-red);
            border: 1px solid rgba(244, 63, 94, 0.3);
        }
        .pulse-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: currentColor;
            display: inline-block;
            animation: pulse 1.8s infinite;
        }
        @keyframes pulse {
            0% { opacity: 0.4; }
            50% { opacity: 1; }
            100% { opacity: 0.4; }
        }

        /* Chart & Main Matrix Layout */
        .analytics-row {
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 20px;
            margin-bottom: 24px;
        }
        @media (max-width: 1200px) {
            .analytics-row { grid-template-columns: 1fr; }
        }
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
        }
        .chart-container {
            position: relative;
            height: 320px;
            width: 100%;
        }

        /* Tables */
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        th {
            text-align: left;
            color: var(--text-muted);
            font-weight: 600;
            padding: 10px 14px;
            border-bottom: 1px solid var(--border);
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        td {
            padding: 12px 14px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.04);
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
        }
        tr:hover td {
            background: rgba(255, 255, 255, 0.02);
        }
        .badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
        }
        .badge-long { background: rgba(16, 185, 129, 0.15); color: var(--accent-green); border: 1px solid rgba(16, 185, 129, 0.3); }
        .badge-short { background: rgba(244, 63, 94, 0.15); color: var(--accent-red); border: 1px solid rgba(244, 63, 94, 0.3); }
        .badge-engine { background: rgba(56, 189, 248, 0.15); color: var(--accent-blue); border: 1px solid rgba(56, 189, 248, 0.3); font-weight: 700; }
        .badge-range { background: rgba(245, 158, 11, 0.15); color: var(--accent-amber); border: 1px solid rgba(245, 158, 11, 0.3); }

        /* News Feed Item */
        .news-feed-list {
            display: flex;
            flex-direction: column;
            gap: 10px;
            max-height: 340px;
            overflow-y: auto;
            padding-right: 6px;
        }
        .news-item {
            background: rgba(0, 0, 0, 0.25);
            border: 1px solid rgba(255, 255, 255, 0.04);
            border-left: 3px solid var(--accent-cyan);
            border-radius: 6px;
            padding: 10px 12px;
            display: flex;
            flex-direction: column;
            gap: 4px;
        }
        .news-meta {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 11px;
            color: var(--text-dim);
            font-family: 'JetBrains Mono', monospace;
        }
        .news-headline { font-size: 12px; line-height: 1.4; color: var(--text-main); }
        
        .val-green { color: var(--accent-green); }
        .val-red { color: var(--accent-red); }
        .val-cyan { color: var(--accent-cyan); }
        .val-amber { color: var(--accent-amber); }
        .val-purple { color: var(--accent-purple); }
    </style>
</head>
<body>
    <div class="container">
        <!-- Top Header -->
        <header>
            <div class="logo-group">
                <div class="logo-badge">BYBIT V5 // META</div>
                <div>
                    <div class="title">Multi-Alpha Master Meta-Dashboard (13 Engines)</div>
                    <div class="subtitle">Централизованный мониторинг 9 квантовых движков, потоков Bybit WS и защитного щита News Core</div>
                </div>
            </div>
            <div class="header-stats">
                <div class="stat-pill">
                    <span class="stat-pill-label">Суммарный капитал</span>
                    <span class="stat-pill-val val-cyan" id="meta-total-equity">$2,400.00</span>
                </div>
                <div class="stat-pill">
                    <span class="stat-pill-label">Реализованный PnL</span>
                    <span class="stat-pill-val" id="meta-realized-pnl">+$0.00</span>
                </div>
                <div class="stat-pill">
                    <span class="stat-pill-label">Нереализованный PnL</span>
                    <span class="stat-pill-val" id="meta-unrealized-pnl">+$0.00</span>
                </div>
                <div class="stat-pill">
                    <span class="stat-pill-label">Активных позиций</span>
                    <span class="stat-pill-val" id="meta-open-count">0</span>
                </div>
                <div class="stat-pill">
                    <span class="stat-pill-label">Статус серверов</span>
                    <span class="stat-pill-val val-green" id="meta-engines-online">5 / 5 Active</span>
                </div>
                <div class="stat-pill">
                    <span class="stat-pill-label">Время UTC</span>
                    <span class="stat-pill-val" id="meta-clock" style="font-size: 13px;">--:--:--</span>
                </div>
            </div>
        </header>

        <!-- SECTION 1: 9-ENGINE INFRASTRUCTURE & HEALTH MATRIX -->
        <div class="section-header">
            <div class="section-title">Состояние и телеметрия 9 квантовых движков</div>
            <span style="font-size: 12px; color: var(--text-dim);">Автообновление каждые 2 сек</span>
        </div>
        <div class="engines-grid" id="engines-grid">
            <!-- Dynamic Render of 9 Engine Cards -->
        </div>

        <!-- SECTION 2: COMPARATIVE CHART & LIVE NEWS RADAR -->
        <div class="analytics-row">
            <div class="card">
                <div class="section-header" style="margin-top: 0; margin-bottom: 12px;">
                    <div class="section-title">Сравнительная динамика капитала (Live Multi-Engine Equity)</div>
                    <span style="font-size: 11px; color: var(--text-muted); font-family: 'JetBrains Mono';">Базовый старт: $300 на версию</span>
                </div>
                <div class="chart-container">
                    <canvas id="equityChart"></canvas>
                </div>
            </div>
            
            <div class="card">
                <div class="section-header" style="margin-top: 0; margin-bottom: 12px;">
                    <div class="section-title">Единый радар новостей News Core (v1.2 Shield)</div>
                    <span class="badge" style="background: rgba(0, 242, 254, 0.15); color: var(--accent-cyan); border: 1px solid rgba(0, 242, 254, 0.3);" id="news-count-badge">0 новостей</span>
                </div>
                <div class="news-feed-list" id="news-feed-list">
                    <div style="color: var(--text-muted); font-size: 12px; text-align: center; padding: 20px;">Ожидание новостных событий...</div>
                </div>
            </div>
        </div>

        <!-- SECTION 3: CONSOLIDATED OPEN POSITIONS & PENDING ORDERS -->
        <div class="section-header">
            <div class="section-title">Сводный реестр открытых позиций (Все версии)</div>
            <span style="font-size: 12px; color: var(--text-muted);" id="positions-header-count">0 позиций</span>
        </div>
        <div class="card" style="padding: 0; margin-bottom: 24px; overflow-x: auto;">
            <table>
                <thead>
                    <tr>
                        <th>Движок / Порт</th>
                        <th>Монета</th>
                        <th>Сторона</th>
                        <th>Модуль</th>
                        <th>Объём</th>
                        <th>Цена входа</th>
                        <th>Текущая цена</th>
                        <th>Стоп-лосс</th>
                        <th>Тейк-профит</th>
                        <th>Unrealized PnL ($ / %)</th>
                        <th>Статус позиции</th>
                    </tr>
                </thead>
                <tbody id="open-positions-body">
                    <tr><td colspan="11" style="text-align: center; color: var(--text-muted); padding: 24px;">Нет открытых позиций в текущий момент</td></tr>
                </tbody>
            </table>
        </div>

        <!-- SECTION 4: PENDING LIMIT ORDERS (IRAS RANGE MAKER) -->
        <div class="section-header">
            <div class="section-title">Ожидающие Maker-ордера (v3.2 IRAS Range Maker)</div>
            <span style="font-size: 12px; color: var(--text-muted);" id="orders-header-count">0 ордеров</span>
        </div>
        <div class="card" style="padding: 0; margin-bottom: 24px; overflow-x: auto;">
            <table>
                <thead>
                    <tr>
                        <th>Движок</th>
                        <th>Монета</th>
                        <th>Тип ордера</th>
                        <th>Сторона</th>
                        <th>Лимитная цена</th>
                        <th>Количество</th>
                        <th>Тейк-профит (SMA20)</th>
                        <th>Стоп-лосс</th>
                    </tr>
                </thead>
                <tbody id="pending-orders-body">
                    <tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 20px;">Нет активных отложенных ордеров</td></tr>
                </tbody>
            </table>
        </div>

        <!-- SECTION 5: RECENTLY CLOSED TRADES -->
        <div class="section-header">
            <div class="section-title">История закрытых сделок (Все версии)</div>
            <span style="font-size: 12px; color: var(--text-muted);" id="trades-header-count">0 закрытых сделок</span>
        </div>
        <div class="card" style="padding: 0; overflow-x: auto;">
            <table>
                <thead>
                    <tr>
                        <th>Движок</th>
                        <th>Монета</th>
                        <th>Сторона</th>
                        <th>Вход</th>
                        <th>Выход</th>
                        <th>Чистый PnL</th>
                        <th>Длительность</th>
                        <th>Причина закрытия</th>
                        <th>Пирамидинг</th>
                    </tr>
                </thead>
                <tbody id="closed-trades-body">
                    <tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 20px;">История закрытых сделок пока пуста</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        let chartInstance = null;

        function initChart() {
            const ctx = document.getElementById('equityChart').getContext('2d');
            chartInstance = new Chart(ctx, {
                type: 'line',
                data: { labels: [], datasets: [] },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    animation: { duration: 400 },
                    interaction: { mode: 'index', intersect: false },
                    plugins: {
                        legend: {
                            labels: {
                                color: '#94a3b8',
                                font: { family: 'JetBrains Mono', size: 11 }
                            }
                        },
                        tooltip: {
                            backgroundColor: '#0d1322',
                            titleColor: '#f8fafc',
                            bodyColor: '#94a3b8',
                            borderColor: '#1e293b',
                            borderWidth: 1,
                            padding: 10,
                            bodyFont: { family: 'JetBrains Mono', size: 12 }
                        }
                    },
                    scales: {
                        x: {
                            grid: { color: 'rgba(255, 255, 255, 0.04)' },
                            ticks: { color: '#64748b', font: { family: 'JetBrains Mono', size: 10 } }
                        },
                        y: {
                            grid: { color: 'rgba(255, 255, 255, 0.04)' },
                            ticks: {
                                color: '#64748b',
                                font: { family: 'JetBrains Mono', size: 10 },
                                callback: val => '$' + val.toFixed(2)
                            }
                        }
                    }
                }
            });
        }

        async function fetchStatus() {
            try {
                const res = await fetch('/api/meta_status');
                if (!res.ok) return;
                const data = await res.json();
                renderDashboard(data);
            } catch (err) {
                console.error('Fetch status error:', err);
            }
        }

        async function fetchChart() {
            try {
                const res = await fetch('/api/meta_chart');
                if (!res.ok) return;
                const data = await res.json();
                if (chartInstance && data.labels) {
                    chartInstance.data.labels = data.labels;
                    chartInstance.data.datasets = data.datasets;
                    chartInstance.update('none');
                }
            } catch (err) {
                console.error('Fetch chart error:', err);
            }
        }

        function renderDashboard(data) {
            // Header stats
            document.getElementById('meta-clock').textContent = data.timestamp_utc.split(' ')[1];
            document.getElementById('meta-total-equity').textContent = '$' + data.total_combined_equity.toFixed(2);
            
            const rPnlEl = document.getElementById('meta-realized-pnl');
            rPnlEl.textContent = (data.total_realized_pnl >= 0 ? '+' : '') + '$' + data.total_realized_pnl.toFixed(2);
            rPnlEl.className = 'stat-pill-val ' + (data.total_realized_pnl >= 0 ? 'val-green' : 'val-red');

            const uPnlEl = document.getElementById('meta-unrealized-pnl');
            uPnlEl.textContent = (data.total_unrealized_pnl >= 0 ? '+' : '') + '$' + data.total_unrealized_pnl.toFixed(2);
            uPnlEl.className = 'stat-pill-val ' + (data.total_unrealized_pnl >= 0 ? 'val-green' : 'val-red');

            document.getElementById('meta-open-count').textContent = data.total_open_positions;
            document.getElementById('meta-engines-online').textContent = data.online_engines;

            // Render 5 Engines Grid
            const gridEl = document.getElementById('engines-grid');
            gridEl.innerHTML = '';

            data.engines.forEach(eng => {
                const isOnline = eng.online;
                const netPnlVal = eng.net_pnl || 0;
                const retPctVal = eng.return_pct || 0;
                const pnlClass = netPnlVal >= 0 ? 'val-green' : 'val-red';
                const pnlSign = netPnlVal >= 0 ? '+' : '-';
                const pnlFormatted = `${pnlSign}$${Math.abs(netPnlVal).toFixed(2)}`;
                const retFormatted = `${retPctVal >= 0 ? '+' : ''}${retPctVal.toFixed(1)}%`;
                const wsClass = eng.bybit_connected ? 'health-tag-good' : 'health-tag-off';
                const newsClass = eng.news_defense_active ? 'health-tag-good' : 'health-tag-warn';

                const card = document.createElement('div');
                card.className = 'engine-card';
                card.innerHTML = `
                    <div class="engine-card-bar" style="background: ${eng.color};"></div>
                    <div class="engine-header">
                        <div>
                            <div class="engine-name" style="color: ${eng.color};">${eng.name}</div>
                            <div class="engine-tag">${eng.tag} (${eng.timeframe})</div>
                        </div>
                        <a href="http://${window.location.hostname}:${eng.port}" target="_blank" class="engine-port-btn">
                            :${eng.port} ↗
                        </a>
                    </div>
                    
                    <div class="engine-stats-row">
                        <div class="engine-stat-item">
                            <span class="engine-stat-label">Капитал (Equity)</span>
                            <span class="engine-stat-value">$${(eng.capital || 300).toFixed(2)}</span>
                        </div>
                        <div class="engine-stat-item">
                            <span class="engine-stat-label">Чистый PnL</span>
                            <span class="engine-stat-value ${pnlClass}">${pnlFormatted} (${retFormatted})</span>
                        </div>
                        <div class="engine-stat-item">
                            <span class="engine-stat-label">Макс. просадка</span>
                            <span class="engine-stat-value">${(eng.drawdown_pct || 0).toFixed(2)}%</span>
                        </div>
                        <div class="engine-stat-item">
                            <span class="engine-stat-label">Позиции / Плечо</span>
                            <span class="engine-stat-value val-cyan">${(eng.open_positions || []).length} поз.${(eng.id === 'v33' || eng.id === 'v34') ? ` (Gear ${eng.gear || 1})` : ''}</span>
                        </div>
                    </div>

                    <div class="health-tags">
                        <span class="health-tag ${wsClass}">
                            <span class="pulse-dot"></span>
                            Bybit WS: ${eng.bybit_connected ? 'OK (' + (eng.candles_ready || eng.monitored_coins || 0) + ' монет)' : 'DISCONNECTED'}
                        </span>
                        <span class="health-tag ${newsClass}">
                            🛡️ News Core: ${eng.news_defense_active ? 'v1.2 Shield' : 'Disabled'}
                        </span>
                        <span class="health-tag health-tag-good" style="font-size: 10px;">
                            ⚡ Ping: ${eng.latency_ms > 0 ? eng.latency_ms + 'ms' : 'N/A'}
                        </span>
                        <span class="health-tag" style="background: ${eng.btc_trend === 'BULLISH' ? 'rgba(16,185,129,0.15)' : (eng.btc_trend === 'BEARISH' ? 'rgba(239,68,68,0.15)' : 'rgba(255,255,255,0.05)')}; color: ${eng.btc_trend === 'BULLISH' ? 'var(--accent-green)' : (eng.btc_trend === 'BEARISH' ? '#f87171' : 'var(--text-muted)')}; font-size: 10px;">
                            BTC: ${eng.btc_trend === 'DISABLED' ? 'Disabled' : (eng.btc_trend || 'N/A')}
                        </span>
                    </div>
                `;
                gridEl.appendChild(card);
            });

            // Render Open Positions
            const posBody = document.getElementById('open-positions-body');
            document.getElementById('positions-header-count').textContent = data.all_open_positions.length + ' позиций';

            if (data.all_open_positions.length === 0) {
                posBody.innerHTML = '<tr><td colspan="11" style="text-align: center; color: var(--text-muted); padding: 24px;">Нет открытых позиций в текущий момент</td></tr>';
            } else {
                posBody.innerHTML = '';
                data.all_open_positions.forEach(p => {
                    const row = document.createElement('tr');
                    const sideBadge = p.direction === 'LONG' ? '<span class="badge badge-long">LONG</span>' : '<span class="badge badge-short">SHORT</span>';
                    const modBadge = p.module === 'RANGE_MAKER' ? '<span class="badge badge-range">RANGE MAKER</span>' : '<span class="badge" style="background: rgba(56, 189, 248, 0.15); color: var(--accent-blue);">TREND</span>';
                    const pnlClass = p.unrealized_pnl >= 0 ? 'val-green' : 'val-red';
                    const pnlSign = p.unrealized_pnl >= 0 ? '+' : '';
                    const pyrStr = p.is_pyramided ? '⭐ Доливка +50%' : (p.is_breakeven_set ? '🛡️ Безубыток' : 'Стандартный');

                    row.innerHTML = `
                        <td><span class="badge badge-engine">${p.engine_name} [${p.port}]</span></td>
                        <td><strong>${p.symbol}</strong></td>
                        <td>${sideBadge}</td>
                        <td>${modBadge}</td>
                        <td>${p.qty}</td>
                        <td>$${p.entry_price.toFixed(4)}</td>
                        <td>$${p.current_price.toFixed(4)}</td>
                        <td>$${p.stop_loss ? p.stop_loss.toFixed(4) : '--'}</td>
                        <td>${p.take_profit ? '$' + p.take_profit.toFixed(4) : '--'}</td>
                        <td class="${pnlClass}"><strong>${pnlSign}$${p.unrealized_pnl.toFixed(2)}</strong> (${pnlSign}${p.unrealized_pct.toFixed(2)}%)</td>
                        <td>${pyrStr}</td>
                    `;
                    posBody.appendChild(row);
                });
            }

            // Render Pending Orders
            const ordBody = document.getElementById('pending-orders-body');
            document.getElementById('orders-header-count').textContent = data.all_pending_orders.length + ' ордеров';

            if (data.all_pending_orders.length === 0) {
                ordBody.innerHTML = '<tr><td colspan="8" style="text-align: center; color: var(--text-muted); padding: 20px;">Нет активных отложенных ордеров</td></tr>';
            } else {
                ordBody.innerHTML = '';
                data.all_pending_orders.forEach(o => {
                    const row = document.createElement('tr');
                    const sideBadge = o.direction === 'BUY' ? '<span class="badge badge-long">BUY LIMIT</span>' : '<span class="badge badge-short">SELL LIMIT</span>';
                    row.innerHTML = `
                        <td><span class="badge badge-engine">${o.engine_name}</span></td>
                        <td><strong>${o.symbol}</strong></td>
                        <td>${o.order_type}</td>
                        <td>${sideBadge}</td>
                        <td class="val-cyan">$${o.price.toFixed(6)}</td>
                        <td>${o.qty}</td>
                        <td class="val-green">$${o.take_profit.toFixed(6)}</td>
                        <td class="val-red">$${o.stop_loss.toFixed(6)}</td>
                    `;
                    ordBody.appendChild(row);
                });
            }

            // Render News Feed
            const newsListEl = document.getElementById('news-feed-list');
            document.getElementById('news-count-badge').textContent = data.master_news_feed.length + ' событий';

            if (data.master_news_feed.length === 0) {
                newsListEl.innerHTML = '<div style="color: var(--text-muted); font-size: 12px; text-align: center; padding: 20px;">Ожидание новостных событий...</div>';
            } else {
                newsListEl.innerHTML = '';
                data.master_news_feed.forEach(n => {
                    const item = document.createElement('div');
                    item.className = 'news-item';
                    const dt = new Date(n.timestamp).toLocaleTimeString();
                    item.innerHTML = `
                        <div class="news-meta">
                            <span><strong style="color: var(--accent-cyan);">${n.symbol || 'MARKET'}</strong> · ${n.source_engine || 'NewsCore'}</span>
                            <span>${dt}</span>
                        </div>
                        <div class="news-headline">${n.headline}</div>
                    `;
                    newsListEl.appendChild(item);
                });
            }

            // Render Closed Trades
            const tradesBody = document.getElementById('closed-trades-body');
            document.getElementById('trades-header-count').textContent = data.all_closed_trades.length + ' сделок';

            if (data.all_closed_trades.length === 0) {
                tradesBody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 20px;">История закрытых сделок пока пуста</td></tr>';
            } else {
                tradesBody.innerHTML = '';
                data.all_closed_trades.slice(-25).reverse().forEach(t => {
                    const row = document.createElement('tr');
                    const engName = t.engine_name ? `${t.engine_name} [${t.port}]` : 'Engine';
                    const sideBadge = t.direction === 'LONG' ? '<span class="badge badge-long">LONG</span>' : '<span class="badge badge-short">SHORT</span>';
                    const pnlClass = t.net_pnl >= 0 ? 'val-green' : 'val-red';
                    const pnlSign = t.net_pnl >= 0 ? '+' : '';
                    let reasonBadge = `<span class="badge" style="background: rgba(255,255,255,0.05); color: var(--text-muted);">${t.reason || 'EXIT'}</span>`;
                    if (t.reason && t.reason.includes('SCRATCH')) {
                        reasonBadge = `<span class="badge" style="background: rgba(245, 158, 11, 0.2); color: var(--accent-amber); font-weight: 700;">⚡ FAST SCRATCH</span>`;
                    } else if (t.reason && (t.reason.includes('EJECT') || t.reason.includes('NEWS'))) {
                        reasonBadge = `<span class="badge" style="background: rgba(244, 63, 94, 0.2); color: var(--accent-red); font-weight: 700;">🚨 NEWS EJECT</span>`;
                    }
                    row.innerHTML = `
                        <td><span class="badge badge-engine">${engName}</span></td>
                        <td><strong>${t.symbol}</strong></td>
                        <td>${sideBadge}</td>
                        <td>$${t.entry_price.toFixed(4)}</td>
                        <td>$${t.exit_price.toFixed(4)}</td>
                        <td class="${pnlClass}"><strong>${pnlSign}$${t.net_pnl.toFixed(2)}</strong></td>
                        <td>${t.hold_hours || 0} ч</td>
                        <td>${reasonBadge}</td>
                        <td>${t.was_pyramided ? '✅ +50%' : '—'}</td>
                    `;
                    tradesBody.appendChild(row);
                });
            }
        }

        // Initialize & poll loops
        window.addEventListener('DOMContentLoaded', () => {
            initChart();
            fetchStatus();
            fetchChart();
            setInterval(fetchStatus, 2000);
            setInterval(fetchChart, 10000);
        });
    </script>
</body>
</html>
"""


class MetaHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        global AGGREGATOR
        if self.path == "/" or self.path == "/index.html":
            content = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/api/meta_status":
            snap = AGGREGATOR.get_snapshot() if AGGREGATOR else {}
            content = json.dumps(snap).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif self.path == "/api/meta_chart":
            chart_data = AGGREGATOR.get_chart_data() if AGGREGATOR else {}
            content = json.dumps(chart_data).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404, "Not Found")

    def log_message(self, format, *args):
        return


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def start_meta_server():
    global AGGREGATOR
    logger.info("Initializing MetaAggregator for Bybit multi-alpha fleet...")
    AGGREGATOR = MetaAggregator()

    agg_thread = threading.Thread(target=AGGREGATOR.run_loop, daemon=True)
    agg_thread.start()

    # Pre-poll once
    AGGREGATOR.update_all()

    socketserver.TCPServer.allow_reuse_address = True
    server_address = ("0.0.0.0", META_PORT)
    with ReusableThreadingTCPServer(server_address, MetaHandler) as httpd:
        logger.info(f"Master Meta-Dashboard live on http://0.0.0.0:{META_PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("Meta-Dashboard server shutting down...")
            AGGREGATOR.running = False
            httpd.server_close()


if __name__ == "__main__":
    start_meta_server()
