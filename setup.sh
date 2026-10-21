#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   Установка Bybit Multi-Alpha Bot v2.0 на VPS    "
echo "=================================================="

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Рабочая директория: $APP_DIR"

# 1. Системные пакеты
echo "[1/5] Проверка системных пакетов и утилит..."
if command -v apt-get &> /dev/null; then
    apt-get update -qq
    apt-get install -y -qq python3-venv python3-pip curl ufw > /dev/null 2>&1 || true
fi

# 2. Виртуальное окружение Python
echo "[2/5] Настройка виртуального окружения Python..."
mkdir -p "$APP_DIR/reports"
if [ ! -d "$APP_DIR/venv" ]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$APP_DIR/venv/bin/pip" install --upgrade pip -qq
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt" -qq

# 3. Инициализация чистого состояния если отсутствует
echo "[3/5] Проверка файла состояния торговли..."
if [ ! -f "$APP_DIR/reports/BOT_STATE_PERSISTENT.json" ]; then
    cat << 'EOF_STATE' > "$APP_DIR/reports/BOT_STATE_PERSISTENT.json"
{
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
}
EOF_STATE
    echo "Создан начальный профиль капитала: $300.00 USDT"
fi

# 4. Регистрация systemd службы
echo "[4/5] Регистрация автозапуска systemd (bybit-bot.service)..."
cat << EOF_SERVICE > /etc/systemd/system/bybit-bot.service
[Unit]
Description=Bybit Multi-Alpha Quantitative Engine v2.0
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$APP_DIR
ExecStart=$APP_DIR/venv/bin/python web_dashboard.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF_SERVICE

systemctl daemon-reload
systemctl enable bybit-bot.service
systemctl restart bybit-bot.service

# Открытие порта 8080 в фаерволе (если ufw активен)
if command -v ufw &> /dev/null; then
    ufw allow 8080/tcp > /dev/null 2>&1 || true
fi

# 5. Проверка запуска
echo "[5/5] Проверка запуска демона..."
sleep 3

if systemctl is-active --quiet bybit-bot; then
    SERVER_IP=$(curl -s -4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}')
    echo ""
    echo "=================================================="
    echo "✅ БОТ УСПЕШНО ЗАПУЩЕН И РАБОТАЕТ 24/7!"
    echo "=================================================="
    echo ""
    echo "🌐 Веб-дашборд доступен в браузере:"
    echo "   👉 http://${SERVER_IP}:8080"
    echo ""
    echo "📊 Телеметрия и мониторинг API:"
    echo "   • Статус JSON: http://${SERVER_IP}:8080/api/status"
    echo "   • Состояние:   http://${SERVER_IP}:8080/api/state"
    echo "   • Живые логи:  http://${SERVER_IP}:8080/api/logs"
    echo ""
    echo "Управление:"
    echo "   • Перезапуск:   systemctl restart bybit-bot"
    echo "   • Логи сервера: journalctl -u bybit-bot -f"
    echo "   • Сброс теста:  bash reset_test.sh"
    echo "=================================================="
else
    echo "❌ Ошибка запуска. Проверьте логи: journalctl -u bybit-bot -n 50"
    exit 1
fi
