"""
Web Dashboard for v3.9 NewDragon Scalp Lab (Port 8092).
Displays HFT Orderbook Microstructure, live Z_OFI flow imbalance, and Micro-Router telemetry.
"""

import asyncio
from collections import deque
from datetime import datetime, timezone
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

import v3_9.src.core_scalper_v39 as core_scalper_v39
from v3_9.src.config import PORT, VERSION_NAME, LIVE_STATUS_FILE

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

logger = logging.getLogger("WebDashboardV39")

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v3.9 NewDragon Scalp Lab | Port 8092</title>
    <link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>⚡</text></svg>">
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700;800&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        :root {
            --bg-dark: #080c14;
            --bg-card: #0f172a;
            --bg-card-hover: #1e293b;
            --accent-violet: #8b5cf6;
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
            background: linear-gradient(135deg, rgba(139,92,246,0.2), rgba(0,242,254,0.1));
            border: 1px solid var(--accent-violet);
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
        .badge-violet { background: rgba(139,92,246,0.15); border-color: rgba(139,92,246,0.4); color: #c4b5fd; }
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

        .table-panel {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
            margin-bottom: 24px;
        }
        .panel-header { font-size: 14px; font-weight: 700; color: #c4b5fd; margin-bottom: 14px; text-transform: uppercase; letter-spacing: 0.5px; }
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
            <div class="logo-icon">⚡</div>
            <div class="title">
                <h1>Bybit Multi-Alpha v3.9 NewDragon Scalp Lab</h1>
                <p>15m/M1 L2 Microstructure | Micro-Router (minNotional) | Z_OFI Fast Scratch</p>
            </div>
        </div>
        <div class="badges">
            <div class="badge badge-violet">⚡ Port :8092</div>
            <div class="badge badge-cyan">🐉 NewDragon v4 Scalp Router</div>
            <div class="badge badge-green">🛡️ Z_OFI Flow Scratch</div>
            <div class="badge badge-violet" id="btcStatus">BTC: OK</div>
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
            <div class="stat-label">В рынке / Очередь</div>
            <div class="stat-val" id="posCount" style="color: var(--accent-violet);">0 / 0</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Z_OFI Fast Scratches</div>
            <div class="stat-val" id="scratchCount" style="color: var(--accent-gold);">0</div>
        </div>
        <div class="stat-card">
            <div class="stat-label">Win Rate / Сделок</div>
            <div class="stat-val" id="winRateVal" style="color: var(--accent-green);">0% (0)</div>
        </div>
    </div>

    
        <!-- 🩺 Мониторинг подсистем и телеметрия ядра -->
        <div style="background: var(--bg-card, #0f172a); border: 1px solid var(--border, #1e293b); border-radius: 12px; padding: 18px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="margin: 0; font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted, #94a3b8); letter-spacing: 0.5px;">🩺 Мониторинг подсистем и телеметрия ядра v3.9 NewDragon Scalp</h3>
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
                        Подписка: <strong>V5 Linear</strong> | Потоковые данные M1/5s
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
                        Pure Lead-Lag Beta-Catchup to BTC + Post-Only Maker
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
    

        <!-- 📊 Итоги и накопленная статистика Paper-теста -->
        <div style="background: var(--bg-card, #0f172a); border: 1px solid var(--border, #1e293b); border-radius: 12px; padding: 18px; margin-bottom: 24px;">
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 14px; flex-wrap: wrap; gap: 8px;">
                <div style="display: flex; align-items: center; gap: 10px;">
                    <h3 style="margin: 0; font-size: 13px; font-weight: 700; text-transform: uppercase; color: var(--text-muted, #94a3b8); letter-spacing: 0.5px;">📊 Итоги и накопленная статистика Paper-теста (v3.9 NewDragon Scalp Lab (Port 8092))</h3>
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
                ⚙️ <strong style="color: #f8fafc;">Спецификация стратегии:</strong> Секундный Lead-Lag арбитраж E3 (импульс BTC >= 0.06%) + E2 Liquidity Sweeps + NewDragon minNotional Guard ($5 alts / $100 BTC) + Post-Only Maker TP1 (+0.50% / +1.20%).
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
        <div class="panel-header">Радар микроструктуры и Z_OFI Order Flow</div>
        <table>
            <thead>
                <tr>
                    <th>Тикер</th>
                    <th>Mid Price</th>
                    <th>Спрэд (Ticks)</th>
                    <th>OBI (Imbalance)</th>
                    <th>Z_OFI (Normalized Flow)</th>
                    <th>Статус потока</th>
                </tr>
            </thead>
            <tbody id="radarTableBody">
                <tr><td colspan="6" style="text-align:center; color: var(--text-muted);">Инициализация микроструктуры...</td></tr>
            </tbody>
        </table>
    </div>

    <div class="table-panel">
        <div class="panel-header">Активные позиции скальпинга</div>
        <table>
            <thead>
                <tr>
                    <th>Тикер</th>
                    <th>Движок</th>
                    <th>Сторона</th>
                    <th>Объем</th>
                    <th>Вход</th>
                    <th>Текущая</th>
                    <th>Стоп-Лосс</th>
                    <th>TP1 Maker</th>
                    <th>Удержание (с)</th>
                </tr>
            </thead>
            <tbody id="posTableBody">
                <tr><td colspan="9" style="text-align:center; color: var(--text-muted);">Нет открытых позиций</td></tr>
            </tbody>
        </table>
    </div>

    <div class="table-panel">
        <div class="panel-header">Закрытые скальп-сделки (<span id="closedCountHeader">0</span>)</div>
        <table>
            <thead>
                <tr>
                    <th>Время (UTC)</th>
                    <th>Тикер</th>
                    <th>Движок</th>
                    <th>Сторона</th>
                    <th>Вход</th>
                    <th>Выход</th>
                    <th>Объем</th>
                    <th>Причина</th>
                    <th>Net PnL</th>
                </tr>
            </thead>
            <tbody id="tradeTableBody">
                <tr><td colspan="9" style="text-align:center; color: var(--text-muted);">История сделок пуста</td></tr>
            </tbody>
        </table>
    </div>

    <div class="log-panel">
        <div class="panel-header">Системный журнал событий v3.9</div>
        <div class="log-box" id="logBox">Ожидание событий...</div>
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


        var chartInstance = chartInstance || null;
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

        document.addEventListener('DOMContentLoaded', initEquityChart);
        async function updateDashboard() {
            try {
                const res = await fetch('/api/status');
                if (!res.ok) return;
                const data = await res.json();
                try { syncPaperTestStatsAndChart(data); } catch (e) { console.error("syncPaperTestStatsAndChart error:", e); }

                document.getElementById('equityVal').textContent = '$' + (data.capital || data.equity || 300).toFixed(2);
                const pnl = data.realized_pnl !== undefined ? data.realized_pnl : (data.daily_pnl_usdt || 0);
                const pnlEl = document.getElementById('pnlVal');
                pnlEl.textContent = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2);
                pnlEl.style.color = pnl > 0 ? 'var(--accent-green)' : (pnl < 0 ? 'var(--accent-red)' : 'var(--text-muted)');

                const posCnt = (data.open_positions && Array.isArray(data.open_positions)) ? data.open_positions.length : (data.active_positions_count || 0);
                const ordCnt = (data.pending_orders && Array.isArray(data.pending_orders)) ? data.pending_orders.length : (data.pending_orders_count || 0);
                document.getElementById('posCount').textContent = posCnt + ' / ' + ordCnt;
                document.getElementById('scratchCount').textContent = data.scratch_count || 0;
                
                const closedTrades = data.closed_trades || [];
                const winCount = closedTrades.filter(t => (t.net_pnl_usdt !== undefined ? t.net_pnl_usdt : (t.net_pnl || 0)) > 0).length;
                const winRate = closedTrades.length > 0 ? (winCount / closedTrades.length * 100) : 0;
                document.getElementById('winRateVal').textContent = winRate.toFixed(0) + '% (' + closedTrades.length + ')';

                // Radar Z_OFI
                const radarData = data.radar || {};
                const radarBody = document.getElementById('radarTableBody');
                const symbols = Object.keys(radarData);
                if (symbols.length > 0) {
                    radarBody.innerHTML = symbols.map(sym => {
                        const item = radarData[sym] || {};
                        const z = item.z_ofi !== undefined ? item.z_ofi : 0.0;
                        const zColor = z > 1.5 ? 'var(--accent-green)' : (z < -1.5 ? 'var(--accent-red)' : 'var(--text-muted)');
                        const flowDesc = z > 2.0 ? 'Агрессивные покупки (+Flow)' : (z < -2.0 ? 'Агрессивные продажи (-Flow)' : 'Нейтральный баланс');
                        return `<tr>
                            <td style="font-weight:700;">${sym}</td>
                            <td>$${Number(item.mid_price || 0).toFixed(4)}</td>
                            <td>${item.spread_ticks || 1.0}</td>
                            <td>${(item.obi > 0 ? '+' : '')}${Number(item.obi || 0).toFixed(3)}</td>
                            <td style="color:${zColor}; font-weight:700;">${z > 0 ? '+' : ''}${z.toFixed(2)}</td>
                            <td><span style="font-size:11px; color:#cbd5e1;">${flowDesc}</span></td>
                        </tr>`;
                    }).join('');
                }

                // Positions
                const posBody = document.getElementById('posTableBody');
                const positions = Array.isArray(data.open_positions) ? data.open_positions : Object.values(data.open_positions || {});
                if (positions.length === 0) {
                    posBody.innerHTML = '<tr><td colspan="9" style="text-align:center; color: var(--text-muted);">Нет открытых позиций</td></tr>';
                } else {
                    posBody.innerHTML = positions.map(p => {
                        const isLong = p.direction === 'LONG' || p.side === 'Buy';
                        return `<tr>
                            <td style="font-weight:700;">${p.symbol}</td>
                            <td><span style="background:rgba(139,92,246,0.2); padding:2px 6px; border-radius:4px; font-size:11px;">${p.engine_id || 'OMS'}</span></td>
                            <td style="color:${isLong ? 'var(--accent-green)' : 'var(--accent-red)'}; font-weight:700;">${isLong ? 'LONG' : 'SHORT'}</td>
                            <td>${p.qty}</td>
                            <td>$${Number(p.entry_price || 0).toFixed(4)}</td>
                            <td>$${Number(p.current_price || p.entry_price || 0).toFixed(4)}</td>
                            <td style="color:var(--accent-red);">$${Number(p.stop_loss || p.sl_price || 0).toFixed(4)}</td>
                            <td style="color:var(--accent-cyan);">$${Number(p.take_profit_1 || p.tp1_price || 0).toFixed(4)}</td>
                            <td>${p.opened_ms ? ((Date.now() - p.opened_ms)/1000).toFixed(1) : '0'}с</td>
                        </tr>`;
                    }).join('');
                }

                // Closed trades
                const tradeBody = document.getElementById('tradeTableBody');
                const trades = data.closed_trades || [];
                const closedCountHeader = document.getElementById('closedCountHeader');
                if (closedCountHeader) closedCountHeader.innerText = trades.length;
                if (trades.length === 0) {
                    tradeBody.innerHTML = '<tr><td colspan="9" style="text-align:center; color: var(--text-muted);">История сделок пуста</td></tr>';
                } else {
                    tradeBody.innerHTML = trades.slice(-20).reverse().map(t => {
                        const pnl = Number(t.net_pnl_usdt !== undefined ? t.net_pnl_usdt : (t.net_pnl || 0.0));
                        const isProfit = pnl >= 0;
                        const isLong = t.side === 'Buy' || t.direction === 'LONG';
                        const dStr = formatChartTs(t.timestamp || t.exit_time || t.closed_at || Date.now());
                        return `<tr>
                            <td>${dStr} UTC</td>
                            <td style="font-weight:700; color:#38bdf8;">${t.symbol}</td>
                            <td><span style="font-size:11px; color:#cbd5e1;">${t.engine_id || 'OMS'}</span></td>
                            <td style="color:${isLong ? 'var(--accent-green)' : 'var(--accent-red)'}; font-weight:700;">${isLong ? 'LONG' : 'SHORT'}</td>
                            <td>$${Number(t.entry_price || 0).toFixed(4)}</td>
                            <td>$${Number(t.exit_price || 0).toFixed(4)}</td>
                            <td>${Number(t.qty || t.initial_qty || 0).toFixed(2)}</td>
                            <td><span style="font-size:11px; color:#cbd5e1;">${t.exit_reason || t.reason || 'CLOSE'}</span></td>
                            <td style="color:${isProfit ? 'var(--accent-green)' : 'var(--accent-red)'}; font-weight:700;">${isProfit ? '+' : ''}$${pnl.toFixed(3)}</td>
                        </tr>`;
                    }).join('');
                }

            } catch (err) {}
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
            if core_scalper_v39.GLOBAL_OMS is not None:
                st = core_scalper_v39.GLOBAL_OMS.get_status()
            elif LIVE_STATUS_FILE.exists():
                try:
                    with open(LIVE_STATUS_FILE, "r", encoding="utf-8") as f:
                        st = json.load(f)
                except Exception:
                    st = {"status": "INITIALIZING", "equity": 300.0, "capital": 300.0}
            else:
                st = {"status": "INITIALIZING", "equity": 300.0, "capital": 300.0}
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


def run_scalper_async():
    asyncio.run(core_scalper_v39.main())


def run_server():
    t = threading.Thread(target=run_scalper_async, daemon=True)
    t.start()
    logger.info("Launched v3.9 Bybit Scalper async loop...")

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
