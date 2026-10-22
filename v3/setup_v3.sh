#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   Установка Bybit Multi-Alpha Bot v3.0 на VPS    "
echo "   (Dynamic Universe + 4h Trend + News Shield)    "
echo "   (Порт 8082 для параллельного A/B/C теста)     "
echo "=================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
echo "Корневая директория проекта: $APP_DIR"
echo "Директория v3: $SCRIPT_DIR"

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
echo "[3/5] Проверка файла состояния торговли v3..."
if [ ! -f "$SCRIPT_DIR/reports/BOT_STATE_PERSISTENT_V3.json" ]; then
    cat << 'EOF_STATE' > "$SCRIPT_DIR/reports/BOT_STATE_PERSISTENT_V3.json"
{
  "version": "3.0",
  "initial_capital": "300.0",
  "realized_equity": "300.0",
  "peak_equity": "300.0",
  "max_drawdown_pct": "0.0",
  "bar_counter": 0,
  "cooldown_tracker": {},
  "open_positions": {},
  "closed_trades": [],
  "risk_manager": {
    "consecutive_losses": 0,
    "consecutive_wins": 0,
    "is_kill_switch_active": false
  }
}
EOF_STATE
    echo "Создан начальный профиль капитала v3: $300.00 USDT"
fi

# 4. Регистрация systemd службы bybit-bot-v3.service
echo "[4/5] Регистрация автозапуска systemd (bybit-bot-v3.service)..."
cat << EOF_SERVICE > /etc/systemd/system/bybit-bot-v3.service
[Unit]
Description=Bybit Multi-Alpha Dynamic Universe Engine v3.0 (Port 8082)
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
systemctl enable bybit-bot-v3.service
systemctl restart bybit-bot-v3.service

# Открытие порта 8082 в фаерволе
if command -v ufw &> /dev/null; then
    ufw allow 8082/tcp > /dev/null 2>&1 || true
fi

# 5. Проверка запуска
echo "[5/5] Проверка запуска демона v3..."
sleep 3

if systemctl is-active --quiet bybit-bot-v3; then
    SERVER_IP=$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
    echo ""
    echo "=================================================="
    echo "✅ ВЕРСИЯ v3.0 УСПЕШНО ЗАПУЩЕНА ПАРАЛЛЕЛЬНО (A/B/C)!"
    echo "=================================================="
    echo ""
    echo "🌐 Панели мониторинга:"
    echo "   • v2.0 (Базовая версия):             http://${SERVER_IP}:8080"
    echo "   • v2.1 (BTC Filter + News Shield):   http://${SERVER_IP}:8081"
    echo "   • v3.0 (Dynamic Universe + Shield):  http://${SERVER_IP}:8082"
    echo ""
    echo "📊 Телеметрия v3.0:"
    echo "   • Статус JSON:   http://${SERVER_IP}:8082/api/status"
    echo "   • Скринер монет: http://${SERVER_IP}:8082/api/universe"
    echo "   • Логи:          http://${SERVER_IP}:8082/api/logs"
    echo ""
    echo "🛡️ Webhook новостного щита:"
    echo "   curl -X POST http://${SERVER_IP}:8082/api/news_event -H 'Content-Type: application/json' -d '{\"headline\": \"SOLANA HACK\"}'"
    echo ""
else
    echo "❌ Ошибка запуска службы bybit-bot-v3. Проверьте логи: journalctl -u bybit-bot-v3 -n 50"
    exit 1
fi
