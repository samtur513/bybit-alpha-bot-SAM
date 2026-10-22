#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   Установка Bybit Multi-Alpha Bot v2.1 на VPS    "
echo "   (Параллельный A/B тест на порту 8081)          "
echo "=================================================="

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
echo "Корневая директория проекта: $APP_DIR"
echo "Директория v2.1: $SCRIPT_DIR"

# 1. Системные пакеты
echo "[1/5] Проверка системных пакетов..."
if command -v apt-get &> /dev/null; then
    apt-get update -qq
    apt-get install -y -qq python3-venv python3-pip curl ufw > /dev/null 2>&1 || true
fi

# 2. Виртуальное окружение Python (используем общее venv)
echo "[2/5] Проверка виртуального окружения Python..."
mkdir -p "$SCRIPT_DIR/reports"
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip -qq
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -qq

# 3. Инициализация чистого состояния если отсутствует
echo "[3/5] Проверка файла состояния торговли v2.1..."
if [ ! -f "$SCRIPT_DIR/reports/BOT_STATE_PERSISTENT_V21.json" ]; then
    cat << 'EOF_STATE' > "$SCRIPT_DIR/reports/BOT_STATE_PERSISTENT_V21.json"
{
  "version": "2.1",
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
    echo "Создан начальный профиль капитала v2.1: $300.00 USDT"
fi

# 4. Регистрация systemd службы bybit-bot-v21.service
echo "[4/5] Регистрация автозапуска systemd (bybit-bot-v21.service)..."
cat << EOF_SERVICE > /etc/systemd/system/bybit-bot-v21.service
[Unit]
Description=Bybit Multi-Alpha Quantitative Engine v2.1 (Port 8081)
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
systemctl enable bybit-bot-v21.service
systemctl restart bybit-bot-v21.service

# Открытие порта 8081 в фаерволе
if command -v ufw &> /dev/null; then
    ufw allow 8081/tcp > /dev/null 2>&1 || true
fi

# 5. Проверка запуска
echo "[5/5] Проверка запуска демона v2.1..."
sleep 3

if systemctl is-active --quiet bybit-bot-v21; then
    SERVER_IP=$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
    echo ""
    echo "=================================================="
    echo "✅ ВЕРСИЯ v2.1 УСПЕШНО ЗАПУЩЕНА ПАРАЛЛЕЛЬНО С v2.0!"
    echo "=================================================="
    echo ""
    echo "🌐 Панели мониторинга A/B теста:"
    echo "   • v2.0 (Базовая версия):    http://${SERVER_IP}:8080"
    echo "   • v2.1 (BTC Filter + MNT):  http://${SERVER_IP}:8081"
    echo ""
    echo "📊 Телеметрия v2.1:"
    echo "   • Статус JSON: http://${SERVER_IP}:8081/api/status"
    echo "   • Состояние:   http://${SERVER_IP}:8081/api/state"
    echo "   • Живые логи:  http://${SERVER_IP}:8081/api/logs"
    echo ""
    echo "Управление v2.1:"
    echo "   • Статус службы: systemctl status bybit-bot-v21"
    echo "   • Логи сервера:  journalctl -u bybit-bot-v21 -f"
    echo "   • Перезапуск:    systemctl restart bybit-bot-v21"
    echo "   • Сброс теста:   bash v2_1/reset_test.sh"
    echo "=================================================="
else
    echo "❌ Ошибка запуска v2.1. Проверьте логи: journalctl -u bybit-bot-v21 -n 50"
    exit 1
fi
