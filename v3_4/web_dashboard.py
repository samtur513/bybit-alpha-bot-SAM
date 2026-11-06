"""
Live Web Dashboard & Paper Trading Server v3.4 ZF-8 Quant (Port 8087).
Features:
- 8-Speed Intelligent ZF Quant Transmission (Smart Kickdown & ABS Rapid Braking)
- Dynamic Universe Radar (Top 25 screened Bybit linear perpetuals)
- News Core v1.2 Defensive Shield
- Anti-Martingale Pyramiding (0% on G1 up to 100% on G8)
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

from v3_4.src.paper_trader_v34 import PaperTraderV34
from v3_4.src.config import PORT, VERSION_NAME, LIVE_STATUS_FILE

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

logger = logging.getLogger("WebDashboardV34")
GLOBAL_TRADER: PaperTraderV34 = None

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v3.4 ZF-8 Quant | 8-Speed Intelligent Transmission</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700;800&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
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
            --accent-violet: #8b5cf6;
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
            background: linear-gradient(135deg, #a855f7 0%, #ec4899 100%);
            color: #ffffff;
            font-weight: 800;
            font-size: 13px;
            padding: 6px 12px;
            border-radius: 6px;
            letter-spacing: 1px;
            font-family: 'JetBrains Mono', monospace;
            box-shadow: 0 0 15px rgba(168, 85, 247, 0.4);
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
        .val-purple { color: var(--accent-purple); }

        /* ZF-8 Transmission Dashboard */
        .transmission-panel {
            background: linear-gradient(135deg, rgba(168, 85, 247, 0.08) 0%, rgba(15, 23, 42, 0.9) 100%);
            border: 1px solid rgba(168, 85, 247, 0.3);
            border-radius: 12px;
            padding: 18px 22px;
            margin-bottom: 24px;
        }
        .trans-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 16px;
        }
        .trans-status-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            padding: 6px 14px;
            border-radius: 8px;
            font-weight: 800;
            font-size: 14px;
            font-family: 'JetBrains Mono', monospace;
            background: rgba(168, 85, 247, 0.2);
            color: #d8b4fe;
            border: 1px solid rgba(168, 85, 247, 0.5);
            box-shadow: 0 0 15px rgba(168, 85, 247, 0.3);
        }
        .gears-track {
            display: grid;
            grid-template-columns: repeat(8, 1fr);
            gap: 8px;
            margin-bottom: 14px;
        }
        @media (max-width: 900px) {
            .gears-track { grid-template-columns: repeat(4, 1fr); }
        }
        .gear-slot {
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 8px;
            padding: 10px 8px;
            text-align: center;
            transition: all 0.2s ease;
        }
        .gear-slot.active {
            background: linear-gradient(180deg, rgba(168, 85, 247, 0.25) 0%, rgba(168, 85, 247, 0.05) 100%);
            border: 2px solid #a855f7;
            box-shadow: 0 0 16px rgba(168, 85, 247, 0.4);
            transform: translateY(-2px);
        }
        .gear-slot-tag {
            font-size: 15px;
            font-weight: 800;
            font-family: 'JetBrains Mono', monospace;
            color: var(--text-muted);
        }
        .gear-slot.active .gear-slot-tag {
            color: #f3e8ff;
        }
        .gear-slot-risk {
            font-size: 11px;
            font-weight: 600;
            color: var(--text-muted);
            margin-top: 4px;
            font-family: 'JetBrains Mono', monospace;
        }
        .gear-slot.active .gear-slot-risk {
            color: #c084fc;
            font-weight: 700;
        }
        .gear-slot-rr {
            font-size: 10px;
            color: rgba(255,255,255,0.4);
            margin-top: 2px;
        }
        .gear-slot.active .gear-slot-rr {
            color: #e9d5ff;
        }

        .trans-telemetry-row {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 12px;
            color: var(--text-muted);
            padding-top: 10px;
            border-top: 1px solid rgba(255, 255, 255, 0.05);
            flex-wrap: wrap;
            gap: 12px;
        }

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
            padding: 14px 18px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
            color: #94a3b8;
            max-height: 240px;
            overflow-y: auto;
            line-height: 1.5;
            white-space: pre-wrap;
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo-group">
                <span class="logo-badge">v3.4 ZF-8</span>
                <div>
                    <h1 class="title">Bybit Multi-Alpha v3.4 ZF-8 Quant (Port 8087)</h1>
                    <p class="subtitle">8-Speed Intelligent Transmission &bull; Smart Kickdown &bull; ABS Rapid Braking</p>
                </div>
            </div>
            <div style="font-family: 'JetBrains Mono', monospace; font-size: 13px; color: var(--text-muted);" id="live-clock">
                --:--:-- UTC
            </div>
        </header>

        <!-- ZF-8 Transmission Dashboard -->
        <div class="transmission-panel">
            <div class="trans-header">
                <div>
                    <span style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px;">АКТИВНАЯ ПЕРЕДАЧА ZF-8</span>
                    <div style="margin-top: 6px;">
                        <span id="trans-badge" class="trans-status-badge">⚙️ G1: LIMP / CRAWL (1.0%)</span>
                    </div>
                </div>
                <div>
                    <span style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px;">ПОСЛЕДНЕЕ ПЕРЕКЛЮЧЕНИЕ</span>
                    <div id="shift-reason" style="font-family: 'JetBrains Mono', monospace; font-size: 13px; color: var(--accent-purple); font-weight: 700; margin-top: 6px;">
                        INITIAL_STATE
                    </div>
                </div>
                <div>
                    <span style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px;">СЕРИЯ ПОБЕД / ПОРАЖЕНИЙ</span>
                    <div id="streak-counter" style="font-family: 'JetBrains Mono', monospace; font-size: 14px; font-weight: 700; margin-top: 6px;">
                        0 побед подряд
                    </div>
                </div>
                <div>
                    <span style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px;">ПИРАМИДИНГ (ДОБАВКА)</span>
                    <div id="pyramid-status" style="font-family: 'JetBrains Mono', monospace; font-size: 14px; font-weight: 700; margin-top: 6px;">
                        0%
                    </div>
                </div>
            </div>

            <!-- 8 Gears Track -->
            <div class="gears-track" id="gears-track">
                <!-- Injected via JavaScript -->
            </div>

            <div class="trans-telemetry-row">
                <span>🛡️ <b>Limp Mode Guard:</b> Сброс в G1 при просадке &ge; 8.0% | Восстановление при &le; 4.0%</span>
                <span>⚡ <b>Kickdown:</b> +2G на раннерах (&ge;3.2R), +3G на мега-раннерах (&ge;5.0R)</span>
                <span>🛑 <b>ABS Braking:</b> -2G на полном стопе (&lt;-0.7R)</span>
            </div>
        </div>

        <!-- Core Metrics -->
        <div class="stats-grid">
            <div class="card">
                <div class="card-label">Эквити Портфеля</div>
                <div class="card-val" id="equity-val">$300.00</div>
                <div class="card-sub" id="pnl-sub">Чистый PnL: $0.00 (0.00%)</div>
            </div>
            <div class="card">
                <div class="card-label">Риск на Сделку (1R)</div>
                <div class="card-val val-purple" id="risk-val">1.0%</div>
                <div class="card-sub" id="risk-usd-sub">$3.00 макс. риск</div>
            </div>
            <div class="card">
                <div class="card-label">Макс. Просадка</div>
                <div class="card-val" id="dd-val">0.00%</div>
                <div class="card-sub">Лимит: 30.0% (Limp при 8%)</div>
            </div>
            <div class="card">
                <div class="card-label">Винрейт / Сделки</div>
                <div class="card-val" id="winrate-val">--%</div>
                <div class="card-sub" id="trades-sub">0 сделок закрыто</div>
            </div>
        </div>

        
        
        <!-- 📊 Итоги и накопленная статистика Paper-теста -->
        <div style="background: var(--bg-card, #0f172a); border: 1px solid var(--border, #1e293b); border-radius: 12px; padding: 18px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; flex-wrap: wrap; gap: 8px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="margin: 0; font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted, #94a3b8); letter-spacing: 0.5px;">📊 Итоги и накопленная статистика Paper-теста (v3.4 ZF-8 Quant (Port 8087))</h3>
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
                ⚙️ <strong style="color: #f8fafc;">Спецификация стратегии:</strong> 8-ступенчатая автоматическая коробка передач (G1-G8) с Half-Kelly сайзингом, каскадными фиксациями TP1 (+1.2R), TP2 (+2.4R), TP3 (+4.0R) и защитой Fast Scratch.
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
                    <h3 style="font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px; margin: 0;">🩺 Мониторинг подсистем и телеметрия ядра v3.4 ZF-8 Quant</h3>
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
                        1h ZF-8 Speed Transmission + Half-Kelly
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
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 16px;">
                <h3 style="font-size: 14px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px;">Открытые Позиции</h3>
                <span id="pos-count" style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--accent-cyan);">0 позиций</span>
            </div>
            <div style="overflow-x: auto;">
                <table>
                    <thead>
                        <tr>
                            <th>Символ</th>
                            <th>Сторона</th>
                            <th>Объем</th>
                            <th>Вход</th>
                            <th>Текущая</th>
                            <th>Стоп-лосс</th>
                            <th>Цель TP1</th>
                            <th>Цель TP2</th>
                            <th>Плавающий PnL</th>
                            <th>Передача</th>
                        </tr>
                    </thead>
                    <tbody id="positions-body">
                        <tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 24px;">Нет открытых позиций</td></tr>
                    </tbody>
                </table>
            </div>
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

        <!-- System Logs -->
        <div class="card">
            <h3 style="font-size: 14px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing: 0.5px; margin-bottom: 12px;">Системный Журнал Событий ZF-8</h3>
            <div class="log-box" id="log-box">Загрузка логов...</div>
        </div>
    </div>

    <script>
    var chartInstance = null;
    var equityChart = null;


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

        try {
            const chartObj = window.chartInstance || chartInstance || window.equityChart || equityChart;
            if (chartObj && chartObj.data) {
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
        } catch (chartErr) {
            console.warn("Chart sync warning:", chartErr);
        }
    }


        const GEARS_DATA = [
            { gear: 1, name: "LIMP", risk: "1.0%", rr: "1:2.8-3.6", pyr: "0%" },
            { gear: 2, name: "CITY", risk: "1.3%", rr: "1:3.0-4.0", pyr: "+20%" },
            { gear: 3, name: "CRUISE", risk: "1.6%", rr: "1:3.3-4.4", pyr: "+35%" },
            { gear: 4, name: "DRIVE", risk: "2.0%", rr: "1:3.6-4.8", pyr: "+50%" },
            { gear: 5, name: "FAST", risk: "2.4%", rr: "1:4.0-5.2", pyr: "+65%" },
            { gear: 6, name: "SPORT", risk: "2.8%", rr: "1:4.4-5.6", pyr: "+75%" },
            { gear: 7, name: "SPORT+", risk: "3.2%", rr: "1:4.8-6.0", pyr: "+85%" },
            { gear: 8, name: "LAUNCH", risk: "3.6%", rr: "1:5.2-6.5", pyr: "+100%" }
        ];

        function renderGearsTrack(activeGear) {
            const track = document.getElementById('gears-track');
            track.innerHTML = '';
            GEARS_DATA.forEach(g => {
                const isActive = (g.gear === activeGear);
                const el = document.createElement('div');
                el.className = 'gear-slot' + (isActive ? ' active' : '');
                el.innerHTML = `
                    <div class="gear-slot-tag">G${g.gear}</div>
                    <div class="gear-slot-risk">${g.risk}</div>
                    <div class="gear-slot-rr">${g.rr}</div>
                `;
                track.appendChild(el);
            });
        }

        
        var chartInstance = chartInstance || null;
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

        document.addEventListener('DOMContentLoaded', initEquityChart);
        async function updateDashboard() {
            try {
                const res = await fetch('/api/status');
                if (!res.ok) return;
                const data = await res.json();
                updateCommonChartAndClosed(data);
                try { syncPaperTestStatsAndChart(data); } catch (e) { console.error("syncPaperTestStatsAndChart error:", e); }

                // Telemetry
                const equity = data.capital || data.portfolio_equity || 300.0;
                const initCapital = data.initial_capital || 300.0;
                const pnl = equity - initCapital;
                const pnlPct = (pnl / initCapital) * 100;
                const dd = data.drawdown_pct || 0.0;

                document.getElementById('equity-val').textContent = '$' + equity.toFixed(2);
                const pnlSign = pnl >= 0 ? '+' : '';
                const pnlSub = document.getElementById('pnl-sub');
                pnlSub.textContent = `Чистый PnL: ${pnlSign}$${pnl.toFixed(2)} (${pnlSign}${pnlPct.toFixed(2)}%)`;
                pnlSub.className = 'card-sub ' + (pnl >= 0 ? 'val-green' : 'val-red');

                document.getElementById('dd-val').textContent = dd.toFixed(2) + '%';
                document.getElementById('winrate-val').textContent = (data.win_rate_pct != null ? data.win_rate_pct.toFixed(1) : '--') + '%';
                document.getElementById('trades-sub').textContent = `${(data.closed_trades || []).length} сделок закрыто`;

                // Transmission Status
                const gear = data.gearbox_gear || 1;
                const riskPct = data.gearbox_risk_pct || 1.0;
                const riskUsd = (equity * (riskPct / 100)).toFixed(2);
                document.getElementById('risk-val').textContent = riskPct.toFixed(1) + '%';
                document.getElementById('risk-usd-sub').textContent = `$${riskUsd} макс. риск`;

                const transBadge = document.getElementById('trans-badge');
                transBadge.textContent = `⚙️ G${gear}: ${data.gear_name || 'LIMP / CRAWL'} (${riskPct.toFixed(1)}%)`;
                
                document.getElementById('shift-reason').textContent = data.last_shift_reason || 'NORMAL_TRANSMISSION';
                
                const streakWins = data.streak_wins || 0;
                const streakLosses = data.streak_losses || 0;
                const streakEl = document.getElementById('streak-counter');
                if (streakWins > 0) {
                    streakEl.innerHTML = `<span class="val-green">🔥 ${streakWins} побед подряд</span>`;
                } else if (streakLosses > 0) {
                    streakEl.innerHTML = `<span class="val-red">❄️ ${streakLosses} поражений подряд</span>`;
                } else {
                    streakEl.textContent = '0 подряд';
                }

                const pyrPct = Math.round((data.pyramiding_ratio || 0.0) * 100);
                document.getElementById('pyramid-status').innerHTML = pyrPct > 0 ? `<span class="val-purple">+${pyrPct}%</span>` : '<span style="color: var(--text-muted);">Выключен (0%)</span>';

                renderGearsTrack(gear);

                // Positions
                const posBody = document.getElementById('positions-body');
                const positions = Object.entries(data.open_positions || {});
                document.getElementById('pos-count').textContent = positions.length + ' позиций';

                if (positions.length === 0) {
                    posBody.innerHTML = '<tr><td colspan="10" style="text-align: center; color: var(--text-muted); padding: 24px;">Нет открытых позиций</td></tr>';
                } else {
                    posBody.innerHTML = '';
                    positions.forEach(([sym, p]) => {
                        const tr = document.createElement('tr');
                        const pnlVal = p.unrealized_pnl || 0;
                        const pnlClass = pnlVal >= 0 ? 'val-green' : 'val-red';
                        const sideBadge = p.direction === 'LONG' ? '<span class="badge badge-long">LONG</span>' : '<span class="badge badge-short">SHORT</span>';
                        tr.innerHTML = `
                            <td><b>${sym}</b></td>
                            <td>${sideBadge}</td>
                            <td>${p.qty}</td>
                            <td>$${Number(p.entry_price).toFixed(4)}</td>
                            <td>$${Number(p.current_price).toFixed(4)}</td>
                            <td class="val-red">$${Number(p.current_stop).toFixed(4)}</td>
                            <td class="val-green">${p.tp1_price ? '$' + Number(p.tp1_price).toFixed(4) : '--'}</td>
                            <td class="val-green">${p.tp2_price ? '$' + Number(p.tp2_price).toFixed(4) : '--'}</td>
                            <td class="${pnlClass}">${pnlVal >= 0 ? '+' : ''}$${pnlVal.toFixed(2)}</td>
                            <td><span class="badge" style="background: rgba(168, 85, 247, 0.2); color: #d8b4fe;">G${p.entry_gear || gear}</span></td>
                        `;
                        posBody.appendChild(tr);
                    });
                }
            } catch (err) {
                console.error("Dashboard update error:", err);
            }
        }

        async function updateLogs() {
            try {
                const res = await fetch('/api/logs');
                if (res.ok) {
                    const logs = await res.json();
                    const box = document.getElementById('log-box');
                    box.innerHTML = logs.slice(-50).map(l => `<div>${l}</div>`).join('');
                    box.scrollTop = box.scrollHeight;
                }
            } catch (err) {}
        }

        setInterval(() => {
            const now = new Date();
            document.getElementById('live-clock').textContent = now.toUTCString().split(' ')[4] + ' UTC';
        }, 1000);

        renderGearsTrack(1);
        updateDashboard();
        updateLogs();
        setInterval(updateDashboard, 2000);
        setInterval(updateLogs, 4000);
    </script>
</body>
</html>
"""


class DashboardHTTPRequestHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ["/", "/index.html"]:
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
    logger.info("Initializing PaperTraderV34 daemon on Port 8087...")
    GLOBAL_TRADER = PaperTraderV34()

    bot_thread = threading.Thread(target=GLOBAL_TRADER.run_daemon, daemon=True)
    bot_thread.start()
    logger.info("Background PaperTraderV34 thread launched.")

    server_address = ("0.0.0.0", PORT)
    with socketserver.ThreadingTCPServer(server_address, DashboardHTTPRequestHandler) as httpd:
        logger.info(f"Multi-Alpha v3.4 ZF-8 Quant (Port {PORT}) Web Dashboard live on http://127.0.0.1:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("Server interrupted. Shutting down...")
            GLOBAL_TRADER.stop()
            httpd.server_close()


if __name__ == "__main__":
    start_server_and_bot()
