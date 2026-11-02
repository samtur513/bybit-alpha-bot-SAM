"""
Live Web Dashboard & Paper Trading Server v3.1 Pure Quant (Port 8081).
A/B Test Configuration:
- Exact same quantitative alpha engine as v3.1:
  * Dynamic Universe Selection (400+ Bybit Linear Perpetuals ranked by Composite Momentum Score)
  * 1-Hour Donchian Breakout (120-bar lookback / 5-day channel)
  * Auto-Gearbox Drawdown Risk Stepping (Gear 3, Gear 2, Gear 1)
  * Half-Kelly dynamic position sizing
  * Cascade Partial TP (TP1 +3.5 ATR, TP2 +5.5 ATR, Runner trailing) & Fast-Scratch exit
- NEWS CORE DEFENSIVE SHIELD: DISABLED (Pure Quantitative Benchmark)
- Dedicated Web Monitor on Port 8081
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

from v3.src.paper_trader_v3 import PaperTraderV3

PORT = 8081
REPORTS_DIR = BASE_DIR / "reports"
PERSISTENT_STATE_FILE = REPORTS_DIR / "BOT_STATE_PERSISTENT_V21.json"
LIVE_STATUS_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V21.json"
LIVE_MD_FILE = REPORTS_DIR / "PAPER_TRADING_LIVE_V21.md"

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

logger = logging.getLogger("WebDashboardV3PureQuant")
GLOBAL_TRADER: PaperTraderV3 = None

HTML_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Bybit Multi-Alpha v3.1 (Pure Quant) | Port 8081</title>
    <link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500;700&family=Inter:wght@400;600;700;800&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg-dark: #080c14;
            --bg-card: #0f172a;
            --bg-card-hover: #1e293b;
            --accent-cyan: #00f2fe;
            --accent-blue: #38bdf8;
            --accent-green: #10b981;
            --accent-red: #f43f5e;
            --accent-yellow: #f59e0b;
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
        .container { max-width: 1480px; margin: 0 auto; }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-bottom: 20px;
            border-bottom: 1px solid var(--border);
            margin-bottom: 24px;
        }
        .header-title h1 {
            font-size: 24px;
            font-weight: 800;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        .header-title .tag {
            font-size: 11px;
            background: linear-gradient(135deg, #475569, #334155);
            padding: 4px 10px;
            border-radius: 6px;
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }
        .header-title p {
            font-size: 13px;
            color: var(--text-muted);
            margin-top: 4px;
        }
        .header-meta {
            display: flex;
            gap: 10px;
            align-items: center;
        }
        .badge {
            padding: 6px 12px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 600;
            background: var(--bg-card);
            border: 1px solid var(--border);
        }
        .countdown-box {
            font-family: 'JetBrains Mono', monospace;
            font-size: 13px;
            color: var(--accent-cyan);
            background: rgba(0, 242, 254, 0.08);
            border: 1px solid rgba(0, 242, 254, 0.2);
            padding: 6px 12px;
            border-radius: 8px;
        }
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }
        .metric-card {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 16px 20px;
        }
        .metric-title {
            font-size: 12px;
            color: var(--text-muted);
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        .metric-value {
            font-size: 24px;
            font-weight: 800;
            font-family: 'JetBrains Mono', monospace;
            margin-top: 6px;
        }
        .val-green { color: var(--accent-green); }
        .val-red { color: var(--accent-red); }
        .val-cyan { color: var(--accent-cyan); }
        .val-yellow { color: var(--accent-yellow); }

        .two-col-grid {
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 24px;
        }
        @media (max-width: 1024px) {
            .two-col-grid { grid-template-columns: 1fr; }
        }
        .card-box {
            background: var(--bg-card);
            border: 1px solid var(--border);
            border-radius: 12px;
            padding: 20px;
        }
        .section-header {
            font-size: 15px;
            font-weight: 700;
            margin-bottom: 14px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .section-header .sub-header {
            font-size: 12px;
            color: var(--text-muted);
            font-weight: normal;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            margin-bottom: 24px;
        }
        th {
            text-align: left;
            padding: 10px 14px;
            background: rgba(15, 23, 42, 0.6);
            color: var(--text-muted);
            font-size: 11px;
            text-transform: uppercase;
            border-bottom: 1px solid var(--border);
        }
        td {
            padding: 12px 14px;
            border-bottom: 1px solid var(--border);
            font-family: 'JetBrains Mono', monospace;
            font-size: 12px;
        }
        tr:hover { background: var(--bg-card-hover); }
        .pos-badge {
            padding: 4px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 700;
        }
        .badge-long { background: rgba(16, 185, 129, 0.15); color: var(--accent-green); border: 1px solid rgba(16, 185, 129, 0.3); }
        .badge-short { background: rgba(244, 63, 94, 0.15); color: var(--accent-red); border: 1px solid rgba(244, 63, 94, 0.3); }
        .badge-cooldown { background: rgba(245, 158, 11, 0.15); color: var(--accent-yellow); font-size: 10px; padding: 2px 6px; border-radius: 4px; }
        .badge-pass { background: rgba(16, 185, 129, 0.2); color: var(--accent-green); padding: 2px 6px; border-radius: 4px; font-weight: 700; }
        .badge-rej { background: rgba(244, 63, 94, 0.2); color: var(--accent-red); padding: 2px 6px; border-radius: 4px; font-weight: 700; }

        .footer-note {
            margin-top: 30px;
            text-align: center;
            font-size: 12px;
            color: var(--text-muted);
        }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="header-title">
                <h1>Bybit Multi-Alpha v3.1 (Pure Quant) <span class="tag">Порт 8081</span></h1>
                <p>Параллельный A/B тест: 1h Dynamic Universe 400+ • Auto-Gearbox • Без Новостного Щита</p>
            </div>
            <div class="header-meta">
                <div class="badge" id="gearbox-badge">⚙️ Риск: Gear 3 (2.0%)</div>
                <div class="badge" style="background:#1e1e2d; border-color:#3b3b54; color:#94a3b8;">⚪ Новостной Щит: Отключён (Pure Quant)</div>
                <div class="badge" id="btc-filter-status">BTC Тренд: --</div>
                <div class="badge" id="universe-badge">🌐 Dynamic Universe: 25 монет</div>
                <div class="countdown-box">1H Бар: <span id="candle-timer">--</span></div>
            </div>
        </header>

        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-title">Капитал (USDT)</div>
                <div class="metric-value" id="metric-capital">$300.00</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Чистый PnL</div>
                <div class="metric-value" id="metric-pnl">$0.00</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Просадка (Max DD)</div>
                <div class="metric-value" id="metric-dd">0.00%</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Винрейт</div>
                <div class="metric-value" id="metric-wr">0.0%</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Открытых позиций</div>
                <div class="metric-value" id="metric-open-count">0 / 4</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Сделок завершено</div>
                <div class="metric-value" id="metric-trades-count">0</div>
            </div>
        </div>

        <!-- Open Positions -->
        <div class="section-header">
            <span>Открытые позиции (1h Trend Breakout + Cascade TP & Fast-Scratch)</span>
        </div>
        <table>
            <thead>
                <tr>
                    <th>Монета</th>
                    <th>Направление</th>
                    <th>Объём</th>
                    <th>Вход ($)</th>
                    <th>Текущая ($)</th>
                    <th>Стоп ($)</th>
                    <th>Unr. PnL ($)</th>
                    <th>Статус / TP</th>
                </tr>
            </thead>
            <tbody id="positions-body">
                <tr><td colspan="8" style="text-align:center; color: var(--text-muted);">Загрузка открытых позиций...</td></tr>
            </tbody>
        </table>

        <!-- Two Column: A/B Mode Info & Dynamic Screener -->
        <div class="two-col-grid">
            <div class="card-box">
                <div class="section-header">
                    <span>⚙️ Профиль A/B Тестирования (Порт 8081 vs 8082)</span>
                    <span class="sub-header">Чистый количественный режим</span>
                </div>
                <div style="background: rgba(15, 23, 42, 0.8); border: 1px solid var(--border); border-radius: 8px; padding: 16px; font-size: 13px; line-height: 1.6; color: var(--text-muted);">
                    <div style="color: var(--accent-cyan); font-weight: 700; margin-bottom: 8px;">🔬 Цель эксперимента:</div>
                    В данной версии отключён блок новостного защитного щита (News Core Shield) для прямого изолированного сравнения:
                    <ul style="margin: 10px 0 10px 20px;">
                        <li><b style="color:var(--text-main);">Порт 8081 (Здесь)</b>: Чистый 1h Donchian + Динамический Скринер (400+ монет) + Auto-Gearbox + Half-Kelly.</li>
                        <li><b style="color:var(--text-main);">Порт 8082</b>: Идентичная стратегия + Новостной Щит (Emergency Eject, Squeeze Escape, LiveNewsStreamer).</li>
                    </ul>
                    Позволяет точно измерить, сколько прибыли и просадки сохраняет Новостной Щит в реальном рынке.
                </div>
            </div>

            <div class="card-box">
                <div class="section-header">
                    <span>🌐 Динамический Скринер (RVOL & Delta OI)</span>
                    <span class="sub-header" id="screener-countdown">Переоценка: --</span>
                </div>
                <div style="font-size: 12px; color: var(--text-muted); margin-bottom: 10px;">
                    Оценка 400+ контрактов Bybit: Ликвидность, Волатильность, KER, RVOL, ΔOI.
                </div>
                <div id="screener-stats-body" style="max-height: 310px; overflow-y: auto;">
                    <table style="margin-bottom: 0;">
                        <thead>
                            <tr>
                                <th>Монета</th>
                                <th>Score</th>
                                <th>RVOL</th>
                                <th>ΔOI (%)</th>
                                <th>Оборот 24h</th>
                                <th>Статус</th>
                            </tr>
                        </thead>
                        <tbody id="screener-table-body">
                            <tr><td colspan="6" style="text-align:center; color: var(--text-muted);">Загрузка данных скринера...</td></tr>
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Universe Radar -->
        <div class="section-header">
            <span>Радар рынка активных монет (Top Bybit Linear Perpetuals)</span>
        </div>
        <div class="macro-banner" id="macro-banner" style="background: rgba(15,23,42,0.6); padding: 10px 16px; border: 1px solid var(--border); border-radius: 8px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; font-size: 12px;">
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
        <table>
            <thead>
                <tr>
                    <th>Монета</th>
                    <th>Текущая цена</th>
                    <th>До Long (Breakout)</th>
                    <th>До Short (Breakdown)</th>
                    <th>ATR (1h)</th>
                    <th>Объём к SMA</th>
                    <th>Тренд EMA</th>
                    <th>Статус</th>
                </tr>
            </thead>
            <tbody id="radar-body">
                <tr><td colspan="8" style="text-align:center; color: var(--text-muted);">Загрузка радара рынка...</td></tr>
            </tbody>
        </table>

        <div class="footer-note">
            Multi-Alpha Growth Engine v3.1 Pure Quant (Port 8081) • Auto-Gearbox • Cascade Take-Profit • Fast-Scratch • Dynamic Universe
        </div>
    </div>

    <script>
        async function fetchStatus() {
            try {
                const res = await fetch('/api/status');
                if (!res.ok) return;
                const data = await res.json();

                // Metrics
                const cap = Number(data.capital) || 300.0;
                document.getElementById('metric-capital').innerText = '$' + cap.toFixed(2);
                
                const pnl = Number(data.total_net_pnl) || 0.0;
                const pnlElem = document.getElementById('metric-pnl');
                pnlElem.innerText = (pnl >= 0 ? '+' : '') + '$' + pnl.toFixed(2);
                pnlElem.className = 'metric-value ' + (pnl >= 0 ? 'val-green' : 'val-red');

                document.getElementById('metric-dd').innerText = (Number(data.drawdown_pct) || 0.0).toFixed(2) + '%';
                document.getElementById('metric-wr').innerText = (Number(data.win_rate_pct) || 0.0).toFixed(1) + '%';
                document.getElementById('metric-open-count').innerText = (data.open_positions_count || 0) + ' / 4';
                document.getElementById('metric-trades-count').innerText = data.closed_trades_count || 0;

                // Meta Badges
                const gear = data.gearbox_gear || 3;
                const riskPct = data.gearbox_risk_pct || 2.0;
                document.getElementById('gearbox-badge').innerText = `⚙️ Риск: Gear ${gear} (${riskPct.toFixed(1)}%)`;

                const btcStatus = data.btc_trend_status || 'NEUTRAL';
                const btcBadge = document.getElementById('btc-filter-status');
                btcBadge.innerText = 'BTC Тренд: ' + btcStatus;
                if (btcStatus === 'BULLISH') btcBadge.style.color = 'var(--accent-green)';
                else if (btcStatus === 'BEARISH') btcBadge.style.color = 'var(--accent-red)';
                else btcBadge.style.color = 'var(--accent-yellow)';

                if (data.monitored_symbols_count) {
                    document.getElementById('universe-badge').innerText = '🌐 Dynamic Universe: ' + data.monitored_symbols_count + ' монет';
                }

                const sec = data.next_candle_seconds || 0;
                const h = Math.floor(sec / 3600);
                const m = Math.floor((sec % 3600) / 60);
                const s = sec % 60;
                document.getElementById('candle-timer').innerText = `${h}ч ${m}м ${s < 10 ? '0' : ''}${s}с`;

                if (data.screener_next_run_seconds !== undefined) {
                    const scSec = data.screener_next_run_seconds;
                    const scH = Math.floor(scSec / 3600);
                    const scM = Math.floor((scSec % 3600) / 60);
                    document.getElementById('screener-countdown').innerText = `Следующая переоценка: ${scH}ч ${scM}м`;
                }

                // Open Positions
                const pBody = document.getElementById('positions-body');
                const posKeys = Object.keys(data.open_positions || {});
                if (posKeys.length === 0) {
                    pBody.innerHTML = '<tr><td colspan="8" style="text-align:center; color: var(--text-muted);">Нет открытых позиций. Динамический сканер активен.</td></tr>';
                } else {
                    let html = '';
                    for (const sym of posKeys) {
                        const p = data.open_positions[sym];
                        const unr = Number(p.unrealized_pnl);
                        const unrClass = unr >= 0 ? 'val-green' : 'val-red';
                        const badgeClass = p.direction === 'LONG' ? 'badge-long' : 'badge-short';
                        html += `<tr>
                            <td style="font-weight:700;">${sym}</td>
                            <td><span class="pos-badge ${badgeClass}">${p.direction}</span></td>
                            <td>${p.qty}</td>
                            <td>$${Number(p.entry_price).toFixed(4)}</td>
                            <td>$${Number(p.current_price).toFixed(4)}</td>
                            <td>$${Number(p.current_stop).toFixed(4)}</td>
                            <td class="${unrClass}" style="font-weight:700;">${unr >= 0 ? '+' : ''}$${unr.toFixed(2)}</td>
                            <td>${p.is_pyramided ? '🚀 Пирамидинг (+50%)' : (p.is_breakeven_set ? '🛡️ Безубыток установлен' : 'Ожидание импульса')}</td>
                        </tr>`;
                    }
                    pBody.innerHTML = html;
                }

                // Screener Top Candidates Table
                const scBody = document.getElementById('screener-table-body');
                const candList = data.screened_candidates || [];
                if (candList.length > 0) {
                    let scHtml = '';
                    for (const c of candList.slice(0, 10)) {
                        const passBadge = c.passed_filters 
                            ? '<span class="badge-pass">PASS</span>' 
                            : `<span class="badge-rej" title="${c.rejection_reason || ''}">REJ</span>`;
                        scHtml += `<tr>
                            <td style="font-weight:700;">${c.symbol}</td>
                            <td style="font-family:'JetBrains Mono';">${c.composite_score}</td>
                            <td style="color:${c.rvol >= 1.2 ? 'var(--accent-cyan)' : 'inherit'}">${c.rvol || 1.0}x</td>
                            <td style="color:${c.delta_oi_pct > 0 ? 'var(--accent-green)' : 'inherit'}">${c.delta_oi_pct > 0 ? '+' : ''}${c.delta_oi_pct || 0}%</td>
                            <td>$${(c.turnover_24h / 1e6).toFixed(1)}M</td>
                            <td>${passBadge}</td>
                        </tr>`;
                    }
                    scBody.innerHTML = scHtml;
                }

                // Macro BTC Banner & Countdown
                const btcStatusEl = document.getElementById('banner-btc-status');
                const btcTrend = data.btc_trend_status || 'NEUTRAL';
                if (btcStatusEl) {
                    if (btcTrend === 'BEARISH') {
                        btcStatusEl.innerHTML = '<span style="color:var(--accent-red);">🔴 BEARISH (Защита: Long-пробои альтов заморожены)</span>';
                    } else if (btcTrend === 'BULLISH') {
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

                // Market Radar
                const rBody = document.getElementById('radar-body');
                const rKeys = Object.keys(data.radar || {});
                if (rKeys.length > 0) {
                    let html = '';
                    for (const sym of rKeys) {
                        const r = data.radar[sym];
                        const coolBadge = r.is_cooldown ? ' <span class="badge-cooldown">Cooldown 3b</span>' : '';
                        
                        let stBadge;
                        if (r.is_in_position) {
                            stBadge = '<span class="pos-badge badge-long">В позиции</span>';
                        } else if (r.is_cooldown) {
                            stBadge = coolBadge;
                        } else if (r.dist_to_long_pct === 0) {
                            if (btcTrend === 'BEARISH' && sym !== 'BTCUSDT') {
                                stBadge = '<span style="background:rgba(244,63,94,0.15); color:var(--accent-red); border:1px solid rgba(244,63,94,0.3); padding:3px 8px; border-radius:4px; font-size:11px; font-weight:700;" title="BTC падает (медвежий тренд). Защитный фильтр блокирует покупки альткоинов, защищая от ловушек">🛑 БЛОК: BTC BEAR</span>';
                            } else {
                                const mins = Math.floor((data.next_candle_seconds || 0) / 60);
                                stBadge = `<span style="background:rgba(245,158,11,0.15); color:var(--accent-yellow); border:1px solid rgba(245,158,11,0.3); padding:3px 8px; border-radius:4px; font-size:11px; font-weight:700;" title="Верхний уровень пробит внутри часа. Ожидание закрытия 1h свечи для входа в LONG через ${mins} мин">⏳ ЖДЁТ ЗАКРЫТИЯ 1H (${mins}м)</span>`;
                            }
                        } else if (r.dist_to_short_pct === 0) {
                            if (btcTrend === 'BULLISH' && sym !== 'BTCUSDT') {
                                stBadge = '<span style="background:rgba(244,63,94,0.15); color:var(--accent-red); border:1px solid rgba(244,63,94,0.3); padding:3px 8px; border-radius:4px; font-size:11px; font-weight:700;" title="BTC растет (бычий тренд). Защитный фильтр блокирует шорты альткоинов">🛑 БЛОК: BTC BULL</span>';
                            } else {
                                const mins = Math.floor((data.next_candle_seconds || 0) / 60);
                                stBadge = `<span style="background:rgba(245,158,11,0.15); color:var(--accent-yellow); border:1px solid rgba(245,158,11,0.3); padding:3px 8px; border-radius:4px; font-size:11px; font-weight:700;" title="Нижняя граница Дончяна пробита внутри часа. Ожидание закрытия 1h свечи для входа в SHORT через ${mins} мин">⏳ ЖДЁТ ЗАКРЫТИЯ 1H (SHORT, ${mins}м)</span>`;
                            }
                        } else if (r.dist_to_long_pct <= 1.0 || r.dist_to_short_pct <= 1.0) {
                            stBadge = '<span style="background:rgba(56,189,248,0.15); color:var(--accent-blue); border:1px solid rgba(56,189,248,0.3); padding:3px 8px; border-radius:4px; font-size:11px; font-weight:700;">🔥 ИМПУЛЬС (≤1%)</span>';
                        } else {
                            stBadge = '<span style="color:var(--text-muted);">Сканирование</span>';
                        }

                        const longTitle = r.dist_to_long_pct === 0 ? 'title="Уровень Дончяна коснулся/пробит внутри часа. Решения принимаются строго по закрытию 1h бара!"' : '';

                        html += `<tr>
                            <td style="font-weight:700;">${sym}</td>
                            <td>$${Number(r.current_price).toFixed(4)}</td>
                            <td style="color:${r.dist_to_long_pct < 2 ? 'var(--accent-green)' : 'inherit'}; font-weight:${r.dist_to_long_pct < 2 ? '700' : 'normal'}" ${longTitle}>${r.dist_to_long_pct}% ${r.dist_to_long_pct===0?'⏳':''}</td>
                            <td style="color:${r.dist_to_short_pct < 2 ? 'var(--accent-red)' : 'inherit'}; font-weight:${r.dist_to_short_pct < 2 ? '700' : 'normal'}">${r.dist_to_short_pct}%</td>
                            <td>${r.atr}</td>
                            <td style="color:${r.volume_ratio >= 1.2 ? 'var(--accent-cyan)' : 'inherit'}">${r.volume_ratio}x</td>
                            <td style="font-size:11px; color:var(--text-muted);">${r.trend}</td>
                            <td>${stBadge}</td>
                        </tr>`;
                    }
                    rBody.innerHTML = html;
                }
            } catch (err) {
                console.error("Failed to fetch status:", err);
            }
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
            if GLOBAL_TRADER and getattr(GLOBAL_TRADER, "last_status_cache", None):
                payload = json.dumps(GLOBAL_TRADER.last_status_cache, indent=2).encode("utf-8")
            elif LIVE_STATUS_FILE.exists():
                try:
                    payload = LIVE_STATUS_FILE.read_bytes()
                except Exception:
                    payload = b"{}"
            else:
                payload = b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif parsed.path == "/api/state":
            payload = PERSISTENT_STATE_FILE.read_bytes() if PERSISTENT_STATE_FILE.exists() else b"{}"
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        elif parsed.path == "/api/universe":
            universe_data = []
            if GLOBAL_TRADER and getattr(GLOBAL_TRADER, "screened_universe", None):
                universe_data = [
                    {
                        "rank": idx,
                        "symbol": a.symbol,
                        "score": round(a.composite_score, 4),
                        "passed": a.passed_filters,
                        "rejection_reason": a.rejection_reason,
                        "turnover_24h": round(a.turnover_24h, 2),
                        "open_interest": round(a.open_interest, 2),
                        "norm_atr": round(a.norm_atr, 4),
                        "rvol": round(getattr(a, "rvol", 1.0), 2),
                        "delta_oi_pct": round(getattr(a, "delta_oi_pct", 0.0) * 100, 2),
                        "adx": round(a.adx, 2),
                        "rel_strength": round(a.relative_strength, 4),
                        "funding_rate": round(a.funding_rate, 6),
                    }
                    for idx, a in enumerate(GLOBAL_TRADER.screened_universe, 1)
                ]
            payload = json.dumps(universe_data, indent=2).encode("utf-8")
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

    def log_message(self, format, *args):
        return


def start_server_and_bot():
    global GLOBAL_TRADER
    logger.info("Initializing PaperTraderV3 (Pure Quant Mode - No News Defense) on Port 8081...")
    GLOBAL_TRADER = PaperTraderV3(
        enable_news_defense=False,
        state_file=PERSISTENT_STATE_FILE,
        live_status_file=LIVE_STATUS_FILE,
        live_md_file=LIVE_MD_FILE,
        poll_interval_seconds=5,
    )

    bot_thread = threading.Thread(target=GLOBAL_TRADER.run_daemon, daemon=True)
    bot_thread.start()
    logger.info("Background PaperTraderV3 thread launched.")

    server_address = ("0.0.0.0", PORT)
    with socketserver.ThreadingTCPServer(server_address, DashboardHTTPRequestHandler) as httpd:
        httpd.allow_reuse_address = True
        logger.info(f"Multi-Alpha v3.1 Pure Quant Dashboard live on http://127.0.0.1:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            logger.info("Server interrupted. Shutting down...")
            GLOBAL_TRADER.stop()
            httpd.server_close()


if __name__ == "__main__":
    start_server_and_bot()
