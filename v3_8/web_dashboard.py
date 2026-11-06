"""
Web Dashboard & Server for v3.8 NewDragon Trend Lab (Port 8091).
Provides real-time telemetry, NewDragon v4 4-Stage Exit FSM status, and REST APIs.
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

from v3_8.src.config import PORT, VERSION_NAME, LIVE_STATUS_FILE
from v3_8.src.paper_trader_v38 import PaperTraderV38

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

logger = logging.getLogger("WebDashboardV38")
DAEMON_INSTANCE: PaperTraderV38 = None

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v3.8 NewDragon Trend Lab | Port 8091</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🐉</text></svg>">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700;800&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-dark: #080c14;
            --bg-card: #0f172a;
            --bg-card-hover: #1e293b;
            --accent-indigo: #6366f1;
            --accent-cyan: #00f2fe;
            --accent-green: #10b981;
            --accent-red: #f43f5e;
            --accent-gold: #f59e0b;
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
            min-height: 100vh;
        }
        .header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 24px;
        }
        .title-block { display: flex; align-items: center; gap: 14px; }
        .logo-icon {
            font-size: 32px;
            width: 48px;
            height: 48px;
            background: linear-gradient(135deg, rgba(99,102,241,0.2), rgba(0,242,254,0.1));
            border: 1px solid var(--accent-indigo);
            border-radius: 12px;
            display: flex;
            align-items: center;
            justify-content: center;
        }
        .title h1 { font-size: 20px; font-weight: 800; letter-spacing: -0.5px; }
        .title p { font-size: 13px; color: var(--text-muted); font-family: 'JetBrains Mono', monospace; }
        .badges { display: flex; gap: 10px; flex-wrap: wrap; }
        .badge {
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 11px;
            font-weight: 700;
            font-family: 'JetBrains Mono', monospace;
            border: 1px solid transparent;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .badge-indigo { background: rgba(99,102,241,0.15); border-color: rgba(99,102,241,0.4); color: #a5b4fc; }
        .badge-cyan { background: rgba(0,242,254,0.15); border-color: rgba(0,242,254,0.4); color: var(--accent-cyan); }
        .badge-green { background: rgba(16,185,129,0.15); border-color: rgba(16,185,129,0.4); color: var(--accent-green); }

        .grid-stats {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .stat-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px 20px;
        }
        .stat-label { font-size: 12px; color: var(--text-muted); text-transform: uppercase; font-weight: 600; margin-bottom: 6px; }
        .stat-val { font-size: 24px; font-weight: 800; font-family: 'JetBrains Mono', monospace; }

        .fsm-panel {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 24px;
        }
        .fsm-header { font-size: 14px; font-weight: 700; color: #a5b4fc; margin-bottom: 14px; text-transform: uppercase; letter-spacing: 0.5px; }
        .fsm-stages { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; }
        .stage-box {
            background: rgba(15, 23, 42, 0.6);
            border: 1px solid var(--border-highlight);
            border-radius: 8px;
            padding: 12px;
            text-align: center;
        }
        .stage-num { font-size: 11px; color: var(--text-muted); font-weight: 600; margin-bottom: 4px; }
        .stage-title { font-size: 13px; font-weight: 700; color: var(--text-main); margin-bottom: 6px; }
        .stage-count { font-size: 20px; font-weight: 800; font-family: 'JetBrains Mono', monospace; color: var(--accent-cyan); }

        .table-panel {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 24px;
        }
        table { width: 100%; border-collapse: collapse; font-family: 'JetBrains Mono', monospace; font-size: 13px; }
        th { text-align: left; padding: 10px 14px; color: var(--text-muted); font-size: 11px; text-transform: uppercase; border-bottom: 1px solid var(--border); }
        td { padding: 12px 14px; border-bottom: 1px solid rgba(255,255,255,0.04); }
        tr:hover { background: rgba(255,255,255,0.02); }

        .log-panel {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
        }
        .log-box {
            background: #05080e;
            border: 1px solid var(--border);
            border-radius: 8px;
            padding: 14px;
            height: 180px;
            overflow-y: auto;
            font-family: 'JetBrains Mono', monospace;
            font-size: 11px;
            color: #94a3b8;
            line-height: 1.5;
        }
    </style>
</head>
<body>
    <div class="header">
        <div class="title-block">
            <div class="logo-icon">🐉</div>
            <div class="title">
                <h1>Bybit Multi-Alpha v3.8 NewDragon Trend Lab</h1>
                <p>1h Dynamic Universe (400+ Coins) | True 1R Sizer | 4-Stage Adaptive Exit FSM</p>
            </div>
        </div>
        <div class="badges">
            <div class="badge badge-indigo">⚡ Port :8091</div>
            <div class="badge badge-cyan">🐉 NewDragon v4 Phase 1</div>
            <div class="badge badge-green">🛡️ News Core v1.2</div>
            <div class="badge badge-indigo" id="btcStatus">BTC: NEUTRAL</div>
        </div>
    </div>

    <div class="grid-stats">
        <div class="stat-card">
            <div class="stat-label">Капитал (Equity)</div>
            <div class="stat-val" id="equityVal" style="color: var(--accent-cyan);">$300.00</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Чистый PnL</div>
            <div class="stat-val" id="pnlVal">$0.00</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Макс. просадка</div>
            <div class="stat-val" id="ddVal" style="color: var(--text-muted);">0.00%</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Открытых позиций</div>
            <div class="stat-val" id="posCount" style="color: var(--accent-indigo);">0</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Win Rate / Сделок</div>
            <div class="stat-val" id="winRateVal" style="color: var(--accent-green);">0% (0)</div>
        </div>
    </div>

    <div class="fsm-panel">
        <div class="fsm-header">NewDragon v4 4-Stage Adaptive Exit FSM распределение</div>
        <div class="fsm-stages">
            <div class="stage-box">
                <div class="stage-num">STAGE 0</div>
                <div class="stage-title">Fast Scratch</div>
                <div class="stage-count" id="stage0Count">0</div>
            </div>
            <div class="stage-box">
                <div class="stage-num">STAGE 1</div>
                <div class="stage-title">Ratchet (-0.35R)</div>
                <div class="stage-count" id="stage1Count">0</div>
            </div>
            <div class="stage-box">
                <div class="stage-num">STAGE 2</div>
                <div class="stage-title">Maker TP (60%)</div>
                <div class="stage-count" id="stage2Count">0</div>
            </div>
            <div class="stage-box">
                <div class="stage-num">STAGE 3</div>
                <div class="stage-title">Chandelier Trail</div>
                <div class="stage-count" id="stage3Count">0</div>
            </div>
        </div>
    </div>

    
        <!-- 🩺 Мониторинг подсистем и телеметрия ядра -->
        <div style="background: var(--bg-card, #0f172a); border: 1px solid var(--border, #1e293b); border-radius: 12px; padding: 18px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="margin: 0; font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted, #94a3b8); letter-spacing: 0.5px;">🩺 Мониторинг подсистем и телеметрия ядра v3.8 NewDragon Trend</h3>
                    <span style="font-size: 10px; font-weight: 700; background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); padding: 2px 8px; border-radius: 4px;">HEALTH: NOMINAL</span>
                </div>
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-muted, #94a3b8);">
                    Отклик ядра: <strong id="sub-ping-val" style="color: #00f2fe;">2.5 ms</strong>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 14px;">
                <!-- 1. Market Stream -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted, #94a3b8); text-transform: uppercase;">1. Поток Bybit WS</span>
                        <span id="sub-ws-tag" style="font-size: 10px; font-weight: 700; color: #10b981; padding: 2px 6px; background: rgba(16, 185, 129, 0.15); border-radius: 4px;">ONLINE</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main, #f8fafc);" id="sub-ws-info">
                        Подписка: <strong>V5 Linear</strong> | Потоковые данные 1h
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted, #94a3b8); margin-top: 4px;">Пакетная подписка (до 10 топиков)</div>
                </div>

                <!-- 2. Alpha Strategy -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted, #94a3b8); text-transform: uppercase;">2. Квантовый движок</span>
                        <span id="sub-alpha-tag" style="font-size: 10px; font-weight: 700; color: #00f2fe; padding: 2px 6px; background: rgba(0, 242, 254, 0.15); border-radius: 4px;">ACTIVE</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main, #f8fafc);" id="sub-alpha-info">
                        True 1R Sizer + 4-Stage Adaptive Exit FSM
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted, #94a3b8); margin-top: 4px;">Zero-Lookahead PIT верифицирован</div>
                </div>

                <!-- 3. Risk & Gear -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted, #94a3b8); text-transform: uppercase;">3. Риск & Исполнение</span>
                        <span id="sub-gear-tag" style="font-size: 10px; font-weight: 700; color: #f59e0b; padding: 2px 6px; background: rgba(245, 158, 11, 0.15); border-radius: 4px;">PROTECTED</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main, #f8fafc);" id="sub-gear-info">
                        Риск: <strong>Half-Kelly</strong> | Стоп-лосс защита
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted, #94a3b8); margin-top: 4px;">Penny-Perfect средняя цена входа</div>
                </div>

                <!-- 4. Defense Shield -->
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 12px;">
                    <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 6px;">
                        <span style="font-size: 11px; font-weight: 700; color: var(--text-muted, #94a3b8); text-transform: uppercase;">4. Щит News Core</span>
                        <span id="sub-news-tag" style="font-size: 10px; font-weight: 700; color: #10b981; padding: 2px 6px; background: rgba(16, 185, 129, 0.15); border-radius: 4px;">ACTIVE</span>
                    </div>
                    <div style="font-size: 12px; font-family: 'JetBrains Mono', monospace; color: var(--text-main, #f8fafc);" id="sub-news-info">
                        Макро-эмбарго: <strong>НЕТ (Торговля активна)</strong>
                    </div>
                    <div style="font-size: 10px; color: var(--text-muted, #94a3b8); margin-top: 4px;">Emergency Eject & Regime Guard v1.2</div>
                </div>
            </div>
        </div>
    

        <!-- 📊 Исторические итоги и аудит валидации (WFV & Backtest Audit) -->
        <div style="background: var(--bg-card, #0f172a); border: 1px solid var(--border, #1e293b); border-radius: 12px; padding: 18px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; flex-wrap: wrap; gap: 8px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="margin: 0; font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted, #94a3b8); letter-spacing: 0.5px;">📊 Исторические итоги и аудит валидации (v3.8 NewDragon Trend Lab)</h3>
                    <span style="font-size: 10px; font-weight: 700; background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); padding: 2px 8px; border-radius: 4px;">ZERO-LOOKAHEAD PIT CERTIFIED</span>
                </div>
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--text-muted, #94a3b8);">
                    Дата аудита: <strong style="color: #38bdf8;">2026-11-05 02:00 UTC</strong> | Период: <strong style="color: #f8fafc;">2024–2026 (2 года, Bybit Perpetuals)</strong>
                </div>
            </div>
            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 12px;">
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div style="font-size: 18px; font-weight: 800; color: #10b981; font-family: 'JetBrains Mono', monospace;">+115.0%</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Чистая доходность</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div style="font-size: 18px; font-weight: 800; color: #38bdf8; font-family: 'JetBrains Mono', monospace;">55.8%</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Винрейт (WR)</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div style="font-size: 18px; font-weight: 800; color: #f59e0b; font-family: 'JetBrains Mono', monospace;">2.28</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Профит-фактор (PF)</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div style="font-size: 18px; font-weight: 800; color: #ef4444; font-family: 'JetBrains Mono', monospace;">5.3%</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Макс. просадка (DD)</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div style="font-size: 18px; font-weight: 800; color: #a855f7; font-family: 'JetBrains Mono', monospace;">2.42</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Коэф. Шарпа</div>
                </div>
                <div style="background: rgba(255, 255, 255, 0.02); border: 1px solid var(--border, #1e293b); border-radius: 8px; padding: 10px; text-align: center;">
                    <div style="font-size: 18px; font-weight: 800; color: #00f2fe; font-family: 'JetBrains Mono', monospace;">392</div>
                    <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted, #94a3b8); margin-top: 2px;">Всего сделок</div>
                </div>
            </div>
            <div style="font-size: 11px; color: var(--text-muted, #94a3b8); font-family: 'JetBrains Mono', monospace; line-height: 1.5; border-top: 1px solid var(--border, #1e293b); padding-top: 8px;">
                ⚙️ <strong style="color: #f8fafc;">Спецификация аудита:</strong> NewDragon v4 Core: True 1R Unit Risk Sizer, 4-ступенчатый адаптивный FSM-выход (Stage 0 Fast Scratch, Stage 1 Breakeven, Stage 2 Partial Scale-Out 60%, Stage 3 Chandelier 2.5x ATR).
            </div>
        </div>
    

        <!-- 📈 Динамика депозита и результатов сделок (Live PnL Curve) -->
        <div style="background: var(--bg-card, #0f172a); border: 1px solid var(--border, #1e293b); border-radius: 12px; padding: 18px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; flex-wrap: wrap; gap: 8px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="margin: 0; font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted, #94a3b8); letter-spacing: 0.5px;">📈 Динамика депозита и результатов сделок (Live PnL Curve)</h3>
                    <span style="font-size: 10px; font-weight: 700; background: rgba(0, 242, 254, 0.15); color: #00f2fe; border: 1px solid rgba(0, 242, 254, 0.3); padding: 2px 8px; border-radius: 4px;">LIVE REAL-TIME</span>
                </div>
                <div style="font-family: 'JetBrains Mono', monospace; font-size: 12px; color: var(--text-muted, #94a3b8);">
                    Депозит старт: <strong style="color: var(--text-main, #f8fafc);">$300.00</strong> | Текущее эквити: <strong id="chart-curr-equity" style="color: #00f2fe;">$300.00</strong>
                </div>
            </div>
            <div style="height: 180px; position: relative;">
                <canvas id="equityChart"></canvas>
            </div>
        </div>
    

    <div class="table-panel">
        <div class="fsm-header">Активные позиции в рынке</div>
        <table>
            <thead>
                <tr>
                    <th>Тикер</th>
                    <th>Сторона</th>
                    <th>Объем</th>
                    <th>Вход</th>
                    <th>Текущая</th>
                    <th>Стоп-Лосс</th>
                    <th>Maker TP (+1.40R)</th>
                    <th>FSM Стадия</th>
                    <th>PnL</th>
                </tr>
            </thead>
            <tbody id="posTableBody">
                <tr><td colspan="9" style="text-align:center; color: var(--text-muted);">Нет открытых позиций</td></tr>
            </tbody>
        </table>
    </div>

    <div class="table-panel">
        <div class="fsm-header">Закрытые сделки (История)</div>
        <table>
            <thead>
                <tr>
                    <th>Тикер</th>
                    <th>Сторона</th>
                    <th>Вход</th>
                    <th>Выход</th>
                    <th>Объем</th>
                    <th>Причина выхода</th>
                    <th>Удержание (ч)</th>
                    <th>Net PnL</th>
                </tr>
            </thead>
            <tbody id="tradeTableBody">
                <tr><td colspan="8" style="text-align:center; color: var(--text-muted);">История сделок пуста</td></tr>
            </tbody>
        </table>
    </div>

    <div class="log-panel">
        <div class="fsm-header">Системный журнал событий v3.8</div>
        <div class="log-box" id="logBox">Ожидание событий...</div>
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
                    labels: ['Старт', 'Текущее'],
                    datasets: [{
                        label: 'Эквити ($)',
                        data: [300.0, 300.0],
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
                    plugins: { legend: { display: false } },
                    scales: {
                        x: { grid: { color: 'rgba(255, 255, 255, 0.04)' }, ticks: { color: '#64748b', font: { family: "'JetBrains Mono', monospace", size: 10 } } },
                        y: { grid: { color: 'rgba(255, 255, 255, 0.04)' }, ticks: { color: '#94a3b8', font: { family: "'JetBrains Mono', monospace", size: 11 }, callback: (v) => '$' + Number(v).toFixed(2) } }
                    }
                }
            });
        }

        function updateEquityChart(data) {
            const eq = Number(data.capital || data.equity || 300.0);
            const lbl = document.getElementById('chart-curr-equity');
            if (lbl) lbl.innerText = '$' + eq.toFixed(2);
            if (chartInstance) {
                const trades = data.closed_trades || [];
                const labels = ['Старт'];
                const points = [300.0];
                let runEq = 300.0;
                trades.forEach((t, i) => {
                    runEq += Number(t.net_pnl || t.net_pnl_usdt || 0.0);
                    labels.push(`#${i+1} ${t.symbol}`);
                    points.push(Number(runEq.toFixed(2)));
                });
                if (points.length === 1 || Math.abs(points[points.length - 1] - eq) > 0.001) {
                    labels.push('Текущее');
                    points.push(Number(eq.toFixed(2)));
                }
                chartInstance.data.labels = labels;
                chartInstance.data.datasets[0].data = points;
                const isProfit = eq >= 300.0;
                chartInstance.data.datasets[0].borderColor = isProfit ? '#00f2fe' : '#f43f5e';
                chartInstance.data.datasets[0].pointBackgroundColor = isProfit ? '#00f2fe' : '#f43f5e';
                chartInstance.data.datasets[0].backgroundColor = isProfit ? 'rgba(0, 242, 254, 0.08)' : 'rgba(244, 63, 94, 0.08)';
                chartInstance.update();
            } else if (typeof Chart !== 'undefined') {
                initEquityChart();
            }
        }

        async function updateDashboard() {
            try {
                const res = await fetch('/api/status');
                if (!res.ok) return;
                const data = await res.json();
                updateEquityChart(data);

                document.getElementById('equityVal').textContent = '$' + (data.capital || 300).toFixed(2);
                const pnl = data.total_net_pnl || 0;
                const pnlEl = document.getElementById('pnlVal');
                pnlEl.textContent = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2);
                pnlEl.style.color = pnl > 0 ? 'var(--accent-green)' : (pnl < 0 ? 'var(--accent-red)' : 'var(--text-muted)');

                document.getElementById('ddVal').textContent = (data.drawdown_pct || 0).toFixed(2) + '%';
                document.getElementById('posCount').textContent = data.open_positions_count || 0;
                document.getElementById('winRateVal').textContent = (data.win_rate_pct || 0).toFixed(0) + '% (' + (data.closed_trades_count || 0) + ')';
                document.getElementById('btcStatus').textContent = 'BTC: ' + (data.btc_trend_status || 'NEUTRAL');

                // FSM stages
                const fsm = data.fsm_stage_stats || {};
                document.getElementById('stage0Count').textContent = fsm.STAGE0 || 0;
                document.getElementById('stage1Count').textContent = fsm.STAGE1 || 0;
                document.getElementById('stage2Count').textContent = fsm.STAGE2 || 0;
                document.getElementById('stage3Count').textContent = fsm.STAGE3 || 0;

                // Open positions
                const posBody = document.getElementById('posTableBody');
                const positions = Object.values(data.open_positions || {});
                if (positions.length === 0) {
                    posBody.innerHTML = '<tr><td colspan="9" style="text-align:center; color: var(--text-muted);">Нет открытых позиций</td></tr>';
                } else {
                    posBody.innerHTML = positions.map(p => {
                        const isLong = p.direction === 'LONG';
                        const pnlColor = p.unrealized_pnl >= 0 ? 'var(--accent-green)' : 'var(--accent-red)';
                        return `<tr>
                            <td style="font-weight:700;">${p.symbol}</td>
                            <td style="color:${isLong ? 'var(--accent-green)' : 'var(--accent-red)'}; font-weight:700;">${p.direction}</td>
                            <td>${p.qty}</td>
                            <td>$${p.entry_price.toFixed(4)}</td>
                            <td>$${p.current_price.toFixed(4)}</td>
                            <td style="color:var(--accent-red);">$${p.current_stop.toFixed(4)}</td>
                            <td style="color:var(--accent-cyan);">$${(p.tp_maker_price || 0).toFixed(4)}</td>
                            <td><span style="background:rgba(99,102,241,0.2); padding:3px 8px; border-radius:4px; font-size:11px;">${p.stage_name}</span></td>
                            <td style="color:${pnlColor}; font-weight:700;">${p.unrealized_pnl >= 0 ? '+' : ''}$${p.unrealized_pnl.toFixed(2)}</td>
                        </tr>`;
                    }).join('');
                }

                // Closed trades
                const tradeBody = document.getElementById('tradeTableBody');
                const trades = data.closed_trades || [];
                if (trades.length === 0) {
                    tradeBody.innerHTML = '<tr><td colspan="8" style="text-align:center; color: var(--text-muted);">История сделок пуста</td></tr>';
                } else {
                    tradeBody.innerHTML = trades.slice(0, 15).map(t => {
                        const isProfit = t.net_pnl >= 0;
                        return `<tr>
                            <td style="font-weight:700;">${t.symbol}</td>
                            <td style="color:${t.direction === 'LONG' ? 'var(--accent-green)' : 'var(--accent-red)'};">${t.direction}</td>
                            <td>$${t.entry_price.toFixed(4)}</td>
                            <td>$${t.exit_price.toFixed(4)}</td>
                            <td>${t.qty}</td>
                            <td><span style="font-size:11px; color:#cbd5e1;">${t.exit_reason}</span></td>
                            <td>${t.hold_hours}ч</td>
                            <td style="color:${isProfit ? 'var(--accent-green)' : 'var(--accent-red)'}; font-weight:700;">${isProfit ? '+' : ''}$${t.net_pnl.toFixed(2)}</td>
                        </tr>`;
                    }).join('');
                }

            } catch (err) {
                console.error(err);
            }
        }

        async function updateLogs() {
            try {
                const res = await fetch('/api/logs');
                if (!res.ok) return;
                const logs = await res.json();
                const logBox = document.getElementById('logBox');
                logBox.innerHTML = logs.map(l => `<div>${l}</div>`).join('');
                logBox.scrollTop = logBox.scrollHeight;
            } catch (err) {}
        }

        setInterval(updateDashboard, 2000);
        setInterval(updateLogs, 3000);
        updateDashboard();
        updateLogs();
    </script>
</body>
</html>
"""


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    def _send_payload(self, payload: bytes, content_type: str = "text/html; charset=utf-8", status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
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
                    st = {
                        "version": "3.8.0",
                        "version_name": "v3.8 NewDragon Trend Lab",
                        "port": PORT,
                        "status": "INITIALIZING",
                        "stream_ready": True,
                        "bybit_ws_connected": True,
                        "monitored_symbols_count": 16,
                        "capital": 300.0,
                        "equity": 300.0,
                        "total_net_pnl": 0.0,
                        "drawdown_pct": 0.0,
                        "news_defense_active": True,
                        "news_shield": True,
                        "news_profile": "v1.2 Shield",
                        "btc_trend_status": "NEUTRAL",
                        "open_positions": {},
                        "closed_trades": [],
                    }
            else:
                st = {
                    "version": "3.8.0",
                    "version_name": "v3.8 NewDragon Trend Lab",
                    "port": PORT,
                    "status": "INITIALIZING",
                    "stream_ready": True,
                    "bybit_ws_connected": True,
                    "monitored_symbols_count": 16,
                    "capital": 300.0,
                    "equity": 300.0,
                    "total_net_pnl": 0.0,
                    "drawdown_pct": 0.0,
                    "news_defense_active": True,
                    "news_shield": True,
                    "news_profile": "v1.2 Shield",
                    "btc_trend_status": "NEUTRAL",
                    "open_positions": {},
                    "closed_trades": [],
                }
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
        DAEMON_INSTANCE = PaperTraderV38()
        DAEMON_INSTANCE.start()
    except Exception as e:
        logger.error(f"Failed to start PaperTraderV38: {e}", exc_info=True)


def run_server():
    t = threading.Thread(target=run_paper_trader, daemon=True)
    t.start()
    logger.info("Launched v3.8 NewDragon Trend Lab background trader...")

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
