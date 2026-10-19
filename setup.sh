#!/usr/bin/env bash
set -e

echo "=================================================="
echo "   Установка Bybit Multi-Alpha Bot v2.0 на VPS    "
echo "=================================================="

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "Рабочая директория: $APP_DIR"

# 1. Системные пакеты
echo "[1/4] Проверка python3-venv..."
apt-get update -qq
apt-get install -y -qq python3-venv python3-pip curl > /dev/null

# 2. Виртуальное окружение Python
echo "[2/4] Настройка виртуального окружения Python..."
mkdir -p "$APP_DIR/reports"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --upgrade pip -qq
"$APP_DIR/venv/bin/pip" install pandas requests -qq

# 3. Регистрация systemd службы
echo "[3/4] Регистрация автозапуска systemd (bybit-bot.service)..."
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

# 4. Проверка
echo "[4/4] Проверка запуска..."
sleep 3

if systemctl is-active --quiet bybit-bot; then
    echo ""
    echo "=================================================="
    echo "✅ БОТ УСПЕШНО ЗАПУЩЕН И РАБОТАЕТ 24/7!"
    echo "=================================================="
    echo ""
    echo "🌐 Веб-дашборд доступен в браузеру:"
    echo "   👉 http://1533242.xorek.cloud:8080"
    echo "   👉 http://150.241.98.70:8080"
    echo "=================================================="
else
    echo "❌ Ошибка запуска. Проверьте логи: journalctl -u bybit-bot -n 50"
    exit 1
fi
