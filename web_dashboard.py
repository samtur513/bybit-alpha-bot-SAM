"""
Live Web Dashboard & Paper Trading Server v2.0.
Built-in multi-threaded HTTP server with live market radar, candle countdown,
and interactive demo controls.
"""

import collections
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
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from src.paper_trader_v2 import PaperTraderV2
from src.config import REPORTS_DIR

LOG_BUFFER = collections.deque(maxlen=250)

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

logger = logging.getLogger("WebDashboard")

PORT = 8080
GLOBAL_TRADER: PaperTraderV2 = None

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v2.0 | Quantitative Dashboard</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-dark: #0a0e17;
            --bg-card: #121826;
            --bg-card-hover: #1a2235;
            --accent-cyan: #00f2fe;
            --accent-blue: #4facfe;
            --accent-green: #00e676;
            --accent-red: #ff3366;
            --accent-yellow: #ffd600;
            --text-main: #f0f4fc;
            --text-muted: #7e8da6;
            --border: #1f293d;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: 'Inter', -apple-system, sans-serif;
            background: var(--bg-dark);
            color: var(--text-main);
            min-height: 100vh;
            padding: 24px;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            padding-bottom: 20px;
            margin-bottom: 24px;
        }
        .logo-group {
            display: flex;
            align-items: center;
            gap: 14px;
        }
        .logo-badge {
            background: linear-gradient(135deg, var(--accent-cyan), var(--accent-blue));
            color: #000;
            font-weight: 800;
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 14px;
            letter-spacing: 0.5px;
        }
        .title { font-size: 22px; font-weight: 700; }
        .subtitle { font-size: 13px; color: var(--text-muted); margin-top: 4px; }
        .header-actions {
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .status-pill {
            display: flex;
            align-items: center;
            gap: 8px;
            background: #0d281e;
            border: 1px solid #134e35;
            color: var(--accent-green);
            padding: 8px 16px;
            border-radius: 20px;
            font-size: 13px;
            font-weight: 600;
        }
        .pulse {
            width: 10px;
            height: 10px;
            background: var(--accent-green);
            border-radius: 50%;
            animation: pulse-animation 2s infinite;
        }
        @keyframes pulse-animation {
            0% { box-shadow: 0 0 0 0 rgba(0, 230, 118, 0.7); }
            70% { box-shadow: 0 0 0 10px rgba(0, 230, 118, 0); }
            100% { box-shadow: 0 0 0 0 rgba(0, 230, 118, 0); }
        }
        .btn {
            background: var(--bg-card);
            border: 1px solid var(--border);
            color: var(--text-main);
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.2s;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .btn:hover {
            background: var(--bg-card-hover);
            border-color: var(--accent-blue);
        }
        .btn-primary {
            background: linear-gradient(135deg, #1e3c72, #2a5298);
            border: 1px solid var(--accent-blue);
        }
        .btn-primary:hover {
            background: linear-gradient(135deg, #2a5298, #3b71c4);
        }
        .btn-danger {
            background: #3d0c18;
            border: 1px solid #6b1428;
            color: var(--accent-red);
        }
        .btn-danger:hover {
            background: #541021;
        }

        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            transition: all 0.2s ease;
        }
        .card:hover {
            border-color: #2b3954;
        }
        .card-label {
            font-size: 12px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.8px;
            margin-bottom: 8px;
        }
        .card-value {
            font-size: 24px;
            font-weight: 700;
            font-family: 'JetBrains Mono', monospace;
        }
        .card-sub {
            font-size: 12px;
            margin-top: 6px;
            color: var(--text-muted);
        }
        .val-pos { color: var(--accent-green); }
        .val-neg { color: var(--accent-red); }
        .val-cyan { color: var(--accent-cyan); }
        .val-yellow { color: var(--accent-yellow); }

        .section-title {
            font-size: 15px;
            font-weight: 700;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        .badge {
            font-size: 11px;
            padding: 3px 8px;
            border-radius: 6px;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.3px;
        }
        .badge-green { background: #0c3321; color: var(--accent-green); border: 1px solid #145938; }
        .badge-red { background: #3d0c18; color: var(--accent-red); border: 1px solid #6b1428; }
        .badge-blue { background: #0c263d; color: var(--accent-blue); border: 1px solid #14466b; }
        .badge-yellow { background: #38300c; color: var(--accent-yellow); border: 1px solid #665714; }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        th {
            text-align: left;
            padding: 12px 16px;
            color: var(--text-muted);
            font-weight: 600;
            border-bottom: 1px solid var(--border);
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        td {
            padding: 14px 16px;
            border-bottom: 1px solid var(--border);
            font-family: 'JetBrains Mono', monospace;
        }
        tr:hover td { background: var(--bg-card-hover); }
        .empty-state {
            padding: 30px;
            text-align: center;
            color: var(--text-muted);
            font-size: 13px;
        }

        .bar-container {
            width: 100%;
            height: 6px;
            background: #1f293d;
            border-radius: 3px;
            overflow: hidden;
            margin-top: 6px;
        }
        .bar-fill {
            height: 100%;
            background: linear-gradient(90deg, var(--accent-blue), var(--accent-cyan));
            border-radius: 3px;
        }

        .grid-layout {
            display: grid;
            grid-template-columns: 3fr 2fr;
            gap: 20px;
            margin-bottom: 24px;
        }
        @media (max-width: 1024px) {
            .grid-layout { grid-template-columns: 1fr; }
        }

        .feed {
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            max-height: 250px;
            overflow-y: auto;
            display: flex;
            flex-direction: column;
            gap: 8px;
        }
        .feed-item {
            padding: 8px 12px;
            background: #0d121c;
            border-radius: 6px;
            border-left: 3px solid var(--accent-blue);
            line-height: 1.4;
        }
        .feed-time { color: var(--text-muted); font-size: 11px; margin-right: 6px; }
    </style>
</head>
<body>

    <div class="header">
        <div class="logo-group">
            <div class="logo-badge">BYBIT V5</div>
            <div>
                <div class="title">Multi-Alpha Growth Engine v2.0</div>
                <div class="subtitle">Количественная система на бессрочных фьючерсах Bybit (Half-Kelly + Anti-Martingale)</div>
            </div>
        </div>
        <div class="header-actions">
            <button class="btn btn-primary" onclick="triggerDemoOpen('SOLUSDT')">⚡ Тест SOL</button>
            <button class="btn btn-primary" onclick="triggerDemoOpen('SUIUSDT')">⚡ Тест SUI</button>
            <button class="btn btn-primary" onclick="triggerDemoOpen('DOGEUSDT')">⚡ Тест DOGE</button>
            <button class="btn btn-danger" onclick="triggerDemoClose()">✖ Закрыть все</button>
            <div class="status-pill">
                <div class="pulse"></div>
                <span id="live-status-text">18 МОНЕТ (LIVE)</span>
            </div>
        </div>
    </div>

    <!-- METRICS CARDS -->
    <div class="metrics-grid">
        <div class="card">
            <div class="card-label">Текущий Капитал</div>
            <div class="card-value val-cyan" id="metric-equity">$300.00</div>
            <div class="card-sub">Старт: $300.00 USDT</div>
        </div>
        <div class="card">
            <div class="card-label">Чистый PnL</div>
            <div class="card-value" id="metric-pnl">+$0.00</div>
            <div class="card-sub" id="metric-pnl-pct">+0.00%</div>
        </div>
        <div class="card">
            <div class="card-label">Макс. Просадка</div>
            <div class="card-value val-yellow" id="metric-drawdown">0.00%</div>
            <div class="card-sub">Лимит: ≤ 18.00%</div>
        </div>
        <div class="card">
            <div class="card-label">Режимный Kill-Switch</div>
            <div class="card-value" id="metric-killswitch" style="font-size: 19px;">🟢 В НОРМЕ</div>
            <div class="card-sub" id="metric-risk-pct">Риск на сделку: 2.50%</div>
        </div>
        <div class="card">
            <div class="card-label">До закрытия 1ч свечи</div>
            <div class="card-value val-cyan" id="metric-countdown">--:--</div>
            <div class="card-sub">Проверка сигналов в конце часа</div>
        </div>
    </div>

    
        <!-- Performance Equity & PnL Curve Chart -->
        <div class="card" style="margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px; margin: 0;">📈 Динамика депозита и результатов сделок (Live PnL Curve)</h3>
                    <span style="font-size: 10px; font-weight: 700; background: rgba(0, 242, 254, 0.15); color: #00f2fe; border: 1px solid rgba(0, 242, 254, 0.3); padding: 2px 8px; border-radius: 4px;">LIVE REAL-TIME</span>
                </div>
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--text-muted);">
                    Депозит старт: <strong style="color: var(--text);">$300.00</strong> | Текущее эквити: <strong id="chart-curr-equity" style="color: #00f2fe;">$300.00</strong>
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
                    <h3 style="font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px; margin: 0;">🩺 Мониторинг подсистем и телеметрия ядра v2.0 Base</h3>
                    <span style="font-size: 10px; font-weight: 700; background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); padding: 2px 8px; border-radius: 4px;">HEALTH: NOMINAL</span>
                </div>
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-muted);">
                    Отклик Bybit API: <strong id="sub-ping-val" style="color: #00f2fe;">2.0 ms</strong>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px;">
                <!-- 1. Market Stream -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">1. Поток Bybit WS</span>
                        <span id="sub-ws-tag" style="font-size: 10px; font-weight: 700; color: #10b981; padding: 2px 6px; background: rgba(16, 185, 129, 0.15); border-radius: 4px;">ONLINE</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text);" id="sub-ws-info">
                        Подписка: <strong>18 монет</strong> | V5 Linear
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Потоковые свечи 1h и тики</div>
                </div>

                <!-- 2. Alpha Strategy -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">2. Квантовый движок</span>
                        <span id="sub-alpha-tag" style="font-size: 10px; font-weight: 700; color: #00f2fe; padding: 2px 6px; background: rgba(0, 242, 254, 0.15); border-radius: 4px;">ACTIVE</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text);" id="sub-alpha-info">
                        1h Donchian Breakout Baseline Engine
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Фильтры тренда: ADX / EMA50 > EMA200 / BTC</div>
                </div>

                <!-- 3. Risk & Gear -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">3. Риск-менеджер & АКПП</span>
                        <span id="sub-gear-tag" style="font-size: 10px; font-weight: 700; color: #f59e0b; padding: 2px 6px; background: rgba(245, 158, 11, 0.15); border-radius: 4px;">PROTECTED</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text);" id="sub-gear-info">
                        Риск: <strong>...</strong> | Half-Kelly
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Penny-Perfect средняя цена и безубыток</div>
                </div>

                <!-- 4. Defense Shield -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">4. Щит News Core v1.2</span>
                        <span id="sub-news-tag" style="font-size: 10px; font-weight: 700; color: #10b981; padding: 2px 6px; background: rgba(16, 185, 129, 0.15); border-radius: 4px;">ACTIVE</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text);" id="sub-news-info">
                        Макро-эмбарго: <strong>НЕТ (Торговля активна)</strong>
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Emergency Eject, Squeeze Escape & Regime Guard</div>
                </div>
            </div>
        </div>

    <!-- 1. LIVE RADAR OF BYBIT UNIVERSE -->
    <div class="card" style="padding: 0; margin-bottom: 24px;">
        <div style="padding: 16px 20px; border-bottom: 1px solid var(--border);">
            <div class="section-title">
                <span>📡 Рыночный Радар Bybit (Сканер 120ч каналов Donchian)</span>
                <span class="badge badge-blue">АКТИВНЫЙ МОНИТОРИНГ</span>
            </div>
        </div>
        <div class="macro-banner" id="macro-banner" style="background: rgba(15,23,42,0.6); padding: 10px 20px; border-bottom: 1px solid var(--border); display: flex; justify-content: space-between; align-items: center; font-size: 12px;">
            <div style="display: flex; gap: 8px; align-items: center;">
                <span style="color: var(--text-muted);">🛡️ Фильтр рынка BTC:</span>
                <span id="banner-btc-status" style="font-weight: 700; color: var(--accent-red);">🔴 BEARISH (Защита: Long-пробои альтов заморожены)</span>
            </div>
            <div style="display: flex; gap: 8px; align-items: center;">
                <span style="color: var(--text-muted);">⏱️ До закрытия 1h свечи:</span>
                <span id="banner-candle-timer" style="font-family: 'JetBrains Mono', monospace; font-weight: 700; color: var(--accent-cyan);">--:--</span>
                <span style="color: var(--text-muted); font-size: 11px;">(входы строго на закрытии 1h бара)</span>
            </div>
        </div>
        <div id="radar-container">
            <div class="empty-state">Синхронизация котировок с Bybit V5...</div>
        </div>
    </div>

    
        <!-- Closed Trades Table -->
        <div class="card" style="margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px; margin: 0;">📜 История закрытых сделок (PnL & Результаты исполнения)</h3>
                </div>
                <span id="closed-trades-count" style="font-family: 'JetBrains Mono', monospace; font-size: 12px; color: #00f2fe; font-weight: 700;">0 сделок</span>
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

    <div class="grid-layout">
        <!-- 2. OPEN POSITIONS TABLE -->
        <div class="card" style="padding: 0;">
            <div style="padding: 16px 20px; border-bottom: 1px solid var(--border);">
                <div class="section-title">
                    <span>Открытые Позиции</span>
                    <span class="badge badge-blue" id="positions-badge">0 АКТИВНО</span>
                </div>
            </div>
            <div id="positions-container">
                <div class="empty-state">
                    🔍 Нет открытых позиций. Сканер отслеживает пробой 5-дневного диапазона (120ч).
                </div>
            </div>
        </div>

        <!-- 3. LIVE EVENT LOG -->
        <div class="card">
            <div class="section-title" style="margin-bottom: 14px;">
                <span>Журнал Событий Ядра</span>
                <span class="badge badge-green">LIVE</span>
            </div>
            <div class="feed" id="events-feed">
                <div class="feed-item">
                    <span class="feed-time">[Инициализация]</span> Ядро v2.0 подключено к потоку котировок Bybit V5.
                </div>
                <div class="feed-item">
                    <span class="feed-time">[Спецификации]</span> Загружены шаги лотности Bybit: BTC (0.001), ETH (0.01), SOL (0.1).
                </div>
                <div class="feed-item">
                    <span class="feed-time">[Келли Sizing]</span> Риск на сделку: 2.5% от эквити. Макс. плечо: 3.5x.
                </div>
                <div class="feed-item">
                    <span class="feed-time">[Пирамидинг]</span> Авто-безубыток и +50% объема при достижении +2.0 ATR.
                </div>
            </div>
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
            if (pingEl) pingEl.innerText = (data.ping_ms || 2.0) + ' ms';

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

        async function fetchStatus() {
            try {
                const response = await fetch('/api/status');
                if (!response.ok) return;
                const data = await response.json();
                updateCommonChartAndClosed(data);

                // 1. Update Metrics
                document.getElementById('metric-equity').innerText = `$${data.capital.toFixed(2)}`;
                
                const pnlEl = document.getElementById('metric-pnl');
                const pnlPctEl = document.getElementById('metric-pnl-pct');
                const pnl = data.total_net_pnl;
                const pnlPct = (pnl / 300.0) * 100.0;
                pnlEl.innerText = `${pnl >= 0 ? '+' : ''}$${pnl.toFixed(2)}`;
                pnlPctEl.innerText = `${pnl >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%`;
                pnlEl.className = `card-value ${pnl >= 0 ? 'val-pos' : 'val-neg'}`;

                document.getElementById('metric-drawdown').innerText = `${data.drawdown_pct.toFixed(2)}%`;
                
                const ksEl = document.getElementById('metric-killswitch');
                const riskPctEl = document.getElementById('metric-risk-pct');
                if (data.is_kill_switch_active) {
                    ksEl.innerText = '🔴 СБРОС (0.75%)';
                    ksEl.className = 'card-value val-neg';
                    riskPctEl.innerText = 'Защита от пилы активна';
                } else {
                    ksEl.innerText = '🟢 В НОРМЕ';
                    ksEl.className = 'card-value val-pos';
                    riskPctEl.innerText = 'Риск на сделку: 2.50%';
                }

                // Countdown
                if (data.next_candle_seconds !== undefined) {
                    const mins = Math.floor(data.next_candle_seconds / 60);
                    const secs = data.next_candle_seconds % 60;
                    document.getElementById('metric-countdown').innerText = `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
                }

                document.getElementById('positions-badge').innerText = `${data.open_positions_count} АКТИВНО`;

                // 2. Render Market Radar
                const radar = data.radar || {};
                const radarContainer = document.getElementById('radar-container');
                const rKeys = Object.keys(radar);

                function formatPrice(val) {
                    if (val === undefined || val === null) return '$0.00';
                    const num = Number(val);
                    if (num < 0.0001) return '$' + num.toFixed(6);
                    if (num < 0.1) return '$' + num.toFixed(4);
                    if (num < 10) return '$' + num.toFixed(3);
                    return '$' + num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
                }

                // Macro BTC Banner & Countdown
                const btcStatusEl = document.getElementById('banner-btc-status');
                const btcData = radar['BTCUSDT'];
                const btcIsBear = btcData && btcData.trend && btcData.trend.includes('BEAR');
                const btcIsBull = btcData && btcData.trend && btcData.trend.includes('BULL');
                if (btcStatusEl) {
                    if (btcIsBear) {
                        btcStatusEl.innerHTML = '<span style="color:var(--accent-red);">🔴 BEARISH (Защита: Long-пробои альтов заморожены)</span>';
                    } else if (btcIsBull) {
                        btcStatusEl.innerHTML = '<span style="color:var(--accent-green);">🟢 BULLISH (Рынок разрешает пробойные покупки)</span>';
                    } else {
                        btcStatusEl.innerHTML = '<span style="color:var(--accent-yellow);">🟡 NEUTRAL (Переходная фаза)</span>';
                    }
                }
                const candleTimerEl = document.getElementById('banner-candle-timer');
                if (candleTimerEl && data.next_candle_seconds !== undefined) {
                    const mins = Math.floor(data.next_candle_seconds / 60);
                    const secs = data.next_candle_seconds % 60;
                    candleTimerEl.innerText = `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
                }

                if (rKeys.length > 0) {
                    // Sort by closest distance to breakout
                    rKeys.sort((a, b) => {
                        const minA = Math.min(radar[a].dist_to_long_pct, radar[a].dist_to_short_pct);
                        const minB = Math.min(radar[b].dist_to_long_pct, radar[b].dist_to_short_pct);
                        return minA - minB;
                    });

                    let rHtml = `
                        <table>
                            <thead>
                                <tr>
                                    <th>Монета</th>
                                    <th>Текущая цена</th>
                                    <th>Пробой вверх (Long)</th>
                                    <th>Дистанция до Long</th>
                                    <th>Пробой вниз (Short)</th>
                                    <th>Дистанция до Short</th>
                                    <th>Объем / MA20</th>
                                    <th>Статус</th>
                                </tr>
                            </thead>
                            <tbody>
                    `;
                    for (const sym of rKeys) {
                        const r = radar[sym];
                        const inPos = r.is_in_position;
                        const minDistance = Math.min(r.dist_to_long_pct, r.dist_to_short_pct);
                        
                        let statusBadge;
                        if (inPos) {
                            statusBadge = `<span class="badge badge-green">🚀 В ПОЗИЦИИ</span>`;
                        } else if (r.dist_to_long_pct === 0) {
                            if (btcIsBear && sym !== 'BTCUSDT') {
                                statusBadge = `<span class="badge" style="background:rgba(244,63,94,0.15); color:var(--accent-red); border:1px solid rgba(244,63,94,0.3); font-weight:700;" title="BTC падает (медвежий тренд). Защитный фильтр блокирует покупки альткоинов, защищая от ловушек">🛑 БЛОК: BTC BEAR</span>`;
                            } else {
                                const mins = Math.floor((data.next_candle_seconds || 0) / 60);
                                statusBadge = `<span class="badge badge-yellow" title="Верхний уровень пробит внутри часа. Ожидание закрытия 1h свечи для входа в LONG через ${mins} мин">⏳ ЖДЁТ ЗАКРЫТИЯ 1H (${mins}м)</span>`;
                            }
                        } else if (r.dist_to_short_pct === 0) {
                            const mins = Math.floor((data.next_candle_seconds || 0) / 60);
                            statusBadge = `<span class="badge" style="background:rgba(245,158,11,0.15); color:var(--accent-yellow); border:1px solid rgba(245,158,11,0.3); font-weight:700;" title="Нижняя граница Дончяна пробита внутри часа. Ожидание закрытия 1h свечи для входа в SHORT через ${mins} мин">⏳ ЖДЁТ ЗАКРЫТИЯ 1H (SHORT, ${mins}м)</span>`;
                        } else if (minDistance <= 1.0) {
                            statusBadge = `<span class="badge badge-yellow">🔥 ИМПУЛЬС (&le;1%)</span>`;
                        } else {
                            statusBadge = `<span class="badge badge-blue">СКАНИРОВАНИЕ</span>`;
                        }

                        const longTitle = r.dist_to_long_pct === 0 ? 'title="Уровень Дончяна коснулся/пробит внутри часа. Торговые решения принимаются строго по закрытию 1h бара!"' : '';

                        rHtml += `
                            <tr>
                                <td><strong>${sym}</strong></td>
                                <td style="color: var(--accent-cyan); font-weight: 600;">${formatPrice(r.current_price)}</td>
                                <td>${formatPrice(r.upper_channel)}</td>
                                <td ${longTitle}>
                                    <span style="color: var(--accent-green); font-weight:${r.dist_to_long_pct===0?'700':'normal'};">+${r.dist_to_long_pct}% ${r.dist_to_long_pct===0?'⏳':''}</span>
                                    <div class="bar-container"><div class="bar-fill" style="width: ${Math.max(5, 100 - r.dist_to_long_pct * 15)}%;"></div></div>
                                </td>
                                <td>${formatPrice(r.lower_channel)}</td>
                                <td>
                                    <span style="color: var(--accent-red);">-${r.dist_to_short_pct}%</span>
                                </td>
                                <td>${r.volume_ratio}x</td>
                                <td>${statusBadge}</td>
                            </tr>
                        `;
                    }
                    rHtml += `</tbody></table>`;
                    radarContainer.innerHTML = rHtml;
                }

                // 3. Render Open Positions Table
                const container = document.getElementById('positions-container');
                const positions = data.open_positions || {};
                const keys = Object.keys(positions);

                if (keys.length === 0) {
                    container.innerHTML = `
                        <div class="empty-state">
                            🔍 Нет открытых позиций. Сканер отслеживает пробой 5-дневного диапазона (120ч) среди 18 монет.
                        </div>
                    `;
                } else {
                    let html = `
                        <table>
                            <thead>
                                <tr>
                                    <th>Монета</th>
                                    <th>Тип</th>
                                    <th>Объём</th>
                                    <th>Вход ($)</th>
                                    <th>Текущая ($)</th>
                                    <th>Стоп-Лосс ($)</th>
                                    <th>Unr. PnL</th>
                                    <th>Пирамидинг</th>
                                </tr>
                            </thead>
                            <tbody>
                    `;
                    for (const sym of keys) {
                        const pos = positions[sym];
                        const isLong = pos.direction === 'LONG';
                        const unr = pos.unrealized_pnl;
                        const pyrBadge = pos.is_pyramided 
                            ? `<span class="badge badge-green">🚀 +50% ДОЛИВКА</span>` 
                            : `<span class="badge badge-yellow">ОЖИДАНИЕ +2 ATR</span>`;

                        html += `
                            <tr>
                                <td><strong>${sym}</strong></td>
                                <td><span class="badge ${isLong ? 'badge-green' : 'badge-red'}">${pos.direction}</span></td>
                                <td>${pos.qty}</td>
                                <td>${formatPrice(pos.entry_price)}</td>
                                <td>${formatPrice(pos.current_price)}</td>
                                <td>${formatPrice(pos.current_stop)}</td>
                                <td class="${unr >= 0 ? 'val-pos' : 'val-neg'}">${unr >= 0 ? '+' : ''}$${unr.toFixed(2)}</td>
                                <td>${pyrBadge}</td>
                            </tr>
                        `;
                    }
                    html += `</tbody></table>`;
                    container.innerHTML = html;
                }

            } catch (err) {
                console.error("Error polling live status:", err);
            }
        }

        async function triggerDemoOpen(sym = "SOLUSDT") {
            try {
                await fetch(`/api/demo_open?symbol=${sym}`, { method: 'POST' });
                addEvent(`Тестовая симуляция: Открыт тестовый лонг по ${sym}`);
                fetchStatus();
            } catch (e) {
                console.error(e);
            }
        }

        async function triggerDemoClose() {
            try {
                await fetch('/api/demo_close', { method: 'POST' });
                addEvent("Тестовая симуляция: Все позиции закрыты вручную по рынку");
                fetchStatus();
            } catch (e) {
                console.error(e);
            }
        }

        function addEvent(text) {
            const feed = document.getElementById('events-feed');
            const item = document.createElement('div');
            item.className = 'feed-item';
            const timeStr = new Date().toLocaleTimeString();
            item.innerHTML = `<span class="feed-time">[${timeStr}]</span> ${text}`;
            feed.insertBefore(item, feed.firstChild);
        }

        setInterval(fetchStatus, 2000);
        fetchStatus();
    </script>
</body>
</html>
"""


class DashboardHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/" or parsed.path == "/index.html":
            payload = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif parsed.path == "/api/status":
            json_file = REPORTS_DIR / "PAPER_TRADING_LIVE.json"
            payload = None
            if json_file.exists():
                try:
                    payload = json_file.read_bytes()
                except Exception as e:
                    logger.error(f"Error reading status JSON: {e}")

            if payload is None:
                fallback = {
                    "capital": 300.0,
                    "peak_equity": 300.0,
                    "drawdown_pct": 0.0,
                    "is_kill_switch_active": False,
                    "total_net_pnl": 0.0,
                    "win_rate_pct": 0.0,
                    "open_positions_count": 0,
                    "radar": {},
                    "open_positions": {},
                    "closed_trades_count": 0,
                }
                payload = json.dumps(fallback).encode("utf-8")

            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif parsed.path == "/api/state":
            state_file = REPORTS_DIR / "BOT_STATE_PERSISTENT.json"
            payload = state_file.read_bytes() if state_file.exists() else b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif parsed.path == "/api/logs":
            payload = json.dumps(list(LOG_BUFFER), indent=2).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        global GLOBAL_TRADER
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/demo_open":
            qs = urllib.parse.parse_qs(parsed.query)
            sym = qs.get("symbol", ["SOLUSDT"])[0]
            if GLOBAL_TRADER:
                GLOBAL_TRADER.open_demo_position(sym, 1)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            msg = json.dumps({"status": "ok", "action": "demo_open", "symbol": sym}).encode("utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
        elif parsed.path == "/api/demo_close":
            if GLOBAL_TRADER:
                GLOBAL_TRADER.close_all_positions()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            msg = b'{"status": "ok", "action": "demo_close"}'
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        return


def start_server_and_bot():
    global GLOBAL_TRADER
    # 1. Initialize trader with full 18-symbol universe
    GLOBAL_TRADER = PaperTraderV2(
        initial_capital=Decimal("300.0"),
        poll_interval_seconds=5,
    )

    # 2. Run PaperTrader in background thread
    bot_thread = threading.Thread(target=GLOBAL_TRADER.run_daemon, daemon=True)
    bot_thread.start()
    logger.info("Background PaperTrader thread launched.")

    # 3. Start Multi-Threaded HTTP server
    handler = DashboardHTTPRequestHandler
    with socketserver.ThreadingTCPServer(("", PORT), handler) as httpd:
        httpd.allow_reuse_address = True
        httpd.daemon_threads = True
        url = f"http://localhost:{PORT}"
        print("\n" + "=" * 70)
        print(f"[OK] LIVE QUANTITATIVE DASHBOARD STARTED AT: {url}")
        print(f"[OK] Bybit V5 Live Paper Trading active on 18 perpetual pairs")
        print("=" * 70 + "\n")
        logger.info(f"Serving web dashboard at {url}")
        httpd.serve_forever()


if __name__ == "__main__":
    start_server_and_bot()
