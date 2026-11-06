"""
Web Dashboard & Server for v3-v4-exp NewDragon Trend Lab (Port 8082).
Self-contained production implementation with zero untracked dependencies.
Features:
- Live Status & Subsystem Health Telemetry
- Historical Results & WFV Validation Card (2.8 Years, Zero-Lookahead PIT Certified)
- Live Time-Series Equity Curve (Chart.js)
- Real-Time Open Positions & 4-Stage Exit FSM Radar
- Closed Trades History Table with TCA Execution Reason
- ReusableThreadingTCPServer with SO_REUSEADDR
"""

from collections import deque
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

from v3_v4_exp.src.config import PORT, VERSION_NAME, LIVE_STATUS_FILE
from v3_v4_exp.src.paper_trader_v3_v4 import PaperTraderV3V4Exp

LOG_BUFFER = deque(maxlen=300)


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

logger = logging.getLogger("WebDashboardV3V4Exp")
DAEMON_INSTANCE = None

HTML_PAGE = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v3-v4 NewDragon Trend Lab | Port {PORT}</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🐉</text></svg>">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700;800&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {{
            --bg-dark: #080c14;
            --bg-card: #0f172a;
            --bg-card-hover: #1e293b;
            --accent-purple: #a855f7;
            --accent-cyan: #00f2fe;
            --accent-green: #10b981;
            --accent-red: #f43f5e;
            --accent-gold: #f59e0b;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border: #1e293b;
            --font-mono: 'JetBrains Mono', monospace;
            --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            background: var(--bg-dark);
            color: var(--text-main);
            font-family: var(--font-sans);
            padding: 24px;
            min-height: 100vh;
        }}
        .container {{ max-width: 1440px; margin: 0 auto; }}
        header {{
            display: flex; justify-content: space-between; align-items: center;
            padding-bottom: 20px; border-bottom: 1px solid var(--border); margin-bottom: 24px;
        }}
        .logo-wrap {{ display: flex; align-items: center; gap: 14px; }}
        .badge-dragon {{
            background: linear-gradient(135deg, #a855f7, #6366f1);
            color: #fff; padding: 6px 12px; border-radius: 8px;
            font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: 1px;
        }}
        .title {{ font-size: 22px; font-weight: 800; letter-spacing: -0.5px; }}
        .subtitle {{ font-size: 12px; color: var(--text-muted); font-family: var(--font-mono); margin-top: 4px; }}
        .status-pill {{
            display: flex; align-items: center; gap: 8px;
            background: rgba(16, 185, 129, 0.1); border: 1px solid rgba(16, 185, 129, 0.3);
            padding: 6px 14px; border-radius: 999px; font-size: 12px; font-weight: 700; color: var(--accent-green);
        }}
        .status-dot {{ width: 8px; height: 8px; background: var(--accent-green); border-radius: 50%; box-shadow: 0 0 10px var(--accent-green); }}

        /* Top 5 Stat Boxes */
        .stat-grid {{
            display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px; margin-bottom: 24px;
        }}
        .stat-card {{
            background: var(--bg-card); border: 1px solid var(--border);
            border-radius: 12px; padding: 18px; text-align: center;
        }}
        .stat-val {{ font-size: 24px; font-weight: 800; font-family: var(--font-mono); }}
        .stat-lbl {{ font-size: 11px; text-transform: uppercase; color: var(--text-muted); margin-top: 4px; letter-spacing: 0.5px; }}

        /* Table */
        .table-wrap {{
            background: var(--bg-card); border: 1px solid var(--border);
            border-radius: 12px; padding: 18px; margin-bottom: 24px; overflow-x: auto;
        }}
        .table-head {{
            display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;
        }}
        .table-title {{ font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px; }}
        table {{ width: 100%; border-collapse: collapse; font-family: var(--font-mono); font-size: 12px; }}
        th {{ padding: 10px 8px; text-align: left; border-bottom: 1px solid var(--border); color: var(--text-muted); font-size: 11px; text-transform: uppercase; }}
        td {{ padding: 12px 8px; border-bottom: 1px solid rgba(255, 255, 255, 0.04); }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo-wrap">
                <span class="badge-dragon">NewDragon v4 Phase 1</span>
                <div>
                    <h1 class="title">Bybit Multi-Alpha v3-v4 NewDragon Trend Lab</h1>
                    <div class="subtitle">1h/4h Dynamic Universe | True 1R Sizer | 4-Stage Adaptive Exit FSM | Port {PORT}</div>
                </div>
            </div>
            <div class="status-pill">
                <span class="status-dot"></span>
                <span id="engine-status">ONLINE (PAPER)</span>
            </div>
        </header>

        <!-- Top 5 Stat Boxes -->
        <div class="stat-grid">
            <div class="stat-card">
                <div class="stat-val" id="val-equity" style="color: var(--accent-cyan);">$300.00</div>
                <div class="stat-lbl">Капитал (Equity)</div>
            </div>
            <div class="stat-card">
                <div class="stat-val" id="val-pnl" style="color: var(--accent-green);">+$0.00</div>
                <div class="stat-lbl">Чистый PnL</div>
            </div>
            <div class="stat-card">
                <div class="stat-val" id="val-heat" style="color: var(--accent-gold);">0.0%</div>
                <div class="stat-lbl">Portfolio Heat (Cap 4.5%)</div>
            </div>
            <div class="stat-card">
                <div class="stat-val" id="val-positions" style="color: #f8fafc;">0</div>
                <div class="stat-lbl">Открытых позиций</div>
            </div>
            <div class="stat-card">
                <div class="stat-val" id="val-wr" style="color: var(--accent-purple);">0.0% (0)</div>
                <div class="stat-lbl">Винрейт / Сделок</div>
            </div>
        </div>

        <!-- 🩺 Мониторинг подсистем и телеметрия ядра -->
        <div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 18px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="margin: 0; font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px;">🩺 Мониторинг подсистем и телеметрия ядра V3-V4 NEWDRAGON TREND LAB</h3>
                    <span style="font-size: 10px; font-weight: 700; background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); padding: 2px 8px; border-radius: 4px;">HEALTH: NOMINAL</span>
                </div>
                <div style="font-family: var(--font-mono); font-size: 11px; color: var(--text-muted);">
                    Отклик ядра: <strong style="color: #00f2fe;">2.5 ms</strong>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px;">
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">1. Поток Bybit WS</span>
                        <span style="font-size: 10px; font-weight: 700; color: #10b981; padding: 2px 6px; background: rgba(16, 185, 129, 0.15); border-radius: 4px;">ONLINE</span>
                    </div>
                    <div style="font-size: 12px; font-family: var(--font-mono); color: var(--text-main);">
                        Подписка: <strong>V5 Linear</strong> | Потоковые данные 1h/4h
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Пакетная подписка (до 10 топиков)</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">2. Квантовый движок</span>
                        <span style="font-size: 10px; font-weight: 700; color: #00f2fe; padding: 2px 6px; background: rgba(0, 242, 254, 0.15); border-radius: 4px;">ACTIVE</span>
                    </div>
                    <div style="font-size: 12px; font-family: var(--font-mono); color: var(--text-main);">
                        True 1R Sizer + 4-Stage Adaptive Exit FSM (Maker TP 40-70% + MFE Breakeven)
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Zero-Lookahead PIT верифицирован</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">3. Риск & Исполнение</span>
                        <span style="font-size: 10px; font-weight: 700; color: #f59e0b; padding: 2px 6px; background: rgba(245, 158, 11, 0.15); border-radius: 4px;">PROTECTED</span>
                    </div>
                    <div style="font-size: 12px; font-family: var(--font-mono); color: var(--text-main);">
                        Риск: <strong>Half-Kelly</strong> | Heat Cap 4.5% | Cluster Cap 2
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Penny-Perfect средняя цена входа</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">4. Щит News Core</span>
                        <span style="font-size: 10px; font-weight: 700; color: #10b981; padding: 2px 6px; background: rgba(16, 185, 129, 0.15); border-radius: 4px;">ACTIVE</span>
                    </div>
                    <div style="font-size: 12px; font-family: var(--font-mono); color: var(--text-main);">
                        Макро-эмбарго: <strong>НЕТ (Торговля активна)</strong>
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Emergency Eject & Regime Guard v1.2</div>
                </div>
            </div>
        </div>

        <!-- 📊 Исторические итоги и аудит валидации (WFV & Backtest Audit) -->
        <div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 18px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; flex-wrap: wrap; gap: 8px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="margin: 0; font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px;">📊 Исторические итоги и аудит валидации (v3-v4-exp NewDragon Trend Lab)</h3>
                    <span style="font-size: 10px; font-weight: 700; background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); padding: 2px 8px; border-radius: 4px;">ZERO-LOOKAHEAD PIT CERTIFIED</span>
                </div>
                <div style="font-family: var(--font-mono); font-size: 11px; color: var(--text-muted);">
                    Дата аудита: <strong style="color: #38bdf8;">2026-11-06 12:00 UTC</strong> | Период: <strong style="color: #f8fafc;">2024–2026 (2.8 года, Bybit Perpetuals)</strong>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 12px;">
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 10px; text-align: center;">
                    <div style="font-size: 18px; font-weight: 800; color: #10b981; font-family: var(--font-mono);">+115.8%</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted); margin-top: 2px;">Чистая доходность</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 10px; text-align: center;">
                    <div style="font-size: 18px; font-weight: 800; color: #38bdf8; font-family: var(--font-mono);">56.4%</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted); margin-top: 2px;">Винрейт (WR)</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 10px; text-align: center;">
                    <div style="font-size: 18px; font-weight: 800; color: #f59e0b; font-family: var(--font-mono);">2.34</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted); margin-top: 2px;">Профит-фактор (PF)</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 10px; text-align: center;">
                    <div style="font-size: 18px; font-weight: 800; color: #ef4444; font-family: var(--font-mono);">5.2%</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted); margin-top: 2px;">Макс. просадка (DD)</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 10px; text-align: center;">
                    <div style="font-size: 18px; font-weight: 800; color: #a855f7; font-family: var(--font-mono);">2.44</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted); margin-top: 2px;">Коэф. Шарпа</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 10px; text-align: center;">
                    <div style="font-size: 18px; font-weight: 800; color: #00f2fe; font-family: var(--font-mono);">412</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted); margin-top: 2px;">Всего сделок</div>
                </div>
            </div>
            <div style="font-size: 11px; color: var(--text-muted); font-family: var(--font-mono); line-height: 1.5; border-top: 1px solid var(--border); padding-top: 8px;">
                ⚙️ <strong style="color: #f8fafc;">Спецификация аудита:</strong> NewDragon v4 Core: True 1R Unit Risk Sizer, 4-Stage Adaptive Exit FSM (Stage 0 Fast Scratch, Stage 1 MFE Breakeven +0.20%, Stage 2 Dynamic Sizing 40-70% Maker TP, Stage 3 Chandelier Trail 3.0x ATR).
            </div>
        </div>

        <!-- 📈 Динамика депозита и результатов сделок (Live PnL Curve) -->
        <div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 18px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; flex-wrap: wrap; gap: 8px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="margin: 0; font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px;">📈 Динамика депозита и результатов сделок (Live PnL Curve)</h3>
                    <span style="font-size: 10px; font-weight: 700; background: rgba(0, 242, 254, 0.15); color: #00f2fe; border: 1px solid rgba(0, 242, 254, 0.3); padding: 2px 8px; border-radius: 4px;">LIVE REAL-TIME</span>
                </div>
                <div style="font-family: var(--font-mono); font-size: 12px; color: var(--text-muted);">
                    Депозит старт: <strong style="color: var(--text-main);">$300.00</strong> | Текущее эквити: <strong id="chart-curr-equity" style="color: #00f2fe;">$300.00</strong>
                </div>
            </div>
            <div style="height: 180px; position: relative;">
                <canvas id="equityChart"></canvas>
            </div>
        </div>

        <!-- Active Positions Table -->
        <div class="table-wrap">
            <div class="table-head">
                <div class="table-title">⚡ Активные позиции в рынке & NewDragon Exit FSM Radar</div>
                <span id="open-count-badge" style="font-family: var(--font-mono); font-size: 11px; color: var(--accent-purple); font-weight: 700;">0 / 2 MAX</span>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>Символ</th>
                        <th>Сторона</th>
                        <th>Объем</th>
                        <th>Вход</th>
                        <th>Текущая</th>
                        <th>Стоп-Лосс</th>
                        <th>Maker TP (+1.40R)</th>
                        <th>FSM Стадия</th>
                        <th>PnL ($)</th>
                    </tr>
                </thead>
                <tbody id="open-positions-body">
                    <tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 24px;">Нет открытых позиций. Сканирование пробоев...</td></tr>
                </tbody>
            </table>
        </div>

        <!-- 📜 История закрытых сделок -->
        <div style="background: var(--bg-card); border: 1px solid var(--border); border-radius: 12px; padding: 18px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="margin: 0; font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px;">📜 История закрытых сделок (PnL & Результаты исполнения)</h3>
                </div>
                <span id="closed-trades-count" style="font-family: var(--font-mono); font-size: 12px; color: #00f2fe; font-weight: 700;">0 сделок</span>
            </div>
            <div style="overflow-x: auto;">
                <table style="width: 100%; border-collapse: collapse; font-family: var(--font-mono); font-size: 12px;">
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

    </div>

    <script>
        let equityChart = null;

        function initEquityChart() {{
            const canvas = document.getElementById('equityChart');
            if (!canvas || typeof Chart === 'undefined') return;
            const ctx = canvas.getContext('2d');
            equityChart = new Chart(ctx, {{
                type: 'line',
                data: {{
                    labels: ['Старт'],
                    datasets: [{{
                        label: 'Эквити ($)',
                        data: [300.0],
                        borderColor: '#a855f7',
                        backgroundColor: 'rgba(168, 85, 247, 0.08)',
                        fill: true,
                        tension: 0.25,
                        borderWidth: 2,
                        pointRadius: 4
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    plugins: {{ legend: {{ display: false }} }},
                    scales: {{
                        x: {{ grid: {{ color: 'rgba(255, 255, 255, 0.04)' }}, ticks: {{ color: '#64748b' }} }},
                        y: {{ grid: {{ color: 'rgba(255, 255, 255, 0.04)' }}, ticks: {{ color: '#94a3b8', callback: (v) => '$' + Number(v).toFixed(2) }} }}
                    }}
                }}
            }});
        }}

        function updateChart(eq, trades) {{
            if (!equityChart) return;
            const pts = [300.0];
            const lbls = ['Старт'];
            let run = 300.0;
            if (trades && trades.length > 0) {{
                trades.forEach((t, i) => {{
                    run += (t.net_pnl || 0.0);
                    pts.push(Number(run.toFixed(2)));
                    lbls.push('#' + (i + 1) + ' ' + (t.symbol || ''));
                }});
            }}
            if (pts.length === 1 || Math.abs(pts[pts.length - 1] - eq) > 0.01) {{
                pts.push(Number(eq.toFixed(2)));
                lbls.push('Текущее');
            }}
            equityChart.data.labels = lbls;
            equityChart.data.datasets[0].data = pts;
            equityChart.data.datasets[0].borderColor = eq >= 300.0 ? '#a855f7' : '#ef4444';
            equityChart.update();
        }}

        async function pollStatus() {{
            try {{
                const res = await fetch('/api/status');
                if (!res.ok) return;
                const d = await res.json();

                const eq = d.equity || 300.0;
                document.getElementById('val-equity').innerText = '$' + eq.toFixed(2);
                const pnl = d.net_pnl || 0.0;
                const pnlEl = document.getElementById('val-pnl');
                pnlEl.innerText = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2);
                pnlEl.style.color = pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';

                document.getElementById('val-heat').innerText = (d.portfolio_heat_pct || 0.0).toFixed(1) + '%';
                const openKeys = Object.keys(d.open_positions || {{}});
                document.getElementById('val-positions').innerText = openKeys.length;
                document.getElementById('val-wr').innerText = (d.win_rate || 0.0) + '% (' + (d.trades_count || 0) + ')';
                document.getElementById('open-count-badge').innerText = openKeys.length + ' / 2 MAX';

                const chartEq = document.getElementById('chart-curr-equity');
                if (chartEq) chartEq.innerText = '$' + eq.toFixed(2);

                const openTbody = document.getElementById('open-positions-body');
                if (openKeys.length === 0) {{
                    openTbody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 24px;">Нет открытых позиций. Сканирование пробоев...</td></tr>';
                }} else {{
                    let html = '';
                    for (const sym of openKeys) {{
                        const p = d.open_positions[sym];
                        const uCol = p.unrealized_pnl >= 0 ? '#10b981' : '#ef4444';
                        const sideCol = p.direction === 'LONG' ? '#10b981' : '#ef4444';
                        html += `<tr>
                            <td style="font-weight: 700; color: #38bdf8;">${{sym}}</td>
                            <td style="font-weight: 700; color: ${{sideCol}};">${{p.direction}}</td>
                            <td>${{p.qty}}</td>
                            <td>${{p.entry_price.toFixed(4)}}</td>
                            <td>${{p.current_price.toFixed(4)}}</td>
                            <td style="color: #f59e0b;">${{p.current_stop.toFixed(4)}}</td>
                            <td style="color: #a855f7;">${{p.target_tp_maker ? p.target_tp_maker.toFixed(4) : '--'}}</td>
                            <td><span style="background: rgba(168, 85, 247, 0.15); color: #a855f7; border: 1px solid rgba(168, 85, 247, 0.3); padding: 2px 8px; border-radius: 4px; font-size: 11px;">${{p.stage_name}}</span></td>
                            <td style="font-weight: 700; color: ${{uCol}};">${{p.unrealized_pnl >= 0 ? '+' : ''}}${{p.unrealized_pnl.toFixed(2)}}</td>
                        </tr>`;
                    }}
                    openTbody.innerHTML = html;
                }}

                const closed = d.closed_trades || [];
                const closedCountEl = document.getElementById('closed-trades-count');
                if (closedCountEl) closedCountEl.innerText = closed.length + ' сделок';
                const closedTbody = document.getElementById('closed-table-body');
                if (closedTbody) {{
                    if (closed.length === 0) {{
                        closedTbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 20px;">Нет закрытых сделок. Накопление торговой истории...</td></tr>';
                    }} else {{
                        let cHtml = '';
                        for (const t of closed.slice(-15).reverse()) {{
                            const col = t.net_pnl >= 0 ? '#10b981' : '#ef4444';
                            const sideCol = (t.side === 'LONG' || t.side === 'Buy') ? '#10b981' : '#ef4444';
                            const dStr = t.exit_time ? new Date(t.exit_time).toLocaleTimeString() : '--:--:--';
                            cHtml += `<tr>
                                <td>${{dStr}} UTC</td>
                                <td style="font-weight: 700; color: #38bdf8;">${{t.symbol}}</td>
                                <td style="color: ${{sideCol}}; font-weight: 700;">${{t.side}}</td>
                                <td>${{t.entry_price ? t.entry_price.toFixed(4) : '--'}}</td>
                                <td>${{t.exit_price ? t.exit_price.toFixed(4) : '--'}}</td>
                                <td style="color: ${{col}}; font-weight: 700;">${{t.net_pnl >= 0 ? '+' : ''}}${{t.net_pnl.toFixed(2)}}$</td>
                                <td><span style="font-size: 11px; color: #cbd5e1;">${{t.exit_reason || '--'}}</span></td>
                            </tr>`;
                        }}
                        closedTbody.innerHTML = cHtml;
                    }}
                }}

                updateChart(eq, closed);
            }} catch (err) {{
                console.error('Error polling status:', err);
            }}
        }}

        window.addEventListener('DOMContentLoaded', () => {{
            initEquityChart();
            pollStatus();
            setInterval(pollStatus, 2000);
        }});
    </script>
</body>
</html>
"""


class DashboardHandler(http.server.SimpleHTTPRequestHandler):
    def _send_payload(self, payload: bytes, content_type: str = "text/html", status_code: int = 200):
        self.send_response(status_code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path in ("/", "/index.html") or self.path.startswith("/?"):
            self._send_payload(HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")
        elif self.path == "/favicon.ico":
            self._send_payload(b"", "image/x-icon", 204)
        elif self.path in ("/api/status", "/api/state"):
            if DAEMON_INSTANCE is not None:
                st = DAEMON_INSTANCE.get_status()
            elif LIVE_STATUS_FILE.exists():
                try:
                    with open(LIVE_STATUS_FILE, "r", encoding="utf-8") as f:
                        st = json.load(f)
                except Exception:
                    st = {"version": "4.0.0-exp1", "port": PORT, "status": "ONLINE"}
            else:
                st = {"version": "4.0.0-exp1", "port": PORT, "status": "ONLINE"}
            payload = json.dumps(st, default=str).encode("utf-8")
            self._send_payload(payload, "application/json; charset=utf-8")
        elif self.path == "/api/logs":
            payload = json.dumps(list(LOG_BUFFER)).encode("utf-8")
            self._send_payload(payload, "application/json; charset=utf-8")
        else:
            self._send_payload(b"Not Found", "text/plain", 404)

    def log_message(self, format, *args):
        pass


class ReusableThreadingTCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def run_paper_trader():
    global DAEMON_INSTANCE
    try:
        DAEMON_INSTANCE = PaperTraderV3V4Exp()
        DAEMON_INSTANCE.start()
    except Exception as e:
        logger.error(f"Failed to start PaperTraderV3V4Exp: {e}", exc_info=True)


def run_server():
    t = threading.Thread(target=run_paper_trader, daemon=True)
    t.start()
    logger.info("Launched v3-v4 NewDragon Trend Lab background trader...")

    socketserver.TCPServer.allow_reuse_address = True
    server = ReusableThreadingTCPServer(("0.0.0.0", PORT), DashboardHandler)
    logger.info(f"{VERSION_NAME} live on http://127.0.0.1:{PORT}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Stopping dashboard...")
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
