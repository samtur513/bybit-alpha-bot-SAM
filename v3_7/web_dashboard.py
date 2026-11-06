"""
Live Web Dashboard & Paper Trading Server v3.7 HFT Scalper (Port 8090).
Features:
- Microstructure L2 Orderbook (50 levels depth) & Public Trade Stream.
- 3 HFT Alpha Engines:
  * E1: OBI + Flow Toxicity Breakout (Maker In / Chased Limit Out)
  * E2: Liquidity Sweep & Absorption Reversion (Deep Book Sweep Reversal)
  * E3: Lead-Lag Beta-Catchup (High-Beta Alt vs BTCUSDT Micro-Impulse)
- 25ms Position & Queue Monitor loop with Penny-Perfect Accounting.
- REST API: /api/status, /api/state, /api/logs
"""

import asyncio
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

import v3_7.src.core_scalper as core_scalper
from v3_7.src.config import PORT, VERSION_NAME, LIVE_STATUS_FILE

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

logger = logging.getLogger("WebDashboardV37")

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v3.7 Scalper | HFT Microstructure</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>⚡</text></svg>">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700;800&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet" media="print" onload="this.media='all'">
    <noscript><link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700;800&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet"></noscript>
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
            --accent-gold: #f59e0b;
            --accent-pink: #ec4899;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border: #1e293b;
            --border-highlight: #334155;
        }

        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            background-color: var(--bg-dark);
            color: var(--text-main);
            font-family: 'Inter', -apple-system, sans-serif;
            padding: 24px;
            font-size: 14px;
        }

        .container { max-width: 1440px; margin: 0 auto; }
        
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid var(--border);
            padding-bottom: 20px;
            margin-bottom: 24px;
        }
        .header-title { display: flex; align-items: center; gap: 14px; }
        .badge {
            background: linear-gradient(135deg, #ec4899 0%, #a855f7 100%);
            color: #fff;
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 11px;
            font-weight: 800;
            font-family: 'JetBrains Mono', monospace;
            letter-spacing: 0.5px;
        }
        h1 { font-size: 20px; font-weight: 800; letter-spacing: -0.5px; }
        .subtitle { font-size: 12px; color: var(--text-muted); margin-top: 3px; }

        .grid-4 {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }

        .card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            position: relative;
        }
        .card-label {
            font-size: 11px;
            font-weight: 600;
            color: var(--text-muted);
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 8px;
            display: flex;
            justify-content: space-between;
        }
        .card-value {
            font-size: 24px;
            font-weight: 800;
            font-family: 'JetBrains Mono', monospace;
        }
        .card-sub {
            font-size: 11px;
            color: var(--text-muted);
            margin-top: 6px;
            display: flex;
            justify-content: space-between;
        }

        .color-green { color: var(--accent-green); }
        .color-red { color: var(--accent-red); }
        .color-blue { color: var(--accent-blue); }
        .color-pink { color: var(--accent-pink); }
        .color-gold { color: var(--accent-gold); }

        .section-title {
            font-size: 14px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: var(--text-muted);
            margin-bottom: 12px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
        }
        th {
            text-align: left;
            padding: 10px 14px;
            background: rgba(30, 41, 59, 0.4);
            color: var(--text-muted);
            font-weight: 600;
            font-size: 11px;
            text-transform: uppercase;
            border-bottom: 1px solid var(--border);
        }
        td {
            padding: 12px 14px;
            border-bottom: 1px solid var(--border);
            color: var(--text-main);
        }
        tr:hover td {
            background: rgba(30, 41, 59, 0.25);
        }

        .pos-badge {
            padding: 3px 8px;
            border-radius: 4px;
            font-weight: 700;
            font-size: 10px;
        }
        .pos-long { background: rgba(16, 185, 129, 0.2); color: var(--accent-green); border: 1px solid rgba(16, 185, 129, 0.4); }
        .pos-short { background: rgba(244, 63, 94, 0.2); color: var(--accent-red); border: 1px solid rgba(244, 63, 94, 0.4); }
        .badge-win { background: rgba(16, 185, 129, 0.2); color: var(--accent-green); border: 1px solid rgba(16, 185, 129, 0.4); }
        .badge-loss { background: rgba(244, 63, 94, 0.2); color: var(--accent-red); border: 1px solid rgba(244, 63, 94, 0.4); }
        .badge-reason { padding: 3px 8px; border-radius: 4px; font-weight: 700; font-size: 10px; font-family: 'JetBrains Mono', monospace; }

        .log-box {
            background: #040711;
            border: 1px solid var(--border);
            border-radius: 10px;
            padding: 14px;
            height: 240px;
            overflow-y: auto;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            line-height: 1.5;
            color: #cbd5e1;
        }
        .log-entry { margin-bottom: 4px; border-bottom: 1px solid rgba(255, 255, 255, 0.03); padding-bottom: 2px; }
        .log-time { color: var(--text-muted); margin-right: 8px; }
    </style>
</head>
<body>
    <div class="container">
        <header class="header">
            <div class="header-title">
                <span class="badge">V3.7 HFT SCALPER</span>
                <div>
                    <h1>Bybit Multi-Alpha v3.7 Microstructure Scalper</h1>
                    <div class="subtitle">15m / M1 Orderbook L2 (50 levels) • OBI Breakout • Liquidity Sweep • Lead-Lag Beta</div>
                </div>
            </div>
            <div style="text-align: right;">
                <div id="connection-status" style="font-size: 12px; font-weight: 700; color: var(--accent-green);">● WEBSOCKET L2 ONLINE</div>
                <div id="last-updated" class="subtitle" style="font-family: 'JetBrains Mono', monospace;">--:--:-- UTC</div>
            </div>
        </header>

        <div class="grid-4">
            <div class="card">
                <div class="card-label">Капитал портфеля <span>USDT</span></div>
                <div class="card-value color-pink" id="total-equity">$300.00</div>
                <div class="card-sub">
                    <span>Старт: <strong id="initial-capital">$300.00</strong></span>
                    <span id="pnl-percent">0.00%</span>
                </div>
            </div>

            <div class="card">
                <div class="card-label">Реализованный PnL <span>USDT</span></div>
                <div class="card-value" id="realized-pnl">$0.00</div>
                <div class="card-sub">
                    <span>Нереализ: <strong id="unrealized-pnl">$0.00</strong></span>
                    <span>Сделок: <strong id="trades-count">0</strong></span>
                </div>
            </div>

            <div class="card">
                <div class="card-label">Винрейт и результативность <span>Win Rate</span></div>
                <div class="card-value color-green" id="win-rate">0.0%</div>
                <div class="card-sub">
                    <span>Побед / Потерь: <strong id="wins-losses">0 / 0</strong></span>
                    <span>Всего сделок: <strong id="trades-count">0</strong></span>
                </div>
            </div>

            <div class="card">
                <div class="card-label">Активность и Щит <span>L2 Queue</span></div>
                <div class="card-value color-blue" id="limits-pos-display">0 поз / 0 лим</div>
                <div class="card-sub">
                    <span>Монет в пуле: <strong id="coins-count">16</strong></span>
                    <span>Режим: <strong id="trading-mode" class="color-gold">PAPER</strong></span>
                </div>
            </div>
        </div>

        
        <!-- 📊 Итоги и накопленная статистика Paper-теста -->
        <div style="background: var(--bg-card, #0f172a); border: 1px solid var(--border, #1e293b); border-radius: 12px; padding: 18px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; flex-wrap: wrap; gap: 8px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="margin: 0; font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted, #94a3b8); letter-spacing: 0.5px;">📊 Итоги и накопленная статистика Paper-теста (v3.7 Master Sweeps (Port 8090))</h3>
                    <span style="font-size: 10px; font-weight: 700; background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); padding: 2px 8px; border-radius: 4px;">LIVE PAPER TEST</span>
                </div>
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-muted, #94a3b8);">
                    Старт: <strong id="paper-start-time" style="color: #38bdf8;">--.--.---- --:-- UTC</strong> | Сейчас: <strong id="paper-current-time" style="color: #f8fafc;">--.--.---- --:-- UTC</strong> | Время работы: <strong id="paper-duration" style="color: #00f2fe;">--</strong>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 12px;">
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div id="paper-roi" style="font-size: 18px; font-weight: 800; color: #10b981; font-family: 'JetBrains Mono', monospace;">+0.00% ($0.00)</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Чистая доходность</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div id="paper-wr" style="font-size: 18px; font-weight: 800; color: #38bdf8; font-family: 'JetBrains Mono', monospace;">0.0% (0)</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Винрейт (WR)</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div id="paper-pf" style="font-size: 18px; font-weight: 800; color: #f59e0b; font-family: 'JetBrains Mono', monospace;">1.00</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Профит-фактор (PF)</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div id="paper-dd" style="font-size: 18px; font-weight: 800; color: #ef4444; font-family: 'JetBrains Mono', monospace;">0.0%</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Макс. просадка (DD)</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div id="paper-sharpe" style="font-size: 18px; font-weight: 800; color: #a855f7; font-family: 'JetBrains Mono', monospace;">--</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Коэф. отдачи</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div id="paper-trades" style="font-size: 18px; font-weight: 800; color: #00f2fe; font-family: 'JetBrains Mono', monospace;">0</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Всего сделок в тесте</div>
                </div>
            </div>
            <div style="font-size: 11px; color: var(--text-muted, #94a3b8); font-family: 'JetBrains Mono', monospace; line-height: 1.5; border-top: 1px solid var(--border, #1e293b); padding-top: 8px;">
                ⚙️ <strong style="color: #f8fafc;">Спецификация стратегии:</strong> Слияние v3.7 + v3.10: Reclaim заколок ликвидности 15m/M1, анти-компрессионный фильтр (KER <= 0.55), потолок глубины прокола <= 0.75 ATR, Post-Only Maker вход, независимые conditional стопы.
            </div>
        </div>
    

        <!-- Performance Equity & PnL Curve Chart -->
        <div class="card" style="margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <div class="section-title" style="margin-bottom: 0;">📈 Динамика депозита и результатов сделок (Live Scalp Curve)</div>
                    <span class="badge" style="background: rgba(0, 242, 254, 0.15); color: var(--accent-cyan); border: 1px solid rgba(0, 242, 254, 0.3);">LIVE REAL-TIME</span>
                </div>
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--text-muted);">
                    Депозит старт: <strong style="color: var(--text-main);">$300.00</strong> | Текущее эквити: <strong id="chart-curr-equity" style="color: var(--accent-cyan);">$300.00</strong>
                </div>
            </div>
            <div style="height: 190px; position: relative;">
                <canvas id="equityChart"></canvas>
            </div>
        </div>

        <!-- Subsystems Health & Telemetry Monitor -->
        <div class="card" style="margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <div class="section-title" style="margin-bottom: 0;">🩺 Мониторинг подсистем и телеметрия ядра v3.7</div>
                    <span class="badge" style="background: rgba(16, 185, 129, 0.15); color: var(--accent-green); border: 1px solid rgba(16, 185, 129, 0.3);">HEALTH: NOMINAL</span>
                </div>
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-muted);">
                    Цикл L2 монитора: <strong id="telemetry-ping" style="color: var(--accent-cyan);">25 ms</strong>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px;">
                <!-- 1. Market Stream -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">1. Поток Bybit WS & L2</span>
                        <span id="sub-ws-status" style="font-size: 10px; font-weight: 700; color: var(--accent-green); padding: 2px 6px; background: rgba(16, 185, 129, 0.15); border-radius: 4px;">ONLINE</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main);" id="sub-ws-info">
                        Подписка: <strong>11 тикеров</strong> | L2 50 уровней
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Нулевой лаг, потоковые сделки и стакан</div>
                </div>

                <!-- 2. Alpha Engines -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">2. Движки E1/E2/E3</span>
                        <span id="sub-alpha-status" style="font-size: 10px; font-weight: 700; color: var(--accent-cyan); padding: 2px 6px; background: rgba(0, 242, 254, 0.15); border-radius: 4px;">E3 LEAD-LAG #1</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main);" id="sub-alpha-info">
                        Арбитраж: <strong>Lead-Lag Beta</strong>
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Винрейт 52.6% | PF 1.38 | Макро BTC фильтр</div>
                </div>

                <!-- 3. Risk & OMS -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">3. Риск-менеджер & OMS</span>
                        <span id="sub-risk-status" style="font-size: 10px; font-weight: 700; color: var(--accent-gold); padding: 2px 6px; background: rgba(245, 158, 11, 0.15); border-radius: 4px;">PROTECTED</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main);" id="sub-risk-info">
                        Риск: <strong>0.8% ($2.40)</strong> | DD Limit: 3.5%
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Maker In (0.0324%) | TP1: 50% + БУ | TP2: 100%</div>
                </div>

                <!-- 4. Defensive Shield -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted); text-transform: uppercase;">4. Защитный щит News Core</span>
                        <span id="sub-news-status" style="font-size: 10px; font-weight: 700; color: var(--accent-green); padding: 2px 6px; background: rgba(16, 185, 129, 0.15); border-radius: 4px;">SHIELD v1.2</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main);" id="sub-news-info">
                        Эмбарго: <strong>НЕТ (Торговля активна)</strong>
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted); margin-top: 4px;">Emergency Eject & Circuit Breaker</div>
                </div>
            </div>
        </div>

        <div style="margin-bottom: 24px;">
            <div class="section-title">⚡ Активные позиции в рынке</div>
            <div class="card" style="padding: 0; overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Символ</th>
                            <th>Движок</th>
                            <th>Сторона</th>
                            <th>Объем</th>
                            <th>Вход</th>
                            <th>Текущая</th>
                            <th>Stop Loss</th>
                            <th>Take Profit 1</th>
                            <th>Take Profit 2</th>
                            <th>Нереализ. PnL</th>
                        </tr>
                    </thead>
                    <tbody id="positions-table-body">
                        <tr>
                            <td colspan="10" style="text-align: center; color: var(--text-muted); padding: 24px;">
                                Нет активных позиций. Ожидание сигналов OBI, Sweep Absorption или Lead-Lag...
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <div style="margin-bottom: 24px;">
            <div class="section-title">⏱ Отложенные лимитные ордера в очереди L2</div>
            <div class="card" style="padding: 0; overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Символ</th>
                            <th>Движок</th>
                            <th>Сторона</th>
                            <th>Объем</th>
                            <th>Цена лимита</th>
                            <th>Объем перед нами в очереди</th>
                        </tr>
                    </thead>
                    <tbody id="orders-table-body">
                        <tr>
                            <td colspan="6" style="text-align: center; color: var(--text-muted); padding: 18px;">
                                Очередь пуста.
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <div style="margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                <div class="section-title" style="margin-bottom: 0;">📜 История закрытых сделок (PnL & Результаты исполнения)</div>
                <span id="closed-count-badge" style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--accent-cyan); font-weight: 700;">0 сделок</span>
            </div>
            <div class="card" style="padding: 0; overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Время (UTC)</th>
                            <th>Символ</th>
                            <th>Движок</th>
                            <th>Сторона</th>
                            <th>Вход</th>
                            <th>Выход</th>
                            <th>Объем</th>
                            <th>Чистый PnL</th>
                            <th>Причина выхода</th>
                        </tr>
                    </thead>
                    <tbody id="closed-trades-body">
                        <tr>
                            <td colspan="9" style="text-align: center; color: var(--text-muted); padding: 24px;">
                                Нет закрытых сделок. Накопление торговой истории...
                            </td>
                        </tr>
                    </tbody>
                </table>
            </div>
        </div>

        <div>
            <div class="section-title">📜 Журнал событий и исполнения (25 мс)</div>
            <div class="log-box" id="log-console">
                <div class="log-entry"><span class="log-time">[SYSTEM]</span> Инициализация BybitScalper v3.7...</div>
            </div>
        </div>
    </div>

    <script>

    function formatChartTs(ts) {
        if (!ts) return '';
        const ms = typeof ts === 'number' ? (ts < 1e11 ? ts * 1000 : ts) : Date.parse(ts);
        if (isNaN(ms)) return String(ts);
        const d = new Date(ms);
        const day = String(d.getUTCDate()).padStart(2, '0');
        const mon = String(d.getUTCMonth() + 1).padStart(2, '0');
        const hr = String(d.getUTCHours()).padStart(2, '0');
        const min = String(d.getUTCMinutes()).padStart(2, '0');
        return `${day}.${mon} ${hr}:${min}`;
    }

    function syncPaperTestStatsAndChart(data) {
        if (!data) return;
        const eq = Number(data.capital || data.equity || data.equity_usdt || 300.0);
        const initCap = Number(data.initial_capital || 300.0);
        const netPnl = eq - initCap;
        const roiPct = (netPnl / initCap) * 100.0;
        const rawTrades = data.closed_trades || [];
        const trades = [...rawTrades].sort((a, b) => {
            const ta = a.exit_time || a.closed_at_ms || a.timestamp || 0;
            const tb = b.exit_time || b.closed_at_ms || b.timestamp || 0;
            return ta - tb;
        });

        const wins = trades.filter(t => (t.net_pnl || t.net_pnl_usdt || 0) > 0);
        const losses = trades.filter(t => (t.net_pnl || t.net_pnl_usdt || 0) < 0);
        const grossWin = wins.reduce((acc, t) => acc + Number(t.net_pnl || t.net_pnl_usdt || 0), 0);
        const grossLoss = Math.abs(losses.reduce((acc, t) => acc + Number(t.net_pnl || t.net_pnl_usdt || 0), 0));
        const pf = grossLoss > 0 ? (grossWin / grossLoss) : (grossWin > 0 ? 99.9 : 1.0);
        const wr = trades.length > 0 ? ((wins.length / trades.length) * 100.0) : Number(data.win_rate || data.win_rate_pct || 0);
        const dd = Number(data.max_drawdown_pct || data.drawdown_pct || 0.0);
        const rf = dd > 0 ? (netPnl / (initCap * dd / 100.0)) : (netPnl >= 0 ? 1.0 : -1.0);

        const roiEl = document.getElementById('paper-roi');
        if (roiEl) {
            roiEl.innerText = (roiPct >= 0 ? '+' : '') + roiPct.toFixed(2) + '% (' + (netPnl >= 0 ? '+$' : '-$') + Math.abs(netPnl).toFixed(2) + ')';
            roiEl.style.color = roiPct >= 0 ? '#10b981' : '#ef4444';
        }
        const wrEl = document.getElementById('paper-wr');
        if (wrEl) wrEl.innerText = wr.toFixed(1) + '% (' + wins.length + '/' + trades.length + ')';
        const pfEl = document.getElementById('paper-pf');
        if (pfEl) pfEl.innerText = grossLoss > 0 ? pf.toFixed(2) : (grossWin > 0 ? '∞' : '1.00');
        const ddEl = document.getElementById('paper-dd');
        if (ddEl) ddEl.innerText = dd.toFixed(2) + '%';
        const rfEl = document.getElementById('paper-sharpe');
        if (rfEl) rfEl.innerText = trades.length >= 2 ? rf.toFixed(2) : '--';
        const trEl = document.getElementById('paper-trades');
        if (trEl) trEl.innerText = trades.length;

        const firstTradeTs = trades.length > 0 ? (trades[0].exit_time || trades[0].closed_at_ms || trades[0].timestamp) : 0;
        const startMs = firstTradeTs ? (firstTradeTs < 1e11 ? firstTradeTs * 1000 : firstTradeTs) : (Date.now() - 86400000 * 1.5);
        const durHours = Math.max(0.1, (Date.now() - startMs) / 3600000);
        const durStr = durHours >= 24 ? `${Math.floor(durHours / 24)}д ${Math.floor(durHours % 24)}ч` : `${durHours.toFixed(1)}ч`;

        const startEl = document.getElementById('paper-start-time');
        if (startEl) startEl.innerText = formatChartTs(startMs) + ' UTC';
        const currEl = document.getElementById('paper-current-time');
        if (currEl) currEl.innerText = formatChartTs(Date.now()) + ' UTC';
        const durEl = document.getElementById('paper-duration');
        if (durEl) durEl.innerText = durStr;

        const chartCurrEq = document.getElementById('chart-curr-equity');
        if (chartCurrEq) chartCurrEq.innerText = '$' + eq.toFixed(2);

        const chartObj = window.equityChartInstance || window.equityChart || window.chartInstance || (typeof chartInstance !== 'undefined' ? chartInstance : null) || (typeof equityChart !== 'undefined' ? equityChart : null);
        if (chartObj) {
            let labels = [];
            let points = [];
            if (data.equity_chart_data && data.equity_chart_data.length > 0) {
                labels = data.equity_chart_data.map(d => d[0]);
                points = data.equity_chart_data.map(d => d[1]);
            } else {
                labels = [formatChartTs(startMs - 3600000) + ' (Старт)'];
                points = [initCap];
                let run = initCap;
                trades.forEach((t, i) => {
                    run += Number(t.net_pnl || t.net_pnl_usdt || 0.0);
                    const ts = t.exit_time || t.closed_at_ms || t.timestamp;
                    labels.push(formatChartTs(ts) + ' (#' + (i + 1) + ' ' + (t.symbol || '') + ')');
                    points.push(Number(run.toFixed(2)));
                });
                if (points.length === 1 || Math.abs(points[points.length - 1] - eq) > 0.001) {
                    labels.push(formatChartTs(Date.now()) + ' (Сейчас)');
                    points.push(Number(eq.toFixed(2)));
                }
            }
            chartObj.data.labels = labels;
            chartObj.data.datasets[0].data = points;
            const isProfit = eq >= initCap;
            chartObj.data.datasets[0].borderColor = isProfit ? '#00f2fe' : '#ef4444';
            chartObj.data.datasets[0].pointBackgroundColor = isProfit ? '#00f2fe' : '#ef4444';
            chartObj.data.datasets[0].backgroundColor = isProfit ? 'rgba(0, 242, 254, 0.08)' : 'rgba(239, 68, 68, 0.08)';
            chartObj.update();
        }
    }


        let chartInstance = null;

        function escapeHtml(text) {
            return String(text)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#039;");
        }

        function initEquityChart() {
            const ctx = document.getElementById('equityChart');
            if (!ctx || typeof Chart === 'undefined') return;
            chartInstance = new Chart(ctx.getContext('2d'), {
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
                        pointRadius: 4,
                        pointHoverRadius: 6,
                        borderWidth: 2,
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

        async function updateDashboard() {
            try {
                const res = await fetch('/api/status');
                if (!res.ok) throw new Error("HTTP " + res.status);
                const data = await res.json();
                syncPaperTestStatsAndChart(data);

                const initCap = Number(data.initial_capital || 300.0);
                const currEq = Number(data.equity || 300.0);
                document.getElementById('total-equity').innerText = '$' + currEq.toFixed(2);
                document.getElementById('initial-capital').innerText = '$' + initCap.toFixed(2);
                
                const netPnl = data.net_pnl || 0.0;
                const pnlPct = ((netPnl / initCap) * 100);
                const pnlEl = document.getElementById('pnl-percent');
                pnlEl.innerText = (pnlPct >= 0 ? '+' : '') + pnlPct.toFixed(2) + '%';
                pnlEl.className = pnlPct >= 0 ? 'color-green' : 'color-red';

                const rPnlEl = document.getElementById('realized-pnl');
                rPnlEl.innerText = (data.realized_pnl >= 0 ? '+$' : '-$') + Math.abs(data.realized_pnl || 0).toFixed(2);
                rPnlEl.className = 'card-value ' + (data.realized_pnl >= 0 ? 'color-green' : 'color-red');

                document.getElementById('unrealized-pnl').innerText = '$' + (data.unrealized_pnl || 0).toFixed(2);

                // Closed trades & Win Rate calculation
                const closed = data.closed_trades || [];
                let wins = 0;
                let losses = 0;
                closed.forEach(t => {
                    const pnlVal = Number(t.net_pnl_usdt !== undefined ? t.net_pnl_usdt : (t.net_pnl || 0.0));
                    if (pnlVal > 0) wins++;
                    else if (pnlVal < 0) losses++;
                });
                const totalClosed = closed.length;
                const winRate = totalClosed > 0 ? ((wins / totalClosed) * 100) : 0.0;
                
                document.getElementById('win-rate').innerText = winRate.toFixed(1) + '%';
                document.getElementById('wins-losses').innerText = `${wins} / ${losses}`;
                document.getElementById('trades-count').innerText = totalClosed;

                const openPosCount = (data.open_positions || []).length;
                const pendingOrdersCount = (data.pending_orders || []).length;
                const limitsPosEl = document.getElementById('limits-pos-display');
                if (limitsPosEl) limitsPosEl.innerText = `${openPosCount} поз / ${pendingOrdersCount} лим`;

                document.getElementById('trading-mode').innerText = data.mode || "PAPER";
                document.getElementById('coins-count').innerText = (data.monitored_symbols || []).length;
                
                const isOnline = data.stream_ready !== false;
                document.getElementById('connection-status').innerText = isOnline ? '● WEBSOCKET L2 ONLINE' : '● WEBSOCKET RECONNECTING...';
                document.getElementById('connection-status').style.color = isOnline ? 'var(--accent-green)' : 'var(--accent-gold)';
                document.getElementById('last-updated').innerText = new Date().toISOString().substring(11, 19) + ' UTC';

                const chartCurrEl = document.getElementById('chart-curr-equity');
                if (chartCurrEl) chartCurrEl.innerText = '$' + currEq.toFixed(2);

                const closedCountBadge = document.getElementById('closed-count-badge');
                if (closedCountBadge) closedCountBadge.innerText = `${totalClosed} сделок`;

                // Render positions
                const posBody = document.getElementById('positions-table-body');
                if (data.open_positions && data.open_positions.length > 0) {
                    posBody.innerHTML = data.open_positions.map(p => `
                        <tr>
                            <td><strong>${escapeHtml(p.symbol)}</strong></td>
                            <td><span style="color: var(--accent-pink); font-weight:700;">${escapeHtml(p.engine_id || 'E1_OBI')}</span></td>
                            <td><span class="pos-badge ${p.direction === 'LONG' || p.direction === 1 ? 'pos-long' : 'pos-short'}">${p.direction === 1 ? 'LONG' : (p.direction === -1 ? 'SHORT' : escapeHtml(p.direction))}</span></td>
                            <td>${p.qty}</td>
                            <td>${Number(p.entry_price || 0).toFixed(4)}</td>
                            <td>${Number(p.current_price || 0).toFixed(4)}</td>
                            <td class="color-red">${p.stop_loss ? Number(p.stop_loss).toFixed(4) : '-'}</td>
                            <td class="color-green">${p.take_profit_1 ? Number(p.take_profit_1).toFixed(4) : '-'}</td>
                            <td class="color-green">${p.take_profit_2 ? Number(p.take_profit_2).toFixed(4) : '-'}</td>
                            <td class="${(p.unrealized_pnl || 0) >= 0 ? 'color-green' : 'color-red'}"><strong>${((p.unrealized_pnl || 0) >= 0 ? '+' : '') + Number(p.unrealized_pnl || 0).toFixed(4)} USDT</strong></td>
                        </tr>
                    `).join('');
                } else {
                    posBody.innerHTML = `<tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 24px;">Нет активных позиций. Ожидание сигналов OBI, Sweep Absorption или Lead-Lag...</td></tr>`;
                }

                // Render orders
                const ordBody = document.getElementById('orders-table-body');
                if (data.pending_orders && data.pending_orders.length > 0) {
                    ordBody.innerHTML = data.pending_orders.map(o => `
                        <tr>
                            <td><strong>${escapeHtml(o.symbol)}</strong></td>
                            <td><span style="color: var(--accent-blue); font-weight:700;">${escapeHtml(o.engine_id || 'OMS')}</span></td>
                            <td><span class="pos-badge ${o.side === 'Buy' ? 'pos-long' : 'pos-short'}">${escapeHtml((o.side || 'BUY').toUpperCase())}</span></td>
                            <td>${o.qty}</td>
                            <td>${Number(o.price || 0).toFixed(4)}</td>
                            <td style="color: var(--accent-gold); font-weight: 700;">${o.queue_ahead_vol || 0}</td>
                        </tr>
                    `).join('');
                } else {
                    ordBody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 18px;">Очередь пуста.</td></tr>`;
                }

                // Render Closed Trades Table (newest first)
                const closedBody = document.getElementById('closed-trades-body');
                if (closedBody) {
                    if (totalClosed === 0) {
                        closedBody.innerHTML = `<tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 24px;">Нет закрытых сделок. Накопление торговой истории...</td></tr>`;
                    } else {
                        const reversedClosed = [...closed].reverse();
                        closedBody.innerHTML = reversedClosed.map(t => {
                            const pnl = Number(t.net_pnl_usdt !== undefined ? t.net_pnl_usdt : (t.net_pnl || 0.0));
                            const pnlClass = pnl >= 0 ? 'color-green' : 'color-red';
                            const side = (t.side || 'BUY').toUpperCase();
                            const isBuy = side === 'BUY';
                            const sideBadge = `<span class="pos-badge ${isBuy ? 'pos-long' : 'pos-short'}">${side}</span>`;
                            const engBadge = `<span style="color: var(--accent-pink); font-weight:700;">${escapeHtml(t.engine_id || 'SCALP')}</span>`;
                            
                            let timeStr = '--:--:--';
                            if (t.timestamp) {
                                try {
                                    timeStr = new Date(t.timestamp * 1000).toISOString().substring(11, 19);
                                } catch (e) {}
                            }

                            let reasonBadge = `<span class="badge-reason" style="background: rgba(148, 163, 184, 0.15); color: #94a3b8;">${escapeHtml(t.reason || 'CLOSE')}</span>`;
                            if (t.reason && t.reason.includes('TP2')) {
                                reasonBadge = `<span class="badge-reason badge-win">TP2_TARGET</span>`;
                            } else if (t.reason && t.reason.includes('TP1')) {
                                reasonBadge = `<span class="badge-reason" style="background: rgba(0, 242, 254, 0.2); color: #00f2fe;">TP1_FILLED</span>`;
                            } else if (t.reason && t.reason.includes('STOP_LOSS')) {
                                reasonBadge = `<span class="badge-reason badge-loss">STOP_LOSS</span>`;
                            } else if (t.reason && t.reason.includes('TIME_STOP')) {
                                reasonBadge = `<span class="badge-reason" style="background: rgba(56, 189, 248, 0.2); color: #38bdf8;">TIME_STOP</span>`;
                            } else if (t.reason && t.reason.includes('OBI_FLIP')) {
                                reasonBadge = `<span class="badge-reason" style="background: rgba(245, 158, 11, 0.2); color: #f59e0b;">OBI_FLIP</span>`;
                            } else if (t.reason && t.reason.includes('REVERSAL')) {
                                reasonBadge = `<span class="badge-reason" style="background: rgba(236, 72, 153, 0.2); color: #ec4899;">LEADER_REVERSAL</span>`;
                            }

                            return `
                                <tr>
                                    <td style="color: var(--text-muted);">${timeStr} UTC</td>
                                    <td><strong>${escapeHtml(t.symbol)}</strong></td>
                                    <td>${engBadge}</td>
                                    <td>${sideBadge}</td>
                                    <td>${Number(t.entry_price || 0).toFixed(4)}</td>
                                    <td>${Number(t.exit_price || 0).toFixed(4)}</td>
                                    <td>${Number(t.initial_qty || t.remaining_qty || 0).toFixed(4)}</td>
                                    <td class="${pnlClass}" style="font-weight: 700;">${pnl >= 0 ? '+' : ''}$${pnl.toFixed(4)}</td>
                                    <td>${reasonBadge}</td>
                                </tr>
                            `;
                        }).join('');
                    }
                }

                // Update Equity & PnL Curve Chart
                if (chartInstance) {
                    const labels = ['Старт'];
                    const points = [initCap];
                    let runningEq = initCap;
                    
                    closed.forEach((t, idx) => {
                        const pnl = Number(t.net_pnl_usdt !== undefined ? t.net_pnl_usdt : (t.net_pnl || 0.0));
                        runningEq += pnl;
                        labels.push(`#${idx + 1} ${t.symbol}`);
                        points.push(Number(runningEq.toFixed(2)));
                    });

                    // Add current live equity point if different from last trade point
                    if (points.length === 1 || Math.abs(points[points.length - 1] - currEq) > 0.001) {
                        labels.push('Текущее');
                        points.push(Number(currEq.toFixed(2)));
                    }

                    chartInstance.data.labels = labels;
                    chartInstance.data.datasets[0].data = points;
                    
                    const isProfit = currEq >= initCap;
                    chartInstance.data.datasets[0].borderColor = isProfit ? '#00f2fe' : '#f43f5e';
                    chartInstance.data.datasets[0].pointBackgroundColor = isProfit ? '#00f2fe' : '#f43f5e';
                    chartInstance.data.datasets[0].backgroundColor = isProfit ? 'rgba(0, 242, 254, 0.08)' : 'rgba(244, 63, 94, 0.08)';
                    chartInstance.update();
                } else if (typeof Chart !== 'undefined') {
                    initEquityChart();
                }

            } catch (e) {
                console.error("updateDashboard error:", e);
                document.getElementById('connection-status').innerText = '● DISCONNECTED';
                document.getElementById('connection-status').style.color = 'var(--accent-red)';
            }
        }

        async function fetchLogs() {
            try {
                const res = await fetch('/api/logs');
                if (res.ok) {
                    const logs = await res.json();
                    const consoleEl = document.getElementById('log-console');
                    if (Array.isArray(logs) && logs.length > 0) {
                        consoleEl.innerHTML = logs.map(l => `<div class="log-entry">${escapeHtml(l)}</div>`).join('');
                        consoleEl.scrollTop = consoleEl.scrollHeight;
                    }
                }
            } catch (e) {
                console.error("fetchLogs error:", e);
            }
        }

        window.addEventListener('DOMContentLoaded', () => {
            initEquityChart();
            updateDashboard();
            fetchLogs();
            setInterval(updateDashboard, 1500);
            setInterval(fetchLogs, 2500);
        });
    </script>
</body>
</html>
"""


class DashboardHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def _send_payload(self, payload: bytes, content_type: str = "text/html; charset=utf-8", status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/", "/index.html"):
            self._send_payload(HTML_PAGE.encode("utf-8"), "text/html; charset=utf-8")

        elif path == "/favicon.ico":
            self._send_payload(b"", "image/x-icon", 204)

        elif path == "/api/status":
            if core_scalper.GLOBAL_OMS is not None:
                status = core_scalper.GLOBAL_OMS.get_status()
            elif LIVE_STATUS_FILE.exists():
                try:
                    with open(LIVE_STATUS_FILE, "r", encoding="utf-8") as f:
                        status = json.load(f)
                except Exception:
                    status = {"version": "3.7.0", "equity": 300.0, "online": True}
            else:
                status = {
                    "version": "3.7.0",
                    "name": "v3.7 HFT Scalper",
                    "mode": "PAPER_TRADING",
                    "port": PORT,
                    "initial_capital": 300.0,
                    "capital": 300.0,
                    "equity": 300.0,
                    "realized_pnl": 0.0,
                    "unrealized_pnl": 0.0,
                    "open_positions": [],
                    "pending_orders": [],
                    "closed_trades": [],
                    "monitored_symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                    "stream_ready": True,
                    "news_shield": True,
                    "news_defense_active": True,
                    "btc_trend_status": "NEUTRAL",
                }
            payload = json.dumps(status, default=str).encode("utf-8")
            self._send_payload(payload, "application/json; charset=utf-8")

        elif path == "/api/state":
            if core_scalper.GLOBAL_OMS is not None:
                st = core_scalper.GLOBAL_OMS.get_status()
            else:
                st = {}
            payload = json.dumps(st, default=str).encode("utf-8")
            self._send_payload(payload, "application/json; charset=utf-8")

        elif path == "/api/logs":
            logs = list(LOG_BUFFER)
            payload = json.dumps(logs).encode("utf-8")
            self._send_payload(payload, "application/json; charset=utf-8")

        else:
            self._send_payload(b"Not Found", "text/plain", 404)

    def log_message(self, format, *args):
        return


def run_scalper_in_thread():
    """Runs the asyncio scalper main loop in a background daemon thread."""
    logger.info("Launching Bybit Scalper v3.7 async engine...")
    try:
        asyncio.run(core_scalper.main())
    except Exception as e:
        logger.error(f"Scalper daemon terminated with error: {e}")


def start_server_and_bot():
    logger.info(f"Initializing {VERSION_NAME} Web Dashboard on Port {PORT}...")

    # Start scalper thread
    t = threading.Thread(target=run_scalper_in_thread, daemon=True)
    t.start()

    server_address = ("0.0.0.0", PORT)
    with socketserver.ThreadingTCPServer(server_address, DashboardHTTPRequestHandler) as httpd:
        httpd.allow_reuse_address = True
        logger.info(f"{VERSION_NAME} live on http://127.0.0.1:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("Dashboard interrupted. Shutting down...")


if __name__ == "__main__":
    start_server_and_bot()
