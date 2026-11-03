#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   Установка Bybit Multi-Alpha Bot v3.4 ZF-8      "
echo "   (8-Speed Intelligent Quant Transmission)      "
echo "   (Порт 8087 // Smart Kickdown & ABS Braking)   "
echo "=================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
echo "Корневая директория проекта: $APP_DIR"
echo "Директория v3_4: $SCRIPT_DIR"

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
echo "[3/5] Проверка файла состояния торговли v3.4 ZF-8..."
if [ ! -f "$SCRIPT_DIR/reports/BOT_STATE_PERSISTENT_V34.json" ]; then
    cat << 'EOF_STATE' > "$SCRIPT_DIR/reports/BOT_STATE_PERSISTENT_V34.json"
{
  "version": "3.4.0",
  "timeframe": "1h",
  "schema_version": 6,
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
  "transmission": {
    "current_gear": 1,
    "streak_wins": 0,
    "streak_losses": 0,
    "total_trades_processed": 0,
    "is_circuit_breaker_active": false,
    "last_shift_reason": "INITIAL_DEPLOYMENT"
  },
  "runtime": {
    "last_processed_bar_ts": {}
  }
}
EOF_STATE
    echo "Создан начальный профиль капитала v3.4 ZF-8: \$300.00 USDT"
fi

# 4. Регистрация systemd службы bybit-bot-v34.service
echo "[4/5] Регистрация автозапуска systemd (bybit-bot-v34.service)..."
cat << EOF_SERVICE > /etc/systemd/system/bybit-bot-v34.service
[Unit]
Description=Bybit Multi-Alpha ZF-8 Quant Transmission Engine v3.4 1h (Port 8087)
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
systemctl enable bybit-bot-v34.service
systemctl restart bybit-bot-v34.service

# Открытие порта 8087 в фаерволе
if command -v ufw &> /dev/null; then
    ufw allow 8087/tcp > /dev/null 2>&1 || true
fi

# 5. Проверка запуска
echo "[5/5] Проверка запуска демона v3.4..."
sleep 3

if systemctl is-active --quiet bybit-bot-v34; then
    SERVER_IP=$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
    echo ""
    echo "=================================================="
    echo "✅ ВЕРСИЯ v3.4 (ZF-8 QUANT TRANSMISSION) ЗАПУЩЕНА!"
    echo "=================================================="
    echo ""
    echo "🌐 Панель мониторинга v3.4 (1h): http://${SERVER_IP}:8087"
    echo "📊 API статуса:                   http://${SERVER_IP}:8087/api/status"
    echo "🪙 Скринер монет:                 http://${SERVER_IP}:8087/api/universe"
    echo "=================================================="
fi
