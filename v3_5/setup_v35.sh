#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   Установка Bybit Multi-Alpha Bot v3.5           "
echo "   (Alpha Pairs: Market-Neutral Long/Short)       "
echo "   (Порт 8088 // Beta ~ 0.00 & Hysteresis Buffer) "
echo "=================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
echo "Корневая директория проекта: $APP_DIR"
echo "Директория v3_5: $SCRIPT_DIR"

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
echo "[3/5] Проверка файла состояния торговли v3.5 Alpha Pairs..."
if [ ! -f "$SCRIPT_DIR/reports/BOT_STATE_PERSISTENT_V35.json" ]; then
    cat << 'EOF_STATE' > "$SCRIPT_DIR/reports/BOT_STATE_PERSISTENT_V35.json"
{
  "version": "3.5.0",
  "timeframe": "1h",
  "trading_halted": false,
  "bar_counter": 0,
  "initial_capital": "300.0",
  "realized_equity": "300.0",
  "peak_equity": "300.0",
  "max_drawdown_pct": "0.0",
  "open_positions": {},
  "closed_trades": []
}
EOF_STATE
    echo "Создан начальный профиль капитала v3.5 Alpha Pairs: \$300.00 USDT"
fi

# 4. Регистрация systemd службы bybit-bot-v35.service
echo "[4/5] Регистрация автозапуска systemd (bybit-bot-v35.service)..."
cat << EOF_SERVICE > /etc/systemd/system/bybit-bot-v35.service
[Unit]
Description=Bybit Multi-Alpha Market-Neutral Engine v3.5 Alpha Pairs (Port 8088)
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
systemctl enable bybit-bot-v35.service
systemctl restart bybit-bot-v35.service

# Открытие порта 8088 в фаерволе
if command -v ufw &> /dev/null; then
    ufw allow 8088/tcp > /dev/null 2>&1 || true
fi

# 5. Проверка запуска
echo "[5/5] Проверка запуска демона v3.5..."
sleep 3

if systemctl is-active --quiet bybit-bot-v35; then
    SERVER_IP=$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
    echo ""
    echo "=================================================="
    echo "✅ ВЕРСИЯ v3.5 (ALPHA PAIRS MARKET-NEUTRAL) ЗАПУЩЕНА!"
    echo "=================================================="
    echo ""
    echo "🌐 Панель мониторинга v3.5 (1h): http://${SERVER_IP}:8088"
    echo "📊 API статуса:                   http://${SERVER_IP}:8088/api/status"
    echo "🛰️ Квантовый радар силы:          http://${SERVER_IP}:8088/api/universe"
    echo "=================================================="
fi
