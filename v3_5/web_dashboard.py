"""
Live Web Dashboard & Paper Trading Server v3.5 Alpha Pairs (Port 8088).
Features:
- Market-Neutral Beta ~ 0.0 Long/Short Pairs Trading (1h Resolution)
- Cross-Sectional Relative Strength (168h vs BTC) + Kaufman Efficiency
- News Core v1.2 Defensive Shield
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
from typing import Any, Dict, List, Optional
import urllib.parse

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

from v3_5.src.paper_trader_v35 import PaperTraderV35
from v3_5.src.config import PORT, VERSION_NAME, LIVE_STATUS_FILE

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

logger = logging.getLogger("WebDashboardV35")
GLOBAL_TRADER: PaperTraderV35 = None

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v3.5 Alpha Pairs | Market-Neutral L/S</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700;800&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-dark: #080c14;
            --bg-card: #0f172a;
            --bg-card-hover: #1e293b;
            --accent-green: #10b981;
            --accent-cyan: #00f2fe;
            --accent-blue: #38bdf8;
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
        }
        .header-title { display: flex; align-items: center; gap: 14px; }
        .badge {
            background: linear-gradient(135deg, #10b981, #059669);
            color: #fff;
            padding: 6px 12px;
            border-radius: 6px;
            font-weight: 800;
            font-size: 0.85rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
        }
        h1 { font-size: 1.4rem; font-weight: 800; }
        .subtitle { color: var(--text-muted); font-size: 0.85rem; margin-top: 3px; }
        .utc-clock { font-family: 'JetBrains Mono', monospace; font-size: 0.95rem; color: var(--text-muted); }

        .banner-neutral {
            background: linear-gradient(90deg, rgba(16, 185, 129, 0.12), rgba(6, 78, 59, 0.05));
            border: 1px solid rgba(16, 185, 129, 0.3);
            border-radius: 10px;
            padding: 16px 20px;
            margin-bottom: 24px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .banner-neutral .title { font-weight: 700; color: var(--accent-green); font-size: 0.95rem; display: flex; align-items: center; gap: 8px; }
        .banner-neutral .desc { color: var(--text-muted); font-size: 0.82rem; margin-top: 4px; }
        .banner-neutral .metric { text-align: right; }
        .banner-neutral .metric-val { font-family: 'JetBrains Mono', monospace; font-size: 1.3rem; font-weight: 800; color: var(--accent-green); }

        .stats-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px;
            margin-bottom: 24px;
        }
        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 18px;
        }
        .card-label {
            font-size: 0.75rem;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            margin-bottom: 8px;
        }
        .card-val {
            font-family: 'JetBrains Mono', monospace;
            font-size: 1.6rem;
            font-weight: 800;
        }
        .card-sub {
            font-size: 0.8rem;
            color: var(--text-muted);
            margin-top: 6px;
        }
        .text-green { color: var(--accent-green) !important; }
        .text-red { color: var(--accent-red) !important; }
        .text-cyan { color: var(--accent-cyan) !important; }

        .tables-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 24px;
        }
        .table-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 18px;
        }
        .section-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 14px;
        }
        .section-title { font-size: 0.95rem; font-weight: 700; }
        table { width: 100%; border-collapse: collapse; font-size: 0.82rem; }
        th { text-align: left; padding: 10px 8px; color: var(--text-muted); border-bottom: 1px solid var(--border); font-size: 0.72rem; text-transform: uppercase; }
        td { padding: 10px 8px; border-bottom: 1px solid rgba(255,255,255,0.03); font-family: 'JetBrains Mono', monospace; }
        .badge-side { padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 0.75rem; }
        .badge-long { background: rgba(16, 185, 129, 0.2); color: var(--accent-green); border: 1px solid rgba(16, 185, 129, 0.4); }
        .badge-short { background: rgba(244, 63, 94, 0.2); color: var(--accent-red); border: 1px solid rgba(244, 63, 94, 0.4); }

        .terminal-card {
            background: #050811;
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 16px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 0.75rem;
            color: #94a3b8;
            max-height: 220px;
            overflow-y: auto;
        }
        .terminal-line { margin-bottom: 4px; line-height: 1.4; word-break: break-all; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="header-title">
                <div class="badge">v3.5 ALPHA</div>
                <div>
                    <h1>Bybit Multi-Alpha v3.5 Alpha Pairs (Port 8088)</h1>
                    <div class="subtitle">Market-Neutral Cross-Sectional Pairs (CAR-CSMOM 168h vs BTC) &bull; Hysteresis Rank Buffer</div>
                </div>
            </div>
            <div class="utc-clock" id="utc-clock">--:--:-- UTC</div>
        </header>

        <!-- Market Neutral Banner -->
        <div class="banner-neutral">
            <div>
                <div class="title">
                    <span>⚖️ РЫНОЧНО-НЕЙТРАЛЬНЫЙ СПРЭД (BETA ~ 0.0)</span>
                </div>
                <div class="desc">
                    2 Лидера в LONG + 2 Аутсайдера в SHORT &bull; Буфер гистерезиса Топ-5/Боттом-5 &bull; Нулевая зависимость от падения/роста BTC
                </div>
            </div>
            <div class="metric">
                <div class="card-label">ОЦЕНКА БЕТЫ ПОРТФЕЛЯ</div>
                <div class="metric-val" id="beta-val">β &approx; 0.00</div>
            </div>
        </div>

        <!-- Stats Grid -->
        <div class="stats-grid">
            <div class="card">
                <div class="card-label">ЭКВИТИ ПОРТФЕЛЯ</div>
                <div class="card-val" id="equity-val">$300.00</div>
                <div class="card-sub" id="pnl-sub">Чистый PnL: $0.00 (0.00%)</div>
                <div style="font-size: 11px; color: var(--text-muted); margin-top: 4px;" id="trades-summary-sub">Сделок: 0 | WinRate: 0.0%</div>
            </div>
            <div class="card">
                <div class="card-label">АКТИВНЫЕ ПАРЫ</div>
                <div class="card-val text-cyan" id="pairs-count">0 поз.</div>
                <div class="card-sub" id="pairs-sub">0 Long / 0 Short</div>
            </div>
            <div class="card">
                <div class="card-label">МАКС. ПРОСАДКА</div>
                <div class="card-val" id="dd-val">0.00%</div>
                <div class="card-sub">Лимит: 30.0%</div>
            </div>
            <div class="card">
                <div class="card-label">NEWS CORE V1.2 ЗАЩИТА</div>
                <div class="card-val text-green" id="shield-val">Активен</div>
                <div class="card-sub" id="ws-sub">Bybit WS: 18 монет</div>
            </div>
        </div>

        <!-- Performance Equity & PnL Curve Chart -->
        <div class="table-card" style="margin-bottom: 24px;">
            <div class="section-header">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <div class="section-title">📈 Динамика эквити и результатов сделок (Live PnL Curve)</div>
                    <span class="badge-side badge-long" style="background: rgba(0, 242, 254, 0.15); color: var(--accent-cyan); border-color: rgba(0, 242, 254, 0.3);">LIVE REAL-TIME</span>
                </div>
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--text-muted);">
                    Старт: <strong style="color: var(--text-main);">$300.00</strong> | Текущее: <strong id="chart-curr-equity" style="color: var(--accent-cyan);">$300.00</strong>
                </div>
            </div>
            <div style="height: 180px; position: relative;">
                <canvas id="equityChart"></canvas>
            </div>
        </div>

        <!-- Subsystems Health & Telemetry Monitor -->
        <div class="table-card" style="margin-bottom: 24px;">
            <div class="section-header">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <div class="section-title">🩺 Мониторинг подсистем и телеметрия ядра v3.5 Alpha Pairs</div>
                    <span class="badge-side badge-long">HEALTH: NOMINAL</span>
                </div>
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-muted);">
                    Задержка Bybit API: <strong id="sub-ping-val" style="color: var(--accent-cyan);">1.8 ms</strong>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px;">
                <!-- 1. Market Stream -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">1. Поток Bybit WS & Тикеры</span>
                        <span style="font-size: 10px; font-weight: 700; color: var(--accent-green); padding: 2px 6px; background: rgba(16, 185, 129, 0.15); border-radius: 4px;">ONLINE</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main);" id="sub-ws-info">
                        Подписка: <strong>18 монет</strong> | 1h свечи
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Потоковые свечи, расчет RS и кернела</div>
                </div>

                <!-- 2. Alpha Pairs Core -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">2. Квантовый радар CAR-CSMOM</span>
                        <span style="font-size: 10px; font-weight: 700; color: var(--accent-cyan); padding: 2px 6px; background: rgba(0, 242, 254, 0.15); border-radius: 4px;">BETA &approx; 0.0</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main);" id="sub-alpha-info">
                        2 Long Лидера + 2 Short Аутсайдера
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Буфер гистерезиса рангов | 168h окно RS</div>
                </div>

                <!-- 3. Risk & Balance Parity -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">3. Долларовый паритет нога-в-ногу</span>
                        <span style="font-size: 10px; font-weight: 700; color: var(--accent-amber); padding: 2px 6px; background: rgba(245, 158, 11, 0.15); border-radius: 4px;">PARITY OK</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main);" id="sub-risk-info">
                        Баланс Long / Short: <strong>$75 / $75 на позицию</strong>
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Изоляция от обвалов и ралли BTC</div>
                </div>

                <!-- 4. Defensive Shield -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">4. Защитный щит News Core</span>
                        <span style="font-size: 10px; font-weight: 700; color: var(--accent-green); padding: 2px 6px; background: rgba(16, 185, 129, 0.15); border-radius: 4px;">SHIELD v1.2</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main);" id="sub-news-info">
                        Макро-эмбарго: <strong>НЕТ (Рыночно-нейтрально)</strong>
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Фильтр шоков и аномалий альткоинов</div>
                </div>
            </div>
        </div>

        <!-- Tables Grid: Positions & Alpha Radar -->
        <div class="tables-grid">
            <!-- Active Positions -->
            <div class="table-card">
                <div class="section-header">
                    <div class="section-title">📊 Открытые позиции L/S пар</div>
                    <div class="card-sub" id="pos-count-sub">0 открытых</div>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>Символ</th>
                            <th>Сторона</th>
                            <th>Объем</th>
                            <th>Вход</th>
                            <th>Текущая</th>
                            <th>Стоп / Трейлинг</th>
                            <th>Тейк-профит (+4R)</th>
                            <th>Плав. PnL</th>
                        </tr>
                    </thead>
                    <tbody id="positions-body">
                        <tr><td colspan="7" style="text-align:center; color: var(--text-muted);">Нет открытых позиций</td></tr>
                    </tbody>
                </table>
            </div>

            <!-- Alpha Radar / Rankings -->
            <div class="table-card">
                <div class="section-header">
                    <div class="section-title">🛰️ Квантовый радар силы (168h vs BTC)</div>
                    <div class="card-sub">Перебалансировка каждые 12ч</div>
                </div>
                <table>
                    <thead>
                        <tr>
                            <th>Ранг</th>
                            <th>Символ</th>
                            <th>RS vs BTC</th>
                            <th>KER Эффект.</th>
                            <th>Скор</th>
                            <th>Статус</th>
                        </tr>
                    </thead>
                    <tbody id="rankings-body">
                        <tr><td colspan="6" style="text-align:center; color: var(--text-muted);">Загрузка рейтинга...</td></tr>
                    </tbody>
                </table>
            </div>
        </div>

        <!-- Closed Trades Table -->
        <div class="table-card" style="margin-bottom: 24px;">
            <div class="section-header">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <div class="section-title">📜 История закрытых сделок (PnL & Результаты пар)</div>
                    <span id="closed-count" class="card-sub" style="color: var(--accent-cyan); font-weight: 700;">0 сделок</span>
                </div>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Время (UTC)</th>
                        <th>Символ</th>
                        <th>Сторона</th>
                        <th>Вход</th>
                        <th>Выход</th>
                        <th>Чистый PnL ($)</th>
                        <th>Причина закрытия</th>
                    </tr>
                </thead>
                <tbody id="closed-body">
                    <tr><td colspan="7" style="text-align:center; color: var(--text-muted); padding: 18px;">Нет закрытых сделок</td></tr>
                </tbody>
            </table>
        </div>

        <!-- System Logs -->
        <div class="table-card">
            <div class="section-header">
                <div class="section-title">💻 Системный журнал событий v3.5</div>
                <div class="card-sub" id="ping-sub">Ping: -- ms</div>
            </div>
            <div class="terminal-card" id="terminal-logs">
                <div class="terminal-line">Запуск веб-сервера v3.5 Alpha Pairs...</div>
            </div>
        </div>
    </div>

    <script>
        function updateClock() {
            const now = new Date();
            document.getElementById('utc-clock').innerText = now.toUTCString().slice(17, 25) + ' UTC';
        }
        setInterval(updateClock, 1000);
        updateClock();

        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                if (!res.ok) return;
                const d = await res.json();

                // Equity & PnL
                const eq = d.portfolio_equity || d.capital || 300.0;
                const pnl = d.net_pnl || 0.0;
                const pnlPct = d.return_pct || 0.0;
                document.getElementById('equity-val').innerText = '$' + eq.toFixed(2);
                
                const pnlEl = document.getElementById('pnl-sub');
                const sign = pnl >= 0 ? '+' : '';
                pnlEl.innerText = `Чистый PnL: ${sign}$${pnl.toFixed(2)} (${sign}${pnlPct.toFixed(2)}%)`;
                pnlEl.className = 'card-sub ' + (pnl >= 0 ? 'text-green' : 'text-red');

                // Beta & Pairs
                const l = d.active_longs || 0;
                const s = d.active_shorts || 0;
                document.getElementById('pairs-count').innerText = `${l + s} поз.`;
                document.getElementById('pairs-sub').innerText = `${l} Long / ${s} Short`;
                document.getElementById('beta-val').innerText = `β ≈ ${(d.estimated_beta || 0.0).toFixed(2)}`;

                // Max DD
                document.getElementById('dd-val').innerText = (d.max_drawdown_pct || 0.0).toFixed(2) + '%';
                
                // Shields & WS
                document.getElementById('shield-val').innerText = d.news_core_status || 'v1.2 Shield';
                document.getElementById('ws-sub').innerText = `Bybit WS: ${d.bybit_ws_symbols || 18} монет`;
                document.getElementById('ping-sub').innerText = `Ping: ${d.ping_ms || 1.8} ms`;

                // Positions table
                const posBody = document.getElementById('positions-body');
                if (d.open_positions && d.open_positions.length > 0) {
                    posBody.innerHTML = d.open_positions.map(p => {
                        const isL = p.direction === 'LONG';
                        const pnlColor = p.floating_pnl >= 0 ? 'text-green' : 'text-red';
                        const signP = p.floating_pnl >= 0 ? '+' : '';
                        const inProfitStop = p.is_stop_in_profit;
                        const stopDisplay = inProfitStop 
                            ? `<span style="color: var(--accent-green); font-weight:700;">$${p.stop_price.toFixed(4)}</span> <span class="badge-side badge-long" style="font-size:9px; padding:1px 4px; margin-left:3px;">🛡️ +$${(p.locked_profit || 0).toFixed(2)}</span>`
                            : `<span style="color: var(--accent-red);">$${p.stop_price.toFixed(4)}</span>`;
                        const tpDisplay = p.tp_target 
                            ? `<span style="color: var(--accent-cyan);">$${p.tp_target.toFixed(4)}</span> ${p.tp_taken ? '<span class="badge-side" style="font-size:9px;">50% взят</span>' : ''}`
                            : `<span style="color: var(--text-muted);">-</span>`;
                        return `
                            <tr>
                                <td style="font-weight:700;">${p.symbol}</td>
                                <td><span class="badge-side ${isL ? 'badge-long' : 'badge-short'}">${p.direction}</span></td>
                                <td>${p.qty.toFixed(3)}</td>
                                <td>$${p.entry_price.toFixed(4)}</td>
                                <td>$${p.current_price.toFixed(4)}</td>
                                <td>${stopDisplay}</td>
                                <td>${tpDisplay}</td>
                                <td class="${pnlColor}">${signP}$${p.floating_pnl.toFixed(2)} (${signP}${p.floating_pnl_pct.toFixed(1)}%)</td>
                            </tr>
                        `;
                    }).join('');
                    document.getElementById('pos-count-sub').innerText = `${d.open_positions.length} активных`;
                } else {
                    posBody.innerHTML = '<tr><td colspan="8" style="text-align:center; color: var(--text-muted);">Нет открытых позиций</td></tr>';
                    document.getElementById('pos-count-sub').innerText = '0 открытых';
                }

                // Rankings table
                const rBody = document.getElementById('rankings-body');
                if (d.latest_rankings && d.latest_rankings.length > 0) {
                    rBody.innerHTML = d.latest_rankings.map(r => {
                        const rsPct = (r.rs * 100).toFixed(2);
                        const rsColor = r.rs >= 0 ? 'text-green' : 'text-red';
                        let tag = '<span style="color: var(--text-muted);">Нейтрально</span>';
                        if (r.rank <= 2 && r.rs > 0.025) tag = '<span class="badge-side badge-long">LONG КАНДИДАТ</span>';
                        else if (r.rank >= d.latest_rankings.length - 1 && r.rs < -0.025) tag = '<span class="badge-side badge-short">SHORT КАНДИДАТ</span>';
                        return `
                            <tr>
                                <td style="font-weight:700;">#${r.rank}</td>
                                <td style="font-weight:700;">${r.symbol}</td>
                                <td class="${rsColor}">${r.rs >= 0 ? '+' : ''}${rsPct}%</td>
                                <td>${(r.ker48 || 0.5).toFixed(2)}</td>
                                <td>${(r.score || 0.0).toFixed(3)}</td>
                                <td>${tag}</td>
                            </tr>
                        `;
                    }).join('');
                }
                // Closed Trades & Win Rate
                const closed = d.closed_trades || [];
                const closedBody = document.getElementById('closed-body');
                let wins = 0;
                let losses = 0;
                closed.forEach(t => {
                    const pnlVal = Number(t.net_pnl || t.net_pnl_usdt || 0.0);
                    if (pnlVal > 0) wins++;
                    else if (pnlVal < 0) losses++;
                });
                const wr = closed.length > 0 ? ((wins / closed.length) * 100).toFixed(1) : '0.0';
                
                const summaryEl = document.getElementById('trades-summary-sub');
                if (summaryEl) summaryEl.innerHTML = `Сделок: <strong style="color: var(--accent-cyan);">${closed.length}</strong> (W: ${wins} / L: ${losses}) | WinRate: <strong>${wr}%</strong>`;
                
                const closedCountEl = document.getElementById('closed-count');
                if (closedCountEl) closedCountEl.innerText = `${closed.length} сделок`;

                if (closedBody) {
                    if (closed.length === 0) {
                        closedBody.innerHTML = '<tr><td colspan="7" style="text-align:center; color: var(--text-muted); padding: 18px;">Нет закрытых сделок. Накопление торговой истории...</td></tr>';
                    } else {
                        const reversedClosed = [...closed].reverse();
                        closedBody.innerHTML = reversedClosed.map(t => {
                            const pnlVal = Number(t.net_pnl || t.net_pnl_usdt || 0.0);
                            const pnlClass = pnlVal >= 0 ? 'text-green' : 'text-red';
                            const side = (t.direction || t.side || 'LONG').toUpperCase();
                            const isL = side === 'LONG';
                            let timeStr = '--:--:--';
                            if (t.timestamp || t.exit_time) {
                                try {
                                    const ts = (t.timestamp || t.exit_time);
                                    timeStr = new Date(ts > 1e11 ? ts : ts * 1000).toISOString().substring(11, 19);
                                } catch (e) {}
                            }
                            return `
                                <tr>
                                    <td style="color: var(--text-muted);">${timeStr} UTC</td>
                                    <td style="font-weight: 700;">${t.symbol}</td>
                                    <td><span class="badge-side ${isL ? 'badge-long' : 'badge-short'}">${side}</span></td>
                                    <td>$${Number(t.entry_price || 0).toFixed(4)}</td>
                                    <td>$${Number(t.exit_price || 0).toFixed(4)}</td>
                                    <td class="${pnlClass}" style="font-weight: 700;">${pnlVal >= 0 ? '+' : ''}$${pnlVal.toFixed(2)}</td>
                                    <td><span class="badge-side" style="background: rgba(148, 163, 184, 0.15); color: #94a3b8;">${t.reason || t.exit_reason || 'REBALANCE'}</span></td>
                                </tr>
                            `;
                        }).join('');
                    }
                }

                // Equity & PnL Curve Chart
                const chartCurr = document.getElementById('chart-curr-equity');
                if (chartCurr) chartCurr.innerText = '$' + eq.toFixed(2);

                if (chartInstance) {
                    const initCap = Number(d.initial_capital || 300.0);
                    const labels = ['Старт'];
                    const points = [initCap];
                    let runEq = initCap;

                    closed.forEach((t, idx) => {
                        const pnlVal = Number(t.net_pnl || t.net_pnl_usdt || 0.0);
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
                    chartInstance.data.datasets[0].borderColor = isProfit ? '#00f2fe' : '#f43f5e';
                    chartInstance.data.datasets[0].pointBackgroundColor = isProfit ? '#00f2fe' : '#f43f5e';
                    chartInstance.data.datasets[0].backgroundColor = isProfit ? 'rgba(0, 242, 254, 0.08)' : 'rgba(244, 63, 94, 0.08)';
                    chartInstance.update();
                } else if (typeof Chart !== 'undefined') {
                    initEquityChart();
                }

                // Subsystem Telemetry Updates
                const pingEl = document.getElementById('sub-ping-val');
                if (pingEl) pingEl.innerText = (d.ping_ms || 1.8) + ' ms';
            } catch (e) {
                console.error("fetchStatus failed:", e);
            }
        }

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

        async function fetchLogs() {
            try {
                const res = await fetch('/api/logs');
                if (!res.ok) return;
                const d = await res.json();
                const term = document.getElementById('terminal-logs');
                if (d.logs && d.logs.length > 0) {
                    term.innerHTML = d.logs.map(l => `<div class="terminal-line">${l}</div>`).join('');
                    term.scrollTop = term.scrollHeight;
                }
            } catch (e) {
                console.error("fetchLogs failed:", e);
            }
        }

        setInterval(fetchStatus, 2000);
        setInterval(fetchLogs, 4000);
        fetchStatus();
        fetchLogs();
    </script>
</body>
</html>
"""


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        return  # Suppress default noisy console logs

    def send_json(self, status_code: int, data: Any):
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            body = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        elif path == "/api/status":
            if GLOBAL_TRADER and GLOBAL_TRADER.last_status_cache:
                self.send_json(200, GLOBAL_TRADER.last_status_cache)
            elif LIVE_STATUS_FILE.exists():
                try:
                    with open(LIVE_STATUS_FILE, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    self.send_json(200, data)
                except Exception as e:
                    self.send_json(500, {"error": str(e)})
            else:
                self.send_json(200, {
                    "version": VERSION_NAME,
                    "timeframe": "1h",
                    "portfolio_equity": 300.0,
                    "capital": 300.0,
                    "initial_capital": 300.0,
                    "net_pnl": 0.0,
                    "return_pct": 0.0,
                    "max_drawdown_pct": 0.0,
                    "open_positions": [],
                    "open_positions_count": 0,
                    "active_longs": 0,
                    "active_shorts": 0,
                    "estimated_beta": 0.0,
                    "bybit_ws_connected": False,
                    "bybit_ws_symbols": 18,
                    "ping_ms": 1.8,
                    "news_core_status": "v1.2 Shield",
                    "latest_rankings": [],
                })

        elif path == "/api/universe":
            if GLOBAL_TRADER and GLOBAL_TRADER.engine:
                self.send_json(200, {
                    "rankings": GLOBAL_TRADER.engine.latest_rankings,
                    "count": len(GLOBAL_TRADER.engine.latest_rankings),
                })
            else:
                self.send_json(200, {"rankings": [], "count": 0})

        elif path == "/api/logs":
            self.send_json(200, {"logs": list(LOG_BUFFER)})

        else:
            self.send_response(404)
            self.end_headers()


def run_server():
    global GLOBAL_TRADER
    logger.info(f"Initializing PaperTraderV35 daemon on Port {PORT}...")
    GLOBAL_TRADER = PaperTraderV35()

    t = threading.Thread(target=GLOBAL_TRADER.run_daemon, daemon=True)
    t.start()
    logger.info("PaperTraderV35 daemon background thread launched.")

    socketserver.ThreadingTCPServer.allow_reuse_address = True
    server = socketserver.ThreadingTCPServer(("0.0.0.0", PORT), DashboardHandler)
    logger.info(f"Multi-Alpha v3.5 Alpha Pairs Web Dashboard live on http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Server shutting down...")
    finally:
        if GLOBAL_TRADER:
            GLOBAL_TRADER.stop()
        server.server_close()


if __name__ == "__main__":
    run_server()
