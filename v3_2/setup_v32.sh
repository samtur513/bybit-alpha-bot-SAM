#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   Установка Bybit Multi-Alpha Bot v3.2 на VPS    "
echo "   (Dual-Regime Hybrid: Trend + Range Maker)      "
echo "   (Порт 8083 для параллельного A/B/C/D теста)   "
echo "=================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
echo "Корневая директория проекта: $APP_DIR"
echo "Директория v3_2: $SCRIPT_DIR"

# 1. Системные пакеты
echo "[1/5] Проверка системных пакетов..."
if command -v apt-get &> /dev/null; then
    apt-get update -qq
    apt-get install -y -qq python3-venv python3-pip curl ufw > /dev/null 2>&1 || true
fi

# 2. Виртуальное окружение Python
echo "[2/5] Проверка виртуального окружения Python..."
mkdir -p "$SCRIPT_DIR/reports"
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip -qq
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -qq

# 3. Инициализация чистого состояния если отсутствует
echo "[3/5] Проверка файла состояния торговли v3.2..."
if [ ! -f "$SCRIPT_DIR/reports/BOT_STATE_PERSISTENT_V32.json" ]; then
    cat << 'EOF_STATE' > "$SCRIPT_DIR/reports/BOT_STATE_PERSISTENT_V32.json"
{
  "version": "3.2",
  "schema_version": 5,
  "mode": "DUAL_REGIME_HYBRID",
  "initial_capital": "300.0",
  "trend_engine": {
    "version": "3.1",
    "schema_version": 4,
    "trading_halted": false,
    "last_bar_timestamp": 0,
    "bar_counter": 0,
    "cooldown_tracker": {},
    "funding_receipts": {},
    "initial_capital": "300.0",
    "realized_equity": "300.0",
    "peak_equity": "300.0",
    "max_drawdown_pct": "0.0",
    "open_positions": {},
    "closed_trades": [],
    "risk_manager": {
      "consecutive_losses": 0,
      "consecutive_wins": 0,
      "is_kill_switch_active": false
    }
  },
  "range_engine": {
    "initial_capital": "300.0",
    "realized_equity": "300.0",
    "peak_equity": "300.0",
    "max_drawdown_pct": "0.0",
    "bar_counter": 0,
    "cooldown_tracker": {},
    "open_positions": {},
    "pending_orders": {},
    "closed_trades": []
  },
  "runtime": {
    "last_processed_bar_ts": {}
  }
}
EOF_STATE
    echo "Создан начальный профиль капитала v3.2: \$300.00 USDT"
fi

# 4. Регистрация systemd службы bybit-bot-v32.service
echo "[4/5] Регистрация автозапуска systemd (bybit-bot-v32.service)..."
cat << EOF_SERVICE > /etc/systemd/system/bybit-bot-v32.service
[Unit]
Description=Bybit Multi-Alpha Dual-Regime Engine v3.2 (Port 8083)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python $SCRIPT_DIR/web_dashboard.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$APP_DIR

[Install]
WantedBy=multi-user.target
EOF_SERVICE

systemctl daemon-reload
systemctl enable bybit-bot-v32.service
systemctl restart bybit-bot-v32.service

# Открытие порта 8083 в фаерволе
if command -v ufw &> /dev/null; then
    ufw allow 8083/tcp > /dev/null 2>&1 || true
fi

# 5. Проверка запуска
echo "[5/5] Проверка запуска демона v3.2..."
sleep 3

if systemctl is-active --quiet bybit-bot-v32; then
    SERVER_IP=$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
    echo ""
    echo "=================================================="
    echo "✅ ВЕРСИЯ v3.2 DUAL-REGIME УСПЕШНО ЗАПУЩЕНА!"
    echo "=================================================="
    echo ""
    echo "🌐 Панель мониторинга v3.2: http://${SERVER_IP}:8083"
    echo "📊 API статуса:             http://${SERVER_IP}:8083/api/status"
    echo "🪙 Скринер монет:           http://${SERVER_IP}:8083/api/universe"
    echo "=================================================="
fi
