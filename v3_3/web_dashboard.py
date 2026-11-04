"""
Live Web Dashboard & Paper Trading Server v3.3 Turbo (Port 8086).
Features:
- Streak-Based Adaptive Risk & Dynamic R:R (1:3 -> 1:4 -> 1:5) Live Telemetry
- Dynamic Universe Radar (Top 25 screened Bybit linear perpetuals)
- News Core v1.2 Defensive Shield
- Anti-Martingale Pyramiding (+0% Gear 1, +50% Gear 2, +75% Gear 3)
- REST API: /api/status, /api/universe, /api/logs
"""

import collections
from datetime import datetime, timezone
from decimal import Decimal
import http.server
import json
import logging
from pathlib import Path
import socketserver
import sys
import threading
import time
import urllib.parse

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
REPO_ROOT = BASE_DIR.parent
for p in [str(REPO_ROOT), str(BASE_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from v3_3.src.paper_trader_v33 import PaperTraderV33
from v3_3.src.config import PORT, VERSION_NAME, LIVE_STATUS_FILE

LOG_BUFFER = collections.deque(maxlen=300)


class BufferHandler(logging.Handler):
    def emit(self, record):
        try:
            msg = self.format(record)
            LOG_BUFFER.append(msg)
        except Exception:
            pass


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
root_logger = logging.getLogger()
buf_handler = BufferHandler()
buf_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
root_logger.addHandler(buf_handler)

logger = logging.getLogger("WebDashboardV33")
GLOBAL_TRADER: PaperTraderV33 = None

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v3.3 Turbo | Streak-Based Dynamic Risk (1:3 -> 1:5)</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-dark: #080c14;
            --bg-card: #0f172a;
            --bg-card-hover: #1e293b;
            --accent-cyan: #00f2fe;
            --accent-blue: #38bdf8;
            --accent-green: #10b981;
            --accent-red: #f43f5e;
            --accent-amber: #f59e0b;
            --accent-purple: #a855f7;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border: #1e293b;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background-color: var(--bg-dark);
            color: var(--text-main);
            font-family: 'Inter', sans-serif;
            padding: 24px;
        }
        .container { max-width: 1560px; margin: 0 auto; }
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
            gap: 14px;
        }
        .logo-badge {
            background: linear-gradient(135deg, #f59e0b 0%, #ef4444 100%);
            color: #080c14;
            font-weight: 800;
            font-size: 13px;
            padding: 6px 12px;
            border-radius: 6px;
            letter-spacing: 1px;
            font-family: 'JetBrains Mono', monospace;
            box-shadow: 0 0 15px rgba(245, 158, 11, 0.4);
        }
        .title { font-size: 20px; font-weight: 800; }
        .subtitle { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
        
        /* Stats Grid */
        .stats-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .card {
            background-color: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 18px 20px;
            position: relative;
            overflow: hidden;
        }
        .card-label {
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
        }
        .card-val {
            font-size: 24px;
            font-weight: 800;
            font-family: 'JetBrains Mono', monospace;
        }
        .card-sub {
            font-size: 12px;
            margin-top: 6px;
            color: var(--text-muted);
        }

        .val-green { color: var(--accent-green); }
        .val-red { color: var(--accent-red); }
        .val-amber { color: var(--accent-amber); }
        .val-cyan { color: var(--accent-cyan); }

        /* Gearbox Banner */
        .gearbox-banner {
            background: linear-gradient(135deg, rgba(245, 158, 11, 0.1) 0%, rgba(15, 23, 42, 0.8) 100%);
            border: 1px solid rgba(245, 158, 11, 0.3);
            border-radius: 12px;
            padding: 16px 20px;
            margin-bottom: 24px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            flex-wrap: wrap;
            gap: 16px;
        }
        .gear-pill {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 14px;
            border-radius: 8px;
            font-weight: 700;
            font-size: 13px;
            font-family: 'JetBrains Mono', monospace;
        }
        .gear-1 { background: rgba(56, 189, 248, 0.15); color: var(--accent-blue); border: 1px solid rgba(56, 189, 248, 0.3); }
        .gear-2 { background: rgba(16, 185, 129, 0.15); color: var(--accent-green); border: 1px solid rgba(16, 185, 129, 0.3); }
        .gear-3 { background: rgba(245, 158, 11, 0.2); color: var(--accent-amber); border: 1px solid rgba(245, 158, 11, 0.5); box-shadow: 0 0 12px rgba(245, 158, 11, 0.3); }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        th {
            text-align: left;
            padding: 12px 14px;
            color: var(--text-muted);
            border-bottom: 1px solid var(--border);
            font-weight: 600;
            font-size: 11px;
            text-transform: uppercase;
        }
        td {
            padding: 12px 14px;
            border-bottom: 1px solid rgba(30, 41, 59, 0.5);
            font-family: 'JetBrains Mono', monospace;
        }
        .badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
        }
        .badge-long { background: rgba(16, 185, 129, 0.15); color: var(--accent-green); }
        .badge-short { background: rgba(244, 63, 94, 0.15); color: var(--accent-red); }

        .log-box {
            background-color: #050811;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 14px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            height: 220px;
            overflow-y: auto;
            color: #94a3b8;
            line-height: 1.5;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo-group">
                <div class="logo-badge">V3.3 TURBO</div>
                <div>
                    <div class="title">Bybit Multi-Alpha v3.3 Turbo (Port 8086)</div>
                    <div class="subtitle">Streak-Based Adaptive Risk & Dynamic R:R (1:3 → 1:4 → 1:5)</div>
                </div>
            </div>
            <div style="font-size: 12px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace;" id="clock-utc">
                --:--:-- UTC
            </div>
        </header>

        <!-- Gearbox Banner -->
        <div class="gearbox-banner">
            <div>
                <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 4px;">Активный режим риска</div>
                <div id="gearbox-pill" class="gear-pill gear-1">⚙️ GEAR 1: PROTECTIVE (1:3)</div>
            </div>
            <div>
                <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 4px;">Серия побед / поражений</div>
                <div style="font-size: 15px; font-weight: 700;" id="streak-counter">🔥 0 побед подряд</div>
            </div>
            <div>
                <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 4px;">Целевой каскад R:R</div>
                <div style="font-size: 15px; font-weight: 700; color: var(--accent-amber);" id="rr-target-label">1:3.0 — 1:4.0</div>
            </div>
            <div>
                <div style="font-size: 11px; color: var(--text-muted); text-transform: uppercase; margin-bottom: 4px;">Пирамидинг в тренде</div>
                <div style="font-size: 15px; font-weight: 700;" id="pyramid-status">Выключен (0%)</div>
            </div>
        </div>

        <!-- Metrics Grid -->
        <div class="stats-grid">
            <div class="card">
                <div class="card-label">Эквити портфеля</div>
                <div class="card-val val-cyan" id="equity-val">$300.00</div>
                <div class="card-sub" id="pnl-sub">Чистый PnL: $0.00 (0.00%)</div>
            </div>
            <div class="card">
                <div class="card-label">Риск на сделку (1R)</div>
                <div class="card-val val-amber" id="risk-val">1.5%</div>
                <div class="card-sub" id="risk-sub">$4.50 макс. риск</div>
            </div>
            <div class="card">
                <div class="card-label">Макс. просадка</div>
                <div class="card-val" id="drawdown-val">0.00%</div>
                <div class="card-sub">Лимит: 30.0% (Cut at 10%)</div>
            </div>
            <div class="card">
                <div class="card-label">Винрейт / Сделки</div>
                <div class="card-val" id="winrate-val">--%</div>
                <div class="card-sub" id="trades-sub">0 сделок закрыто</div>
            </div>
        </div>

        
        <!-- Performance Equity & PnL Curve Chart -->
        <div class="card" style="margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px; margin: 0;">📈 Динамика депозита и результатов сделок (Live PnL Curve)</h3>
                    <span style="font-size: 10px; font-weight: 700; background: rgba(0, 242, 254, 0.15); color: var(--accent-cyan, #00f2fe); border: 1px solid rgba(0, 242, 254, 0.3); padding: 2px 8px; border-radius: 4px;">LIVE REAL-TIME</span>
                </div>
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--text-muted);">
                    Депозит старт: <strong style="color: var(--text-main, #f8fafc);">$300.00</strong> | Текущее эквити: <strong id="chart-curr-equity" style="color: var(--accent-cyan, #00f2fe);">$300.00</strong>
                </div>
            </div>
            <div style="height: 180px; position: relative;">
                <canvas id="equityChart"></canvas>
            </div>
        </div>


        <!-- Subsystems Health & Telemetry Monitor -->
        <div class="card" style="margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px; margin: 0;">🩺 Мониторинг подсистем и телеметрия ядра v3.3 Turbo Risk</h3>
                    <span style="font-size: 10px; font-weight: 700; background: rgba(16, 185, 129, 0.15); color: var(--accent-green, #10b981); border: 1px solid rgba(16, 185, 129, 0.3); padding: 2px 8px; border-radius: 4px;">HEALTH: NOMINAL</span>
                </div>
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-muted);">
                    Отклик Bybit API: <strong id="sub-ping-val" style="color: var(--accent-cyan, #00f2fe);">2.1 ms</strong>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px;">
                <!-- 1. Market Stream -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">1. Поток Bybit WS</span>
                        <span id="sub-ws-tag" style="font-size: 10px; font-weight: 700; color: var(--accent-green, #10b981); padding: 2px 6px; background: rgba(16, 185, 129, 0.15); border-radius: 4px;">ONLINE</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main, #f8fafc);" id="sub-ws-info">
                        Подписка: <strong>...</strong> | V5 Linear
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Потоковые свечи 1h и тики</div>
                </div>

                <!-- 2. Alpha Strategy -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">2. Квантовый движок</span>
                        <span id="sub-alpha-tag" style="font-size: 10px; font-weight: 700; color: var(--accent-cyan, #00f2fe); padding: 2px 6px; background: rgba(0, 242, 254, 0.15); border-radius: 4px;">ACTIVE</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main, #f8fafc);" id="sub-alpha-info">
                        1h Turbo Dynamic Leverage + Aggressor
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Фильтры: BTC Delta / ADX >= 18 / KER</div>
                </div>

                <!-- 3. Risk & Gear -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">3. Риск-менеджер & АКПП</span>
                        <span id="sub-gear-tag" style="font-size: 10px; font-weight: 700; color: var(--accent-gold, #f59e0b); padding: 2px 6px; background: rgba(245, 158, 11, 0.15); border-radius: 4px;">PROTECTED</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main, #f8fafc);" id="sub-gear-info">
                        Риск: <strong>...</strong> | Half-Kelly
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Penny-Perfect средняя цена и безубыток</div>
                </div>

                <!-- 4. Defense Shield -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">4. Щит News Core v1.2</span>
                        <span id="sub-news-tag" style="font-size: 10px; font-weight: 700; color: var(--accent-green, #10b981); padding: 2px 6px; background: rgba(16, 185, 129, 0.15); border-radius: 4px;">ACTIVE</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main, #f8fafc);" id="sub-news-info">
                        Макро-эмбарго: <strong>НЕТ (Торговля активна)</strong>
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Emergency Eject, Squeeze Escape & Regime Guard</div>
                </div>
            </div>
        </div>

        <!-- Open Positions -->
        <div class="card" style="margin-bottom: 24px;">
            <div class="card-label" style="display: flex; justify-content: space-between;">
                <span>Открытые позиции</span>
                <span id="pos-count">0 позиций</span>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Символ</th>
                        <th>Сторона</th>
                        <th>Объем</th>
                        <th>Вход</th>
                        <th>Текущая</th>
                        <th>Стоп-лосс</th>
                        <th>Цель TP1 (BE)</th>
                        <th>Цель TP2</th>
                        <th>Плавающий PnL</th>
                        <th>Передача</th>
                    </tr>
                </thead>
                <tbody id="positions-body">
                    <tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 20px;">Нет открытых позиций</td></tr>
                </tbody>
            </table>
        </div>

        
        <!-- Closed Trades Table -->
        <div class="card" style="margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px; margin: 0;">📜 История закрытых сделок (PnL & Результаты исполнения)</h3>
                </div>
                <span id="closed-trades-count" style="font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--accent-cyan, #00f2fe); font-weight: 700;">0 сделок</span>
            </div>
            <div style="overflow-x: auto;">
                <table style="width: 100%; border-collapse: collapse; font-family: 'JetBrains Mono', monospace; font-size: 12px;">
                    <thead>
                        <tr style="border-bottom: 1px solid var(--border); color: var(--text-muted); font-size: 11px; text-transform: uppercase;">
                            <th style="padding: 10px 8px; text-align: left;">Время (UTC)</th>
                            <th style="padding: 10px 8px; text-align: left;">Символ</th>
                            <th style="padding: 10px 8px; text-align: left;">Сторона</th>
                            <th style="padding: 10px 8px; text-align: left;">Вход</th>
                            <th style="padding: 10px 8px; text-align: left;">Выход</th>
                            <th style="padding: 10px 8px; text-align: left;">Чистый PnL ($)</th>
                            <th style="padding: 10px 8px; text-align: left;">Причина выхода</th>
                        </tr>
                    </thead>
                    <tbody id="closed-table-body">
                        <tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 20px;">Нет закрытых сделок. Накопление торговой истории...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Logs -->
        <div class="card">
            <div class="card-label">Системный журнал событий v3.3</div>
            <div class="log-box" id="logs-box">Загрузка логов...</div>
        </div>
    </div>

    <script>
        
        let chartInstance = null;
        function initEquityChart() {
            const canvas = document.getElementById('equityChart');
            if (!canvas || typeof Chart === 'undefined') return;
            const ctx = canvas.getContext('2d');
            chartInstance = new Chart(ctx, {
                type: 'line',
                data: {
                    labels: ['Старт'],
                    datasets: [{
                        label: 'Эквити ($)',
                        data: [300.0],
                        borderColor: '#00f2fe',
                        backgroundColor: 'rgba(0, 242, 254, 0.08)',
                        fill: true,
                        tension: 0.25,
                        borderWidth: 2,
                        pointRadius: 4,
                        pointHoverRadius: 6,
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: (c) => ` Эквити: $${Number(c.parsed.y).toFixed(2)}`
                            }
                        }
                    },
                    scales: {
                        x: {
                            grid: { color: 'rgba(255, 255, 255, 0.04)' },
                            ticks: { color: '#64748b', font: { family: "'JetBrains Mono', monospace", size: 10 } }
                        },
                        y: {
                            grid: { color: 'rgba(255, 255, 255, 0.04)' },
                            ticks: {
                                color: '#94a3b8',
                                font: { family: "'JetBrains Mono', monospace", size: 11 },
                                callback: (v) => '$' + Number(v).toFixed(2)
                            }
                        }
                    }
                }
            });
        }

        function updateCommonChartAndClosed(data) {
            const closed = data.closed_trades || [];
            const eq = Number(data.portfolio_equity || data.capital || data.equity || 300.0);
            const initCap = Number(data.initial_capital || 300.0);

            // Update Chart Current Equity Label
            const chartCurr = document.getElementById('chart-curr-equity');
            if (chartCurr) chartCurr.innerText = '$' + eq.toFixed(2);

            // Update Closed Count
            const cntEl = document.getElementById('closed-trades-count');
            if (cntEl) cntEl.innerText = `${closed.length} сделок`;

            // Update Closed Table Body
            const clBody = document.getElementById('closed-table-body');
            if (clBody) {
                if (closed.length === 0) {
                    clBody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 20px;">Нет закрытых сделок. Накопление торговой истории...</td></tr>';
                } else {
                    const reversedClosed = [...closed].reverse();
                    clBody.innerHTML = reversedClosed.map(t => {
                        const pnlVal = Number(t.net_pnl !== undefined ? t.net_pnl : (t.net_pnl_usdt || 0.0));
                        const isWin = pnlVal >= 0;
                        const pnlColor = isWin ? '#10b981' : '#ef4444';
                        const side = (t.direction || t.side || 'LONG').toUpperCase();
                        const isL = side === 'LONG' || side === 'BUY';
                        const sideBg = isL ? 'rgba(16, 185, 129, 0.15)' : 'rgba(239, 68, 68, 0.15)';
                        const sideCol = isL ? '#10b981' : '#ef4444';
                        
                        let timeStr = '--:--:--';
                        const ts = t.exit_time || t.timestamp;
                        if (ts) {
                            try {
                                timeStr = new Date(ts > 1e11 ? ts : ts * 1000).toISOString().substring(11, 19);
                            } catch (e) {}
                        }

                        const reason = t.reason || t.exit_reason || 'CLOSE';
                        let rCol = '#94a3b8';
                        let rBg = 'rgba(148, 163, 184, 0.15)';
                        if (reason.includes('TP') || reason.includes('PROFIT')) { rCol = '#10b981'; rBg = 'rgba(16, 185, 129, 0.2)'; }
                        else if (reason.includes('STOP')) { rCol = '#ef4444'; rBg = 'rgba(239, 68, 68, 0.2)'; }
                        else if (reason.includes('SCRATCH')) { rCol = '#38bdf8'; rBg = 'rgba(56, 189, 248, 0.2)'; }

                        return `
                            <tr style="border-bottom: 1px solid rgba(255,255,255,0.03);">
                                <td style="padding: 10px 8px; color: var(--text-muted);">${timeStr} UTC</td>
                                <td style="padding: 10px 8px; font-weight: 700;">${t.symbol}</td>
                                <td style="padding: 10px 8px;"><span style="padding: 2px 6px; border-radius: 4px; font-weight: 700; font-size: 10px; background: ${sideBg}; color: ${sideCol};">${side}</span></td>
                                <td style="padding: 10px 8px;">$${Number(t.entry_price || 0).toFixed(4)}</td>
                                <td style="padding: 10px 8px;">$${Number(t.exit_price || 0).toFixed(4)}</td>
                                <td style="padding: 10px 8px; font-weight: 700; color: ${pnlColor};">${isWin ? '+' : ''}$${pnlVal.toFixed(2)}</td>
                                <td style="padding: 10px 8px;"><span style="padding: 2px 6px; border-radius: 4px; font-weight: 700; font-size: 10px; background: ${rBg}; color: ${rCol};">${reason}</span></td>
                            </tr>
                        `;
                    }).join('');
                }
            }

            // Update Chart
            if (chartInstance) {
                const labels = ['Старт'];
                const points = [initCap];
                let runEq = initCap;

                closed.forEach((t, idx) => {
                    const pnlVal = Number(t.net_pnl !== undefined ? t.net_pnl : (t.net_pnl_usdt || 0.0));
                    runEq += pnlVal;
                    labels.push(`#${idx + 1} ${t.symbol}`);
                    points.push(Number(runEq.toFixed(2)));
                });

                if (points.length === 1 || Math.abs(points[points.length - 1] - eq) > 0.001) {
                    labels.push('Текущее');
                    points.push(Number(eq.toFixed(2)));
                }

                chartInstance.data.labels = labels;
                chartInstance.data.datasets[0].data = points;
                const isProfit = eq >= initCap;
                chartInstance.data.datasets[0].borderColor = isProfit ? '#00f2fe' : '#ef4444';
                chartInstance.data.datasets[0].pointBackgroundColor = isProfit ? '#00f2fe' : '#ef4444';
                chartInstance.data.datasets[0].backgroundColor = isProfit ? 'rgba(0, 242, 254, 0.08)' : 'rgba(239, 68, 68, 0.08)';
                chartInstance.update();
            } else if (typeof Chart !== 'undefined') {
                initEquityChart();
            }

            // Update Subsystems Telemetry
            const pingEl = document.getElementById('sub-ping-val');
            if (pingEl) pingEl.innerText = (data.ping_ms || 2.1) + ' ms';

            const wsInfo = document.getElementById('sub-ws-info');
            if (wsInfo) {
                const coins = data.monitored_symbols_count || (data.bybit_ws_symbols || (data.monitored_symbols ? data.monitored_symbols.length : 18));
                wsInfo.innerHTML = `Подписка: <strong>${coins} монет</strong> | V5 Linear`;
            }

            const gearInfo = document.getElementById('sub-gear-info');
            if (gearInfo) {
                const g = data.gearbox_gear || data.gear || 1;
                const r = data.gearbox_risk_pct || data.risk_pct || 1.5;
                gearInfo.innerHTML = `Ступень: <strong>G${g}</strong> | Риск: <strong>${r}%</strong>`;
            }

            const newsInfo = document.getElementById('sub-news-info');
            if (newsInfo) {
                const isEmbargo = data.macro_blackout_active || data.macro_embargo_active || false;
                newsInfo.innerHTML = isEmbargo 
                    ? '<strong style="color: #ef4444;">ЭМБАРГО АКТИВНО (Торговля заблокирована)</strong>'
                    : 'Эмбарго: <strong>НЕТ (Торговля активна)</strong>';
            }
        }

        async function updateStatus() {
            try {
                const resp = await fetch('/api/status');
                const data = await resp.json();
                updateCommonChartAndClosed(data);

                document.getElementById('clock-utc').textContent = data.datetime_utc || new Date().toISOString();
                document.getElementById('equity-val').textContent = '$' + (data.capital || 300.0).toFixed(2);
                
                const pnl = data.total_net_pnl || 0.0;
                const pnlSign = pnl >= 0 ? '+' : '';
                const pnlEl = document.getElementById('pnl-sub');
                pnlEl.textContent = `Чистый PnL: ${pnlSign}$${pnl.toFixed(2)}`;
                pnlEl.className = pnl >= 0 ? 'card-sub val-green' : 'card-sub val-red';

                const dd = data.drawdown_pct || 0.0;
                document.getElementById('drawdown-val').textContent = dd.toFixed(2) + '%';
                document.getElementById('winrate-val').textContent = (data.win_rate_pct || 0.0).toFixed(1) + '%';
                document.getElementById('trades-sub').textContent = (data.closed_trades_count || 0) + ' сделок закрыто';

                // Gearbox
                const gear = data.gearbox_gear || 1;
                const riskPct = data.gearbox_risk_pct || 1.5;
                const riskUsd = ((data.capital || 300.0) * riskPct / 100).toFixed(2);
                document.getElementById('risk-val').textContent = riskPct.toFixed(1) + '%';
                document.getElementById('risk-sub').textContent = `~$${riskUsd} макс. риск`;

                const gearPill = document.getElementById('gearbox-pill');
                gearPill.className = `gear-pill gear-${gear}`;
                gearPill.textContent = `⚙️ GEAR ${gear}: ${data.gear_name || 'PROTECTIVE'}`;

                const wins = data.streak_wins || 0;
                const losses = data.streak_losses || 0;
                if (wins > 0) {
                    document.getElementById('streak-counter').textContent = `🔥 ${wins} побед подряд`;
                    document.getElementById('streak-counter').className = 'val-green';
                } else if (losses > 0) {
                    document.getElementById('streak-counter').textContent = `❄️ ${losses} убытков подряд`;
                    document.getElementById('streak-counter').className = 'val-red';
                } else {
                    document.getElementById('streak-counter').textContent = '🛡️ Нейтральная серия';
                    document.getElementById('streak-counter').className = '';
                }

                document.getElementById('rr-target-label').textContent = data.rr_target || '1:3.0 — 1:4.0';
                const pyrRatio = data.pyramiding_ratio ? `+${(data.pyramiding_ratio * 100).toFixed(0)}%` : 'Выключен (0%)';
                document.getElementById('pyramid-status').textContent = pyrRatio;

                // Positions
                const posBody = document.getElementById('positions-body');
                const positions = data.open_positions || {};
                const keys = Object.keys(positions);
                document.getElementById('pos-count').textContent = keys.length + ' позиций';

                if (keys.length === 0) {
                    posBody.innerHTML = '<tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 20px;">Нет открытых позиций</td></tr>';
                } else {
                    posBody.innerHTML = '';
                    keys.forEach(sym => {
                        const p = positions[sym];
                        const row = document.createElement('tr');
                        const sideBadge = p.direction === 'LONG' || p.direction === 1 ? '<span class="badge badge-long">LONG</span>' : '<span class="badge badge-short">SHORT</span>';
                        const pnl = p.unrealized_pnl || 0.0;
                        const pnlSign = pnl >= 0 ? '+' : '';
                        const pnlClass = pnl >= 0 ? 'val-green' : 'val-red';
                        row.innerHTML = `
                            <td><strong>${sym}</strong></td>
                            <td>${sideBadge}</td>
                            <td>${p.qty}</td>
                            <td>$${Number(p.entry_price).toFixed(4)}</td>
                            <td>$${Number(p.current_price || p.entry_price).toFixed(4)}</td>
                            <td class="val-red">$${Number(p.current_stop).toFixed(4)}</td>
                            <td class="val-cyan">+${p.tp1_r_mult || 3.0}R</td>
                            <td class="val-green">+${p.tp2_r_mult || 4.0}R</td>
                            <td class="${pnlClass}"><strong>${pnlSign}$${pnl.toFixed(2)}</strong></td>
                            <td><span class="badge" style="background: rgba(245, 158, 11, 0.15); color: var(--accent-amber);">G${p.entry_gear || 1}</span></td>
                        `;
                        posBody.appendChild(row);
                    });
                }
            } catch (e) {
                console.error("Status fetch error:", e);
            }
        }

        async function updateLogs() {
            try {
                const resp = await fetch('/api/logs');
                const logs = await resp.json();
                const box = document.getElementById('logs-box');
                box.innerHTML = logs.slice(-50).map(l => `<div>${l}</div>`).join('');
                box.scrollTop = box.scrollHeight;
            } catch (e) {}
        }

        setInterval(updateStatus, 2000);
        setInterval(updateLogs, 4000);
        updateStatus();
        updateLogs();
    </script>
</body>
</html>
"""


class DashboardHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/" or path == "/index.html":
            content = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        elif path == "/api/status":
            if GLOBAL_TRADER and getattr(GLOBAL_TRADER, "last_status_cache", None):
                resp = json.dumps(GLOBAL_TRADER.last_status_cache, indent=2).encode("utf-8")
            elif LIVE_STATUS_FILE.exists():
                try:
                    resp = LIVE_STATUS_FILE.read_bytes()
                except Exception:
                    resp = b"{}"
            else:
                resp = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        elif path == "/api/logs":
            logs = list(LOG_BUFFER)
            resp = json.dumps(logs).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        elif path == "/api/universe":
            univ = []
            if GLOBAL_TRADER and hasattr(GLOBAL_TRADER, "screened_universe"):
                univ = [c.to_dict() if hasattr(c, "to_dict") else vars(c) for c in GLOBAL_TRADER.screened_universe]
            resp = json.dumps(univ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        else:
            self.send_error(404, "Endpoint not found")

    def log_message(self, format, *args):
        return


def start_server_and_bot():
    global GLOBAL_TRADER
    logger.info("Initializing PaperTraderV33 daemon...")
    GLOBAL_TRADER = PaperTraderV33()

    bot_thread = threading.Thread(target=GLOBAL_TRADER.run_daemon, daemon=True)
    bot_thread.start()
    logger.info("Background PaperTraderV33 thread launched.")

    server_address = ("0.0.0.0", PORT)
    with socketserver.ThreadingTCPServer(server_address, DashboardHTTPRequestHandler) as httpd:
        logger.info(f"Multi-Alpha v3.3 Turbo (Port {PORT}) Web Dashboard live on http://127.0.0.1:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("Server interrupted. Shutting down...")
            GLOBAL_TRADER.stop()
            httpd.server_close()


if __name__ == "__main__":
    start_server_and_bot()
